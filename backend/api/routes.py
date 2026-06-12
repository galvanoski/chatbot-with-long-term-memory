import datetime as dt
import html as html_mod
import re
import asyncio
import json
import logging
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from backend.api.schemas import (
    ApprovalRequest,
    BrandRuleResponse,
    BrandRuleSaveRequest,
    BrandRuleSaveResponse,
    DeleteThreadResponse,
    DeleteMemoryResponse,
    ImageGenerationRequest,
    ImagePromptRequest,
    SEORequest,
    SocialPostRequest,
    MemoryItem,
    MemoryListResponse,
    MessageSendRequest,
    ProductCatalogBulkLoadRequest,
    ProductCatalogBulkLoadResponse,
    RegenerateRequest,
    ThreadCreateRequest,
    ThreadDetailResponse,
    ThreadListItemResponse,
    ThreadStateResponse,
    ThreadActionResponse,
)
from backend.graph.tools.rag import load_products_to_catalog
from backend.memory.manager import MemoryManager
from backend.middleware.geekcat import GeekCatMiddleware, _request_analytics
from backend.graph.nodes.seo import SEO_SYSTEM_PROMPT
from backend.tracking.rag_trace import get_rag_trace
from backend.tracking.usage import UsageTracker, extract_usage_from_llm_output

logger = logging.getLogger("geekcat.api")

# Global graph instance — set in main.py
_graph: CompiledStateGraph | None = None
_middleware: GeekCatMiddleware | None = None
_memory: MemoryManager | None = None

# In-memory thread store (keyed by thread_id)
_threads: dict[str, dict] = {}
_threads_lock = threading.Lock()
_thread_db_path = Path(__file__).resolve().parents[2] / "threads.db"
_thread_db_conn: sqlite3.Connection | None = None
_thread_db_lock = threading.Lock()
_ANON_USER_ID_PATTERN = re.compile(
    r"^anon_[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

def _set_thread_db_path(path: Path) -> None:
    global _thread_db_path, _thread_db_conn
    with _thread_db_lock:
        if _thread_db_conn is not None:
            _thread_db_conn.close()
            _thread_db_conn = None
        _thread_db_path = path


def _get_thread_db_conn() -> sqlite3.Connection:
    global _thread_db_conn
    with _thread_db_lock:
        if _thread_db_conn is None:
            _thread_db_path.parent.mkdir(parents=True, exist_ok=True)
            _thread_db_conn = sqlite3.connect(_thread_db_path, check_same_thread=False)
            _thread_db_conn.row_factory = sqlite3.Row
            _thread_db_conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_threads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    messages_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            _thread_db_conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chat_threads_fts
                USING fts5(thread_id UNINDEXED, user_id UNINDEXED, title, content)
                """
            )
            existing_columns = {
                row[1]
                for row in _thread_db_conn.execute("PRAGMA table_info(chat_threads)").fetchall()
            }
            if "messages_json" not in existing_columns:
                _thread_db_conn.execute(
                    "ALTER TABLE chat_threads ADD COLUMN messages_json TEXT NOT NULL DEFAULT '[]'"
                )
            _thread_db_conn.execute("DELETE FROM chat_threads_fts")
            _thread_db_conn.execute(
                """
                INSERT INTO chat_threads_fts(thread_id, user_id, title, content)
                SELECT id, user_id, COALESCE(title, ''), COALESCE(messages_json, '[]')
                FROM chat_threads
                """
            )
            _thread_db_conn.commit()
        return _thread_db_conn


def _serialize_messages(messages: list[dict] | None) -> str:
    return json.dumps(messages or [], ensure_ascii=False)


def _messages_to_fts_text(messages: list[dict] | None) -> str:
    if not messages:
        return ""
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").strip()
        if content:
            chunks.append(content)
    return "\n".join(chunks)


def _to_fts_query(text: str) -> str:
    tokens = [token for token in re.split(r"\s+", text.strip()) if token]
    if not tokens:
        return ""
    safe_tokens = [re.sub(r'[^\w\-]', '', token) for token in tokens]
    safe_tokens = [token for token in safe_tokens if token]
    return " AND ".join(f'"{token}"*' for token in safe_tokens)


def _deserialize_messages(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _thread_cache_get(thread_id: str) -> dict | None:
    with _threads_lock:
        thread = _threads.get(thread_id)
        return dict(thread) if thread else None


def _thread_cache_set(thread: dict) -> dict:
    with _threads_lock:
        _threads[thread["id"]] = dict(thread)
        return _threads[thread["id"]]


def _thread_cache_update(thread_id: str, updates: dict) -> dict:
    with _threads_lock:
        thread = dict(_threads.get(thread_id, {}))
        if not thread:
            # Fall back to DB to preserve user_id and other fields
            db_thread = _load_thread_record(thread_id)
            if db_thread:
                thread = db_thread
            else:
                thread = {"id": thread_id}
        thread.update(updates)
        _threads[thread_id] = thread
        return thread


def _save_thread_record(thread: dict) -> None:
    conn = _get_thread_db_conn()
    messages = thread.get("messages", [])
    conn.execute(
        """
        INSERT INTO chat_threads (id, user_id, title, status, created_at, updated_at, messages_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            user_id=excluded.user_id,
            title=excluded.title,
            status=excluded.status,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at,
            messages_json=excluded.messages_json
        """,
        (
            thread["id"],
            thread["user_id"],
            thread.get("title"),
            thread.get("status", "active"),
            thread["created_at"],
            thread["updated_at"],
            _serialize_messages(messages),
        ),
    )
    conn.execute("DELETE FROM chat_threads_fts WHERE thread_id = ?", (thread["id"],))
    conn.execute(
        """
        INSERT INTO chat_threads_fts(thread_id, user_id, title, content)
        VALUES (?, ?, ?, ?)
        """,
        (
            thread["id"],
            thread["user_id"],
            str(thread.get("title") or ""),
            _messages_to_fts_text(messages),
        ),
    )
    conn.commit()


def _load_thread_record(thread_id: str, user_id: str | None = None) -> dict | None:
    conn = _get_thread_db_conn()
    if user_id:
        row = conn.execute(
            "SELECT id, user_id, title, status, created_at, updated_at, messages_json FROM chat_threads WHERE id = ? AND user_id = ?",
            (thread_id, user_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, user_id, title, status, created_at, updated_at, messages_json FROM chat_threads WHERE id = ?",
            (thread_id,),
        ).fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "messages": _deserialize_messages(row["messages_json"]),
    }


def _list_thread_records(user_id: str, query: str | None = None) -> list[dict]:
    conn = _get_thread_db_conn()
    q = (query or "").strip()
    if q:
        fts_query = _to_fts_query(q)
        if not fts_query:
            return []
        rows = conn.execute(
            """
            SELECT ct.id, ct.user_id, ct.title, ct.status, ct.created_at, ct.updated_at, ct.messages_json
            FROM chat_threads AS ct
            JOIN chat_threads_fts AS fts ON fts.thread_id = ct.id
            WHERE ct.user_id = ? AND fts.user_id = ? AND chat_threads_fts MATCH ?
            ORDER BY bm25(chat_threads_fts), ct.updated_at DESC
            """,
            (user_id, user_id, fts_query),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, user_id, title, status, created_at, updated_at, messages_json
            FROM chat_threads
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "messages": _deserialize_messages(row["messages_json"]),
        }
        for row in rows
    ]


def _migrate_single_owner_threads_to_user_id(user_id: str) -> bool:
    if not _ANON_USER_ID_PATTERN.match(user_id):
        return False

    conn = _get_thread_db_conn()
    owners = [row[0] for row in conn.execute("SELECT DISTINCT user_id FROM chat_threads").fetchall()]
    if len(owners) != 1 or owners[0] == user_id:
        return False

    source_user_id = owners[0]
    conn.execute(
        "UPDATE chat_threads SET user_id = ? WHERE user_id = ?",
        (user_id, source_user_id),
    )
    conn.execute(
        "UPDATE chat_threads_fts SET user_id = ? WHERE user_id = ?",
        (user_id, source_user_id),
    )
    conn.commit()

    with _threads_lock:
        for thread_id, thread in list(_threads.items()):
            if thread.get("user_id") == source_user_id:
                updated_thread = dict(thread)
                updated_thread["user_id"] = user_id
                _threads[thread_id] = updated_thread

    logger.info("migrated chat threads from %s to %s", source_user_id, user_id)
    return True


def _delete_thread_record(thread_id: str, user_id: str) -> bool:
    conn = _get_thread_db_conn()
    row = conn.execute(
        "SELECT id FROM chat_threads WHERE id = ? AND user_id = ?",
        (thread_id, user_id),
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM chat_threads WHERE id = ? AND user_id = ?", (thread_id, user_id))
    conn.execute("DELETE FROM chat_threads_fts WHERE thread_id = ? AND user_id = ?", (thread_id, user_id))
    conn.commit()
    with _threads_lock:
        _threads.pop(thread_id, None)
    return True


def _encode_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_chunks(text: str, chunk_size: int = 32) -> list[str]:
    content = (text or "")
    if not content:
        return []
    return [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]


def _latest_assistant_text(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if str(message.get("role") or "") != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def _html_to_text(html_str: str) -> str:
    """Convert HTML to clean plain text."""
    from html.parser import HTMLParser

    class _HTMLStripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._result: list[str] = []
            self._skip = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag_lower = tag.lower()
            if tag_lower in ("script", "style"):
                self._skip = True
            if tag_lower in ("p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                self._result.append("\n")
            if tag_lower == "td":
                self._result.append(" ")

        def handle_endtag(self, tag: str) -> None:
            tag_lower = tag.lower()
            if tag_lower in ("script", "style"):
                self._skip = False
            if tag_lower in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "div"):
                self._result.append("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self._result.append(data)

        def handle_entityref(self, name: str) -> None:
            if not self._skip:
                self._result.append(f"&{name};")

        def handle_charref(self, name: str) -> None:
            if not self._skip:
                self._result.append(f"&#{name};")

    stripper = _HTMLStripper()
    stripper.feed(html_str)
    text = "".join(stripper._result)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"\n +", "\n", text)
    return text.strip()


def _extract_urls(text: str) -> list[str]:
    """Extract all URLs from a text string."""
    url_pattern = re.compile(
        r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+"
        r"(?:/[-\w$.+!*'(),;:@&=?/~#%]*)?"
    )
    return list(set(url_pattern.findall(text)))


def _fetch_url_text(url: str, timeout: int = 10) -> str | None:
    """Fetch a URL and extract readable text content."""
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GeekCatBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return None
            raw = resp.read().decode("utf-8", errors="replace")
            text = _html_to_text(raw)
            return text[:5000] if text else None
    except Exception:
        return None


def _md_headings_to_html(text: str) -> str:
    """Convert markdown headings (# ## ###) to HTML tags in a text block.
    The first heading becomes H1 regardless of level; subsequent headings
    map to their standard HTML level (## → H2, ### → H3)."""
    lines = text.split("\n")
    result: list[str] = []
    h1_done = False
    for line in lines:
        stripped = line.strip()
        m = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if m:
            level = len(m.group(1))
            content = m.group(2).strip()
            if not h1_done:
                result.append(f"<h1>{content}</h1>")
                h1_done = True
            else:
                result.append(f"<h{level}>{content}</h{level}>")
        else:
            result.append(line)
    return "\n".join(result)


async def _sse_send_message(thread_id: str, body: MessageSendRequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }

        initial_state = _middleware.before_agent(
            {
                "messages": [HumanMessage(content=body.content)],
                "user_id": body.user_id,
                "thread_id": thread_id,
            },
            config,
        )

        # Detect URLs in the user message and fetch content for graph context
        urls = _extract_urls(body.content)
        url_texts: list[str] = list(initial_state.get("product_context") or [])
        for url in urls:
            url_text = _fetch_url_text(url)
            if url_text:
                url_texts.append(f"[URL] {url}\n{url_text}")
        if url_texts:
            initial_state["product_context"] = url_texts

        # Load thread early so we can validate context
        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id, "user_id": body.user_id, "title": None,
            "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(), "status": "active", "messages": [],
        }
        thread = _thread_cache_set(thread)

        # Validate relevance of user message + fetched URL content
        combined = body.content + "\n" + "\n".join(url_texts)
        if not _is_geekcat_related(combined):
            lang = _detect_language(combined)
            yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})
            yield _encode_sse("delta", {"text": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"])})
            now = _utc_now_iso()
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.content, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"]), "created_at": now}
            thread["messages"] = list(thread.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        # Detect if user is asking to create content (post, SEO, image) without product context
        if _is_creation_request(body.content) and not _has_product_context(thread, body.content) and not url_texts:
            lang = _detect_language(body.content)
            yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})
            yield _encode_sse("delta", {"text": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"])})
            now = _utc_now_iso()
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.content, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"]), "created_at": now}
            thread["messages"] = list(thread.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})

        usage_tracker = UsageTracker()
        stream_chunks: list[Any] = []

        async for event in _graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            metadata = event.get("metadata") or {}
            node_name = str(metadata.get("langgraph_node") or "")

            if kind == "on_chat_model_stream" and name == "ChatOpenAI":
                # Stream only the copywriter model output when node metadata is available.
                if node_name and node_name != "copywriter":
                    continue
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content"):
                    stream_chunks.append(chunk)
                    token = chunk.content
                    if isinstance(token, str) and token:
                        yield _encode_sse("delta", {"text": token})

            if kind == "on_llm_end" and name == "ChatOpenAI":
                output = event.get("data", {}).get("output")
                if output:
                    usage = extract_usage_from_llm_output(output)
                    if usage:
                        model = getattr(output, "response_metadata", {}).get("model", "") or ""
                        usage_tracker.add(
                            model=model,
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                        )

        # Fallback: if no usage was tracked via on_llm_end, try accumulated chunks
        if not usage_tracker._calls and stream_chunks:
            from backend.tracking.usage import extract_usage_from_chunks, UsageInfo
            chunk_usage = extract_usage_from_chunks(stream_chunks)
            if chunk_usage:
                model_name = "openai/gpt-5-mini"
                u = UsageInfo(model=model_name, input_tokens=chunk_usage.get("input_tokens", 0), output_tokens=chunk_usage.get("output_tokens", 0))
                usage_tracker.add(model=model_name, input_tokens=u.input_tokens, output_tokens=u.output_tokens)

        state_snapshot = await _graph.aget_state(config)
        snap_values = state_snapshot.values if state_snapshot else {}

        # Capture analytics from context var before after_agent clears it
        analytics_log: list[dict] = _request_analytics.get() or []

        _middleware.after_agent(snap_values, config)

        existing_thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id,
            "user_id": body.user_id,
            "title": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "status": "active",
            "messages": [],
        }
        existing_thread = _thread_cache_set(existing_thread)

        payload = _build_thread_payload(existing_thread, snap_values)

        image_url = snap_values.get("image_url") or ""

        # Attach aggregate token usage and RAG trace to the last assistant message
        usage_dict = usage_tracker.to_dict() if usage_tracker._calls else None
        if not usage_dict:
            # Fallback: extract usage from middleware-tracked analytics log
            for entry in analytics_log:
                if entry.get("event") == "llm_call":
                    usage_entry = entry.get("usage", {})
                    if usage_entry:
                        model_name = entry.get("model", "openai/gpt-5-mini")
                        input_tokens = usage_entry.get("input_tokens", 0) or usage_entry.get("prompt_tokens", 0)
                        output_tokens = usage_entry.get("output_tokens", 0) or usage_entry.get("completion_tokens", 0)
                        if input_tokens or output_tokens:
                            usage_tracker.add(model=model_name, input_tokens=int(input_tokens), output_tokens=int(output_tokens))
            if usage_tracker._calls:
                usage_dict = usage_tracker.to_dict()
        # Merge middleware events (context var, before/after graph) with state-based events (inside graph nodes)
        middleware_trace = get_rag_trace()
        state_trace = snap_values.get("_rag_trace", [])
        if state_trace and middleware_trace:
            # The last middleware event is always agent_complete; keep it after graph events
            if len(middleware_trace) >= 2:
                rag_trace_data = middleware_trace[:-1] + state_trace + middleware_trace[-1:]
            else:
                rag_trace_data = middleware_trace + state_trace
        elif state_trace:
            rag_trace_data = state_trace
        else:
            rag_trace_data = middleware_trace
        for msg in reversed(payload.get("messages", [])):
            if msg.get("role") == "assistant":
                if usage_dict:
                    msg["usage"] = usage_dict
                if rag_trace_data:
                    msg["rag_trace"] = rag_trace_data
                if image_url:
                    msg["image_url"] = image_url
                seo = {
                    k: snap_values.get(k)
                    for k in ("seo_title", "seo_keywords", "seo_description", "meta_description", "url_slug", "alt_text")
                    if snap_values.get(k)
                }
                if seo:
                    msg["seo_metadata"] = seo
                suggestions = _compute_suggestions(snap_values)
                if suggestions:
                    msg["suggestions"] = suggestions
                break

        if image_url:
            yield _encode_sse("image_url", {"url": image_url, "message_id": ""})

        now = _utc_now_iso()
        cached_thread = _thread_cache_update(thread_id, {
            "messages": payload["messages"],
            "status": payload["status"],
            "title": _derive_thread_title_from_messages(payload["messages"]),
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "title": cached_thread["title"],
            "status": payload["status"],
            "messages": payload["messages"],
            "pending_copy": payload["pending_copy"],
        })
    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("stream send_message failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


def _format_recent_conversation(messages: list[dict], max_pairs: int = 3) -> str:
    """Format recent user/assistant pairs from thread messages for LLM context."""
    pairs: list[str] = []
    for msg in reversed(messages):
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            label = "Assistant"
            content = _coerce_plain_assistant_content(content)
        elif role == "user":
            label = "User"
        else:
            continue
        pairs.insert(0, f"{label}: {content[:500]}")
    # Keep only the last max_pairs * 2 messages (pairs)
    pairs = pairs[-(max_pairs * 2):]
    return "\n\n".join(pairs) if pairs else ""


# ── Image generation (standalone, called after post or image prompt) ──


async def _sse_generate_image(thread_id: str, body: ImageGenerationRequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }
        clean_state = _middleware.before_agent(
            {
                "messages": [HumanMessage(content=body.prompt or "Generate an image")],
                "user_id": body.user_id,
                "thread_id": thread_id,
                "_current_node": "image_generator",
                "image_generation_requested": True,
            },
            config,
        )

        yield _encode_sse("start", {"status": "active", "image_generation": True})

        thread_dict = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {}
        msg_list = thread_dict.get("messages", [])

        # Use the user prompt or fall back to the last assistant text
        source_text = body.prompt
        if not source_text:
            for msg in reversed(msg_list):
                if msg.get("role") != "assistant":
                    continue
                text = str(msg.get("content") or "").strip()
                if text and not text.startswith("🖼") and not text.startswith("🔎"):
                    source_text = text[:1000]
                    break
        if not source_text:
            source_text = "a cat programmer logo"

        # Call Riverflow V2.5 Pro directly — no GPT-5-mini refinement
        logger.info("_sse_generate_image: calling Riverflow with '%s'", source_text[:80])
        from backend.graph.nodes.image_generator import _call_openrouter_image
        image_url, image_usage = _call_openrouter_image(source_text)
        logger.info("_sse_generate_image: result image_url=%s usage=%s", image_url[:60] if image_url else "None", image_usage)

        # Save to thread
        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id, "user_id": body.user_id, "title": None,
            "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(),
            "status": "active", "messages": [],
        }
        thread = _thread_cache_set(thread)
        now = _utc_now_iso()

        if body.prompt and not body.source_message_id:
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.prompt, "created_at": now}
            thread["messages"] = list(thread.get("messages", [])) + [user_msg]

        msg_id = str(uuid.uuid4())
        usage_info = None
        if image_usage:
            from backend.tracking.usage import UsageInfo
            model_used = "google/gemini-2.5-flash-image"
            input_tokens = image_usage.get("prompt_tokens", 0) or image_usage.get("input_tokens", 0) or 0
            output_tokens = image_usage.get("completion_tokens", 0) or image_usage.get("output_tokens", 0) or 0
            if input_tokens or output_tokens:
                u = UsageInfo(model=model_used, input_tokens=int(input_tokens), output_tokens=int(output_tokens))
                usage_info = u.to_dict()
        assistant_msg: dict[str, Any] = {
            "id": msg_id, "role": "assistant",
            "content": "",
            "created_at": now,
        }
        if image_url:
            assistant_msg["image_url"] = image_url
        if usage_info:
            assistant_msg["usage"] = usage_info
        assistant_msg["suggestions"] = [
            {"action": "generate_seo", "label": "SEO Metadata", "icon": "i-lucide-search", "description": "Generate SEO title, keywords and description"},
            {"action": "create_post", "label": "Create Social Media Post", "icon": "i-lucide-megaphone", "description": "Create a marketing post for this design"},
        ]

        thread["messages"] = list(thread.get("messages", [])) + [assistant_msg]
        cached_thread = _thread_cache_update(thread_id, {
            "messages": thread["messages"],
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("image_url", {"url": image_url or "", "message_id": msg_id})
        yield _encode_sse("done", {
            "title": cached_thread.get("title"),
            "status": "active",
            "messages": cached_thread.get("messages", []),
        })

    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("generate image failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


IMAGE_PROMPT_SYSTEM_PROMPT = (
    "You are a creative image prompt generator for 'The Geek Cat' brand \u2014 "
    "a German print-on-demand store selling sarcastic IT/cat-themed merchandise. "
    "Generate a detailed image generation prompt suitable for Midjourney / DALL-E / Stable Diffusion. "
    "The prompt must be in English, describe a logo or illustration style, include visual details "
    "(colors, composition, style), and fit on merchandise (t-shirts, mugs, stickers). "
    "IMPORTANT: Never include generic placeholder text like 'Brand Name', 'Your Brand', 'Brand', 'Logo', "
    "'Company Name', or similar generic text in the image. If text must appear, use a specific phrase "
    "inspired by the product theme (e.g. a tech/cat pun in German or English). "
    "Output ONLY the prompt text, no explanations, no markdown."
)


async def _sse_generate_image_prompt(thread_id: str, body: ImagePromptRequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }

        # Build a clean state via middleware (brand rules + LTM context only,
        # no conversation state from graph checkpoint)
        clean_state = _middleware.before_agent(
            {
                "messages": [HumanMessage(content=body.instruction)],
                "user_id": body.user_id,
                "thread_id": thread_id,
                "_current_node": "image_prompt_generator",
                "image_prompt_instruction": body.instruction,
            },
            config,
        )

        yield _encode_sse("start", {"status": "active"})

        llm = ChatOpenAI(
            model="openai/gpt-5-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

        # Load recent conversation context from the thread record so the LLM
        # can understand references like "the generated post".
        thread_dict = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
        if not thread_dict:
            # Fallback: load without user_id filter (thread_id is unique)
            thread_dict = _load_thread_record(thread_id) or {}
        msg_list = thread_dict.get("messages", [])
        recent_context = _format_recent_conversation(msg_list)

        # Find last REAL assistant post (skip image prompt responses from prior attempts)
        last_assistant_text = ""
        for msg in reversed(msg_list):
            if msg.get("role") != "assistant":
                continue
            text = _coerce_plain_assistant_content(str(msg.get("content") or "")).strip()
            if not text or text.startswith("🎨") or text.startswith("**Image Prompt**"):
                continue
            last_assistant_text = text
            break

        instruction = body.instruction
        if last_assistant_text:
            # Case-insensitive replacement of "the generated post" with actual content
            instruction = re.sub(
                r"\bthe generated post\b",
                last_assistant_text[:400],
                instruction,
                flags=re.IGNORECASE,
            )

        # Fetch URL content if the instruction contains a URL
        urls = _extract_urls(instruction)
        url_contexts: list[str] = []
        for url in urls:
            url_text = _fetch_url_text(url)
            if url_text:
                url_contexts.append(f"Content from {url}:\n{url_text}")
        if url_contexts:
            instruction += "\n\n" + "\n\n---\n\n".join(url_contexts)

        # Validate relevance
        combined_text = instruction + "\n".join(url_contexts) if url_contexts else instruction
        if not _is_geekcat_related(combined_text):
            lang = _detect_language(combined_text)
            yield _encode_sse("delta", {"text": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"])})
            now = _utc_now_iso()
            if not thread_dict.get("id"):
                thread_dict = {"id": thread_id, "user_id": body.user_id, "title": None, "created_at": now, "updated_at": now, "status": "active", "messages": list(msg_list)}
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"]), "created_at": now}
            thread_dict["messages"] = list(thread_dict.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread_dict["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        feedback = (clean_state.get("human_feedback_image_prompt_generator") or "").strip()
        system_content = IMAGE_PROMPT_SYSTEM_PROMPT
        if feedback:
            system_content += (
                "\n\nIMPORTANT USER FEEDBACK (previous image prompt was rejected):\n"
                f'"{feedback}"\n'
                "Use this feedback to improve the new prompt.\n"
            )
        if recent_context:
            system_content += (
                "\n\nRecent conversation context (use this to resolve references "
                "like 'the generated post' or 'my design'):\n" + recent_context
            )

        messages: list = [
            SystemMessage(content=system_content),
            HumanMessage(content=instruction),
        ]

        # Apply before_model middleware (injects brand rules, LTM context)
        messages = _middleware.before_model(messages, clean_state)

        # Stream tokens directly from the LLM (no graph state contamination)
        result_parts: list[str] = []
        stream_chunks: list[Any] = []
        async for chunk in llm.astream(messages):
            stream_chunks.append(chunk)
            content = chunk.content if hasattr(chunk, "content") else ""
            if isinstance(content, str) and content:
                result_parts.append(content)
                yield _encode_sse("delta", {"text": content})

        raw_result = "".join(result_parts).strip()

        # Extract usage from stream chunks
        usage_dict = None
        from backend.tracking.usage import extract_usage_from_chunks, UsageInfo
        chunk_usage = extract_usage_from_chunks(stream_chunks)
        if chunk_usage:
            model_name = "openai/gpt-5-mini"
            u = UsageInfo(model=model_name, input_tokens=chunk_usage.get("input_tokens", 0), output_tokens=chunk_usage.get("output_tokens", 0))
            usage_dict = u.to_dict()
        if not usage_dict and stream_chunks:
            # Fallback: try last chunk's response_metadata (OpenRouter sometimes omits usage_metadata)
            last = stream_chunks[-1]
            meta = getattr(last, "response_metadata", None) or {}
            token_usage = meta.get("token_usage") or meta.get("usage")
            if token_usage and isinstance(token_usage, dict):
                input_tokens = int(token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0) or 0)
                output_tokens = int(token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0) or 0)
                if input_tokens or output_tokens:
                    u = UsageInfo(model="openai/gpt-5-mini", input_tokens=input_tokens, output_tokens=output_tokens)
                    usage_dict = u.to_dict()

        # Apply after_model middleware
        response = type("Response", (), {"content": raw_result})()
        response = _middleware.after_model(response, clean_state)

        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id,
            "user_id": body.user_id,
            "title": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "status": "active",
            "messages": [],
        }
        thread = _thread_cache_set(thread)

        now = _utc_now_iso()
        user_msg = None
        if not body.silent:
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
        msg_id = str(uuid.uuid4())
        content = f"🎨 **Image Prompt:**\n\n{raw_result}" if raw_result else "Could not generate image prompt."
        assistant_msg = {"id": msg_id, "role": "assistant", "content": content, "created_at": now, "is_image_prompt": True}
        if usage_dict:
            assistant_msg["usage"] = usage_dict
        rag_trace_data = get_rag_trace()
        if rag_trace_data:
            assistant_msg["rag_trace"] = rag_trace_data
        assistant_msg["suggestions"] = [
            {"action": "generate_image", "label": "Generate Image", "icon": "i-lucide-image", "description": "Create the actual product image from this prompt"},
            {"action": "generate_seo", "label": "SEO Metadata", "icon": "i-lucide-search", "description": "Generate SEO title, keywords and description"},
            {"action": "create_post", "label": "Create Social Media Post", "icon": "i-lucide-megaphone", "description": "Create a marketing post for this product"},
        ]
        new_messages = list(thread.get("messages", []))
        if user_msg:
            new_messages.append(user_msg)
        new_messages.append(assistant_msg)
        thread["messages"] = new_messages

        cached_thread = _thread_cache_update(thread_id, {
            "messages": thread["messages"],
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "title": cached_thread.get("title"),
            "status": "active",
            "messages": cached_thread.get("messages", []),
            "pending_copy": cached_thread.get("pending_copy"),
        })
    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("generate image prompt failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


# ── SEO generation (standalone, like image_prompt) ──


async def _sse_generate_seo(thread_id: str, body: SEORequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }

        clean_state = _middleware.before_agent(
            {
                "messages": [HumanMessage(content=body.instruction)],
                "user_id": body.user_id,
                "thread_id": thread_id,
                "_current_node": "seo_generator",
            },
            config,
        )

        yield _encode_sse("start", {"status": "active"})

        llm = ChatOpenAI(
            model="openai/gpt-5-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

        # Load recent conversation context
        thread_dict = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
        if not thread_dict:
            thread_dict = _load_thread_record(thread_id) or {}
        msg_list = thread_dict.get("messages", [])

        # Validate product context exists (image prompt, logo, or product link)
        if not _has_product_context(thread_dict, body.instruction):
            lang = _detect_language(body.instruction)
            yield _encode_sse("delta", {"text": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"])})
            now = _utc_now_iso()
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"]), "created_at": now}
            thread_dict["messages"] = list(msg_list) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread_dict["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        recent_context = _format_recent_conversation(msg_list)

        # Find last assistant post to use as reference for SEO
        last_assistant_text = ""
        for msg in reversed(msg_list):
            if msg.get("role") != "assistant":
                continue
            text = _coerce_plain_assistant_content(str(msg.get("content") or "")).strip()
            if not text or text.startswith("🎨") or text.startswith("🔎"):
                continue
            last_assistant_text = text
            break

        instruction = body.instruction
        if last_assistant_text:
            instruction = re.sub(
                r"\bthe generated post\b",
                last_assistant_text[:400],
                instruction,
                flags=re.IGNORECASE,
            )

        product_context = thread_dict.get("product_context", [])
        product_skus = thread_dict.get("product_skus", [])
        brand_rules = thread_dict.get("brand_rules", {})

        context_parts = []
        if product_skus:
            context_parts.append("Product SKUs: " + ", ".join(product_skus))
        if product_context:
            context_parts.append("Product context:\n" + "\n\n".join(product_context[:3]))
        if brand_rules:
            rules_text = "\n".join(f"- {k}: {v}" for k, v in brand_rules.items())
            context_parts.append("Brand rules:\n" + rules_text)
        if last_assistant_text:
            context_parts.append("Generated marketing copy:\n" + last_assistant_text[:500])

        user_prompt = instruction
        if context_parts:
            user_prompt = f"{instruction}\n\nReference context:\n" + "\n\n".join(context_parts)

        # Fetch URL content if the instruction contains a URL
        urls = _extract_urls(instruction)
        url_contexts: list[str] = []
        for url in urls:
            url_text = _fetch_url_text(url)
            if url_text:
                url_contexts.append(f"Content from {url}:\n{url_text}")
        if url_contexts:
            user_prompt += "\n\n" + "\n\n---\n\n".join(url_contexts)

        # Validate relevance
        combined_text = user_prompt + "\n".join(url_contexts) if url_contexts else user_prompt
        if not _is_geekcat_related(combined_text):
            lang = _detect_language(combined_text)
            yield _encode_sse("delta", {"text": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"])})
            now = _utc_now_iso()
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"]), "created_at": now}
            thread_dict["messages"] = list(msg_list) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread_dict["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        feedback = (clean_state.get("human_feedback_seo_generator") or "").strip()
        system_content = SEO_SYSTEM_PROMPT
        if feedback:
            system_content += (
                "\n\nIMPORTANT USER FEEDBACK (previous SEO metadata was rejected):\n"
                f'"{feedback}"\n'
                "Use this feedback to improve the new SEO metadata.\n"
            )
        if recent_context:
            system_content += (
                "\n\nRecent conversation context:\n" + recent_context
            )

        messages: list = [
            SystemMessage(content=system_content),
            HumanMessage(content=user_prompt),
        ]

        messages = _middleware.before_model(messages, clean_state)

        result_parts: list[str] = []
        stream_chunks: list[Any] = []
        async for chunk in llm.astream(messages):
            stream_chunks.append(chunk)
            content = chunk.content if hasattr(chunk, "content") else ""
            if isinstance(content, str) and content:
                result_parts.append(content)
                yield _encode_sse("delta", {"text": content})

        raw_result = "".join(result_parts).strip()

        usage_dict = None
        from backend.tracking.usage import extract_usage_from_chunks
        chunk_usage = extract_usage_from_chunks(stream_chunks)
        if chunk_usage:
            model_name = "openai/gpt-5-mini"
            from backend.tracking.usage import UsageInfo
            u = UsageInfo(model=model_name, input_tokens=chunk_usage.get("input_tokens", 0), output_tokens=chunk_usage.get("output_tokens", 0))
            usage_dict = u.to_dict()

        # Parse SEO metadata from LLM output
        seo_metadata = {}
        try:
            import json
            text = raw_result.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            payload = json.loads(text)
            desc_raw = str(payload.get("seo_description") or "")
            desc_html = _md_headings_to_html(desc_raw)
            seo_metadata = {
                "seo_title": payload.get("seo_title", ""),
                "focus_keyword": payload.get("focus_keyword", ""),
                "secondary_keywords": payload.get("secondary_keywords", []),
                "meta_description": payload.get("meta_description", ""),
                "seo_description": desc_html,
                "url_slug": payload.get("url_slug", ""),
                "alt_text": payload.get("alt_text", ""),
            }
        except Exception:
            logger.warning("_sse_generate_seo: failed to parse JSON from LLM output, using raw")

        # Build formatted display text
        def _fmt(val: str) -> str:
            return val.strip() if val else ""

        seo_desc_raw = seo_metadata.get("seo_description", "")
        if seo_desc_raw and ("<" in seo_desc_raw and ">" in seo_desc_raw):
            seo_desc_clean = _html_to_text(seo_desc_raw)
        else:
            seo_desc_clean = seo_desc_raw

        if seo_metadata.get("seo_title"):
            parts = []
            if _fmt(seo_metadata.get("seo_title")):
                parts.append(f"SEO Title: {seo_metadata['seo_title']}")
            if _fmt(seo_metadata.get("focus_keyword")):
                parts.append(f"Focus Keyword: {seo_metadata['focus_keyword']}")
            if seo_metadata.get("secondary_keywords"):
                parts.append(f"Secondary Keywords: {', '.join(seo_metadata['secondary_keywords'])}")
            if _fmt(seo_metadata.get("meta_description")):
                parts.append(f"Meta Description: {seo_metadata['meta_description']}")
            if _fmt(seo_metadata.get("url_slug")):
                parts.append(f"URL Slug: {seo_metadata['url_slug']}")
            if _fmt(seo_metadata.get("alt_text")):
                parts.append(f"Alt Text: {seo_metadata['alt_text']}")
            if seo_desc_clean:
                parts.append(f"\nDescription:\n{seo_desc_clean}")
            display_text = "\n\n".join(parts) if parts else raw_result
        else:
            display_text = raw_result
        if display_text and ("<" in display_text and ">" in display_text):
            display_text = _html_to_text(display_text)
        if not display_text:
            display_text = "Could not generate SEO metadata."

        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id, "user_id": body.user_id, "title": None,
            "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(),
            "status": "active", "messages": [],
        }
        thread = _thread_cache_set(thread)

        now = _utc_now_iso()
        user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
        msg_id = str(uuid.uuid4())
        assistant_msg = {
            "id": msg_id, "role": "assistant",
            "content": f"🔎 SEO Metadata\n\n{display_text}",
            "created_at": now,
        }
        if usage_dict:
            assistant_msg["usage"] = usage_dict
        if seo_metadata.get("seo_title"):
            assistant_msg["seo_metadata"] = seo_metadata
        rag_trace_data = get_rag_trace()
        if rag_trace_data:
            assistant_msg["rag_trace"] = rag_trace_data
        assistant_msg["suggestions"] = [
            {"action": "generate_image_prompt", "label": "Create Image Prompt", "icon": "i-lucide-wand", "description": "Generate a product image prompt"},
            {"action": "generate_seo", "label": "Regenerate SEO", "icon": "i-lucide-search", "description": "Generate new SEO metadata"},
            {"action": "create_post", "label": "Create Social Media Post", "icon": "i-lucide-megaphone", "description": "Create a marketing post for SEO-optimized product"},
        ]
        thread["messages"] = list(thread.get("messages", [])) + [user_msg, assistant_msg]

        cached_thread = _thread_cache_update(thread_id, {
            "messages": thread["messages"],
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "title": cached_thread.get("title"),
            "status": "active",
            "messages": cached_thread.get("messages", []),
            "pending_copy": cached_thread.get("pending_copy"),
        })
    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("generate seo failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


def _extract_messages(state_values: dict) -> list[dict]:
    """Convert LangGraph AnyMessage list to frontend Message dicts.

    Filters out internal messages: tool results and regeneration prompts.
    Creates synthetic assistant messages from top-level state fields
    (image_prompt_result, image_url) when no assistant message exists.
    """
    msgs = []

    def _is_internal_user_message(text: str) -> bool:
        normalized = (text or "").strip().lower()
        if not normalized:
            return False

        internal_prefixes = (
            "bitte regeneriere den copy",
            "bitte ueberarbeite den copy",
            "regenerate new copy",
            "rewrite your answer",
            "create a different answer",
            "i did not like your answer",
        )
        return any(normalized.startswith(prefix) for prefix in internal_prefixes)

    for i, m in enumerate(state_values.get("messages", [])):
        msg_type = getattr(m, "type", "")

        # Skip tool results (internal RAG queries)
        if msg_type == "tool":
            continue

        role = "user" if msg_type in ("human", "user") else "assistant"
        content = m.content if isinstance(m.content, str) else str(m.content)

        # Skip internal rewrite/regeneration prompts so they never pollute persisted chat history.
        if role == "user" and _is_internal_user_message(content):
            continue

        msgs.append({
            "id": str(i),
            "role": role,
            "content": content,
            "created_at": _utc_now_iso(),
        })

    # Create synthetic assistant message from image_prompt_result if no assistant message exists
    image_prompt = (state_values.get("image_prompt_result") or "").strip()
    if image_prompt and not any(m.get("role") == "assistant" for m in msgs):
        msgs.append({
            "id": str(len(msgs)),
            "role": "assistant",
            "content": image_prompt,
            "created_at": _utc_now_iso(),
            "is_image_prompt": True,
        })

    # Attach top-level image_url to the last assistant message
    image_url = state_values.get("image_url") or ""
    if image_url:
        for msg in reversed(msgs):
            if msg.get("role") == "assistant":
                msg["image_url"] = image_url
                break

    return msgs


def _compute_suggestions(state: dict) -> list[dict]:
    """Compute contextual next-step suggestions based on graph/thread state."""
    suggestions = []

    draft = (state.get("draft_copy_de") or "").strip()
    image_prompt = (state.get("image_prompt_result") or "").strip()
    image_url = (state.get("image_url") or "").strip()
    seo_title = (state.get("seo_title") or "").strip()
    product_skus = state.get("product_skus", [])

    has_draft = bool(draft)
    has_image_prompt = bool(image_prompt)
    has_image = bool(image_url)
    has_seo = bool(seo_title)
    has_product = bool(product_skus)

    # After copywriter generates draft
    if has_draft:
        if not has_image_prompt:
            suggestions.append({
                "action": "generate_image_prompt",
                "label": "Create Image Prompt",
                "icon": "i-lucide-wand",
                "description": "Generate a product image prompt from this copy",
            })
        if not has_seo:
            suggestions.append({
                "action": "generate_seo",
                "label": "SEO Metadata",
                "icon": "i-lucide-search",
                "description": "Generate SEO title, keywords and description",
            })
        suggestions.append({
            "action": "research_trends",
            "label": "Research Trends",
            "icon": "i-lucide-trending-up",
            "description": "Find trending topics for this product",
        })

    # After image prompt is available (no draft or no image yet)
    if has_image_prompt and not has_image:
        suggestions.append({
            "action": "generate_image",
            "label": "Generate Image",
            "icon": "i-lucide-image",
            "description": "Create the actual product image",
        })
        if not has_seo:
            suggestions.append({
                "action": "generate_seo",
                "label": "SEO Metadata",
                "icon": "i-lucide-search",
                "description": "Generate SEO title, keywords and description",
            })
        if not has_draft:
            suggestions.append({
                "action": "create_post",
                "label": "Create Social Media Post",
                "icon": "i-lucide-megaphone",
                "description": "Create a marketing post for this design",
            })

    # After image is generated
    if has_image:
        if not has_seo:
            suggestions.append({
                "action": "generate_seo",
                "label": "SEO Metadata",
                "icon": "i-lucide-search",
                "description": "Optimize for search engines",
            })
        if not has_draft:
            suggestions.append({
                "action": "create_post",
                "label": "Create Social Media Post",
                "icon": "i-lucide-megaphone",
                "description": "Create a marketing post about this design",
            })

    # After SEO is generated (and no draft/prompt/image flow yet)
    if has_seo and not has_draft and not has_image_prompt:
        suggestions.append({
            "action": "generate_image_prompt",
            "label": "Create Image Prompt",
            "icon": "i-lucide-wand",
            "description": "Create a product image prompt",
        })
        suggestions.append({
            "action": "create_post",
            "label": "Create Social Media Post",
            "icon": "i-lucide-megaphone",
            "description": "Create a marketing post for SEO-optimized product",
        })

    # Product research available
    if has_product and not has_draft:
        suggestions.append({
            "action": "create_post",
            "label": "Create Social Media Post",
            "icon": "i-lucide-megaphone",
            "description": "Create a marketing post for these products",
        })

    return suggestions


# ── Relevance validation ──

_GEEKCAT_DOMAINS = {"thegeekcat.de", "thegeekcat.com", "geekcat.de"}

_GEEKCAT_KEYWORDS = {
    # German
    "geek", "katze", "katzen", "it", "programmierer", "entwickler", "admin", "server",
    "t-shirt", "tasse", "sticker", "hoodie", "merch", "print-on-demand", "bedrucken",
    "shop", "produkt", "design", "logo", "marketing", "social media", "kopie", "copy",
    "werbung", "post", "artikel", "mode", "bekleidung", "geschenk", "geschenkidee",
    "nerd", "nerdig", "sarkasmus", "humor", "witz", "lustig", "katzenliebhaber",
    "pixel", "code", "coding", "bug", "debug", "linux", "terminal", "shell",
    "entwicklerhumor", "it-humor", "cat", "cats", "geekcat",
    # English
    "merchandise", "apparel", "clothing", "gift", "present", "print on demand",
    "programmer", "developer", "sysadmin", "coding", "debugging", "sarcastic",
    "feline", "kitten", "pet", "animal", "humor", "funny",
    # Spanish
    "camiseta", "taza", "pegatina", "sudorera", "regalo", "mercadotecnia",
    "programador", "desarrollador", "gato", "gatos", "humor", "geek",
}

_GEEKCAT_IRRELEVANT_PATTERNS = [
    # Cooking / Recipes
    r"\b(recipe|cooking|baking|kitchen|dinner|lunch|breakfast|kochen|backen|rezept|koch|back|küche|ingredient)\b",
    # Sports
    r"\b(sports|football|soccer|basketball|tennis|fussball|fußball|sport|team|player|trainer|stadium|match|game|league|championship)\b",
    # Finance / Investing
    r"\b(stock|stock market|investing|invest|trading|trade|aktien|investieren|finanz|bank|konto|finance|financial|portfolio)\b",
    # Politics
    r"\b(politics|political|election|voting|vote|parliament|politik|wahl|regierung|präsident|president|minister|senator|congress|government)\b",
    # Health / Medical
    r"\b(health|medical|hospital|doctor|symptom|treatment|gesundheit|krankenhaus|arzt|krankheit|medizin|headache|pain|surgery|diagnosis|disease|patient|clinic)\b",
    # Travel
    r"\b(hotel|flight|airline|travel agency|reise|urlaub|hotel|flug|tourist|tourism|vacation|destination|booking)\b",
    # Weather
    r"\b(weather|forecast|rain|temperature|wetter|vorhersage|climate|sunny|cloudy)\b",
    # Education / Homework
    r"\b(math|homework|exam|test|lesson|classroom|unterricht|hausaufgabe|prüfung|assignment|grade|school|university|college)\b",
    # Entertainment (movies, music — unless geek/cat related)
    r"\b(movie|film|actor|actress|singer|album|concert|tv show|television|netflix)\b",
]


def _is_geekcat_related(text: str) -> bool:
    """Check if the query or URL content is related to The Geek Cat brand.

    Returns True if the text mentions:
    - The Geek Cat brand or its products
    - IT/programming/nerd culture topics
    - Cats
    - Merchandise/print-on-demand
    - Marketing/content creation

    Returns False for clearly irrelevant topics.
    """
    lower = (text or "").strip().lower()
    if not lower or len(lower) < 5:
        return True  # Too short to judge, let the model decide

    # Check for known irrelevant patterns first
    import re
    for pattern in _GEEKCAT_IRRELEVANT_PATTERNS:
        if re.search(pattern, lower):
            return False

    # Check for relevant keywords
    for keyword in _GEEKCAT_KEYWORDS:
        if keyword in lower:
            return True

    # Allow URLs from known domains
    for domain in _GEEKCAT_DOMAINS:
        if domain in lower:
            return True

    # Ambiguous — let the LLM decide
    return True


_IRRELEVANT_RESPONSES: dict[str, str] = {
    "de": (
        "Es tut mir leid, aber das liegt außerhalb meines Fachbereichs. "
        "Ich bin der Marketing-Assistent von **The Geek Cat** \u2013 "
        "einem deutschen Print-on-Demand-Store für sarkastische IT- und Katzen-Merchandise. "
        "Ich kann dir bei der Erstellung von Marketingtexten, Bildprompts, "
        "SEO-Metadaten und Social-Media-Beiträgen für The Geek Cat Produkte helfen. "
        "Bitte versuche es mit einer Anfrage zu diesem Thema."
    ),
    "en": (
        "Sorry, that\u2019s outside my area of expertise. "
        "I\u2019m the marketing assistant for **The Geek Cat** \u2013 "
        "a German print-on-demand store selling sarcastic IT/cat-themed merchandise. "
        "I can help you create marketing copy, image prompts, SEO metadata, "
        "and social media posts for The Geek Cat products. "
        "Please try a request related to this topic."
    ),
    "es": (
        "Lo siento, eso está fuera de mi área de especialización. "
        "Soy el asistente de marketing de **The Geek Cat** \u2013 "
        "una tienda alemana de print-on-demand que vende merchandising sarcástico de TI y gatos. "
        "Puedo ayudarte a crear textos de marketing, prompts de imágenes, "
        "metadatos SEO y publicaciones para redes sociales de productos The Geek Cat. "
        "Por favor, intenta con una solicitud relacionada con este tema."
    ),
}


_NO_PRODUCT_CONTEXT_RESPONSE: dict[str, str] = {
    "de": (
        "Bevor ich einen Social-Media-Beitrag oder SEO-Metadaten erstellen kann, "
        "ben\u00f6tige ich zun\u00e4chst ein Produktdesign oder einen Bild-Prompt. "
        "Bitte erstelle zuerst einen Bild-Prompt oder lade ein Produktbild / Logo hoch. "
        "Du kannst auch einen Link zu einem Produkt angeben, damit ich die Informationen daraus verwenden kann."
    ),
    "en": (
        "Before I can create a social media post or SEO metadata, "
        "I need a product design or image prompt first. "
        "Please create an image prompt or upload a product image / logo. "
        "You can also provide a link to a product so I can use the information from it."
    ),
    "es": (
        "Antes de poder crear una publicaci\u00f3n o metadatos SEO, "
        "necesito primero un dise\u00f1o de producto o un prompt de imagen. "
        "Por favor, crea un prompt de imagen o sube una imagen del producto / logo. "
        "Tambi\u00e9n puedes proporcionar un enlace a un producto para usar la informaci\u00f3n del mismo."
    ),
}


_CREATION_KEYWORDS = re.compile(
    r"(create|erstelle|crea|generate|genera|generar|write|schreib|escribe|make|mach|haz)"
    r"(\s+\w+){0,4}\s*(post|beitrag|publicación|entry|eintrag)"
    r"|(create|generate|genera|write|make|erstelle|crea)"
    r"(\s+\w+){0,4}\s*(seo|social media|instagram|facebook|linkedin|tweet|thread|image|bild|imagen|logo|design|diseño|caption|text|copy)"
    r"|(instagram|social media|post|seo)\s+(post|beitrag|metadaten|metadata|meta)"
    r"|prompt\s*(für|for|para|de)\s*(ein|a|un)?\s*(bild|image|imagen|logo)"
    r"|(crear|generar|escribir|hacer)\s+(una\s+)?(publicación|imagen|logo)"
    r"|(einen|ein)\s+(beitrag|post|social.media|bild|logo)\s+(erstellen|generieren|schreiben|machen)",
    re.IGNORECASE,
)


def _is_creation_request(text: str) -> bool:
    """Check if the user is asking to create content (post, SEO, image, etc.)."""
    return bool(_CREATION_KEYWORDS.search(text))


_EVALUATION_TARGET_PREFIXES: dict[str, str] = {
    "🎨": "image_prompt_generator",
    "🔎": "seo_generator",
    "**Image Prompt**": "image_prompt_generator",
    "🖼": "image_generator",
}


def _determine_evaluation_target_node(messages: list[dict]) -> str:
    """Determine which graph node generated the last assistant message."""
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        if msg.get("is_image_prompt"):
            return "image_prompt_generator"
        if msg.get("image_url"):
            return "image_generator"
        content = str(msg.get("content") or "").strip()
        for prefix, node in _EVALUATION_TARGET_PREFIXES.items():
            if content.startswith(prefix):
                return node
        return "copywriter"
    return "copywriter"


def _has_product_context(thread_dict: dict | None, instruction: str = "") -> bool:
    """Check if the thread has product context (image prompt, image, or URL content)
    needed to generate SEO or social media posts."""
    if not thread_dict:
        return bool(instruction.strip())

    messages = thread_dict.get("messages", [])

    # Check for image prompt messages
    for msg in messages:
        if msg.get("is_image_prompt"):
            return True

    # Check for messages with image_url
    for msg in messages:
        if msg.get("image_url"):
            return True

    # Check for product context in thread dict
    if thread_dict.get("product_context") or thread_dict.get("product_skus"):
        return True

    # Check if instruction contains a URL (product link provided)
    if instruction.strip() and _extract_urls(instruction):
        return True

    # Check for image prompt content in assistant messages (content starts with 🎨)
    for msg in messages:
        if msg.get("role") == "assistant" and str(msg.get("content") or "").startswith("\U0001f3a8"):
            return True

    # If the instruction itself has meaningful text, let the graph/RAG decide
    if instruction.strip():
        return True

    return False


def _detect_language(text: str) -> str:
    """Detect if text is German, Spanish, or default to English."""
    lower = text.lower()
    # German-specific characters
    if any(c in lower for c in "äöüß"):
        return "de"
    # Spanish-specific characters or common Spanish words
    if any(c in lower for c in "ñáéíóú¿¡"):
        return "es"
    # Common Spanish words without accents
    spanish_indicators = [
        "crea un", "crea una", "diseña", "necesito", "quiero", "puedes", "por favor",
        "publicación", "redes sociales", "para mi", "para mis", "genera un", "genera una",
        "haz un", "haz una", "escribe un", "el link", "la página", "esta página",
    ]
    for indicator in spanish_indicators:
        if indicator in lower:
            return "es"
    return "en"


SOCIAL_POST_SYSTEM_PROMPT = (
    "You are a social media marketing specialist for 'The Geek Cat' brand \u2014 "
    "a German print-on-demand store selling sarcastic IT/cat-themed merchandise. "
    "Create an engaging social media post for the given product. "
    "The post should be in German, include a hook, body, call-to-action, and relevant hashtags. "
    "Keep it concise and suitable for Instagram, Facebook, and TikTok. "
    "Output ONLY the post text, no explanations, no markdown formatting."
)


async def _sse_generate_social_post(thread_id: str, body: SocialPostRequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }

        clean_state = _middleware.before_agent(
            {
                "messages": [HumanMessage(content=body.instruction)],
                "user_id": body.user_id,
                "thread_id": thread_id,
                "_current_node": "social_post_generator",
            },
            config,
        )

        yield _encode_sse("start", {"status": "active"})

        thread_dict = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
        if not thread_dict:
            thread_dict = _load_thread_record(thread_id) or {}
        msg_list = thread_dict.get("messages", [])
        recent_context = _format_recent_conversation(msg_list)

        # Validate product context exists (image prompt, logo, or product link)
        if not _has_product_context(thread_dict, body.instruction):
            lang = _detect_language(body.instruction)
            yield _encode_sse("delta", {"text": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"])})
            now = _utc_now_iso()
            if not thread_dict.get("id"):
                thread_dict = {"id": thread_id, "user_id": body.user_id, "title": None, "created_at": now, "updated_at": now, "status": "active", "messages": list(msg_list)}
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _NO_PRODUCT_CONTEXT_RESPONSE.get(lang, _NO_PRODUCT_CONTEXT_RESPONSE["en"]), "created_at": now}
            thread_dict["messages"] = list(thread_dict.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread_dict["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        if not body.product_type:
            # No product type selected -- ask user with suggestion cards
            now = _utc_now_iso()
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": "F\u00fcr welchen Produkttyp m\u00f6chtest du den Social-Media-Beitrag erstellen?",
                "created_at": now,
                "suggestions": [
                    {"action": "create_post_tshirt", "label": "T-Shirt", "icon": "i-lucide-shirt", "description": "Social-Media-Beitrag f\u00fcr ein T-Shirt"},
                    {"action": "create_post_mug", "label": "Tasse", "icon": "i-lucide-coffee", "description": "Social-Media-Beitrag f\u00fcr eine Tasse"},
                    {"action": "create_post_sticker", "label": "Sticker", "icon": "i-lucide-tag", "description": "Social-Media-Beitrag f\u00fcr einen Sticker"},
                    {"action": "create_post_hoodie", "label": "Hoodie", "icon": "i-lucide-shirt", "description": "Social-Media-Beitrag f\u00fcr einen Hoodie"},
                ],
            }

            thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
                "id": thread_id, "user_id": body.user_id, "title": None,
                "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(),
                "status": "active", "messages": [],
            }
            thread = _thread_cache_set(thread)
            thread["messages"] = list(thread.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread["messages"], "updated_at": now})
            _save_thread_record(cached_thread)

            yield _encode_sse("done", {
                "title": cached_thread.get("title"),
                "status": "active",
                "messages": cached_thread.get("messages", []),
            })
            return

        # Product type selected -- generate social post
        llm = ChatOpenAI(
            model="openai/gpt-5-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

        # Find last assistant text for context
        last_assistant_text = ""
        for msg in reversed(msg_list):
            if msg.get("role") != "assistant":
                continue
            text = _coerce_plain_assistant_content(str(msg.get("content") or "")).strip()
            if not text or text.startswith("\U0001f3a8") or text.startswith("\U0001f50e"):
                continue
            last_assistant_text = text
            break

        instruction = body.instruction
        if last_assistant_text:
            instruction = re.sub(
                r"\bthe generated post\b",
                last_assistant_text[:400],
                instruction,
                flags=re.IGNORECASE,
            )

        # Fetch URL content if the instruction contains a URL
        urls = _extract_urls(instruction)
        url_contexts: list[str] = []
        for url in urls:
            url_text = _fetch_url_text(url)
            if url_text:
                url_contexts.append(f"Content from {url}:\n{url_text}")
        if url_contexts:
            instruction += "\n\n" + "\n\n---\n\n".join(url_contexts)

        # Validate relevance
        if url_contexts:
            combined_text = instruction + "\n".join(url_contexts)
        else:
            combined_text = instruction
        if not _is_geekcat_related(combined_text):
            lang = _detect_language(combined_text)
            yield _encode_sse("delta", {"text": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"])})
            now = _utc_now_iso()
            if not thread_dict.get("id"):
                thread_dict = {"id": thread_id, "user_id": body.user_id, "title": None, "created_at": now, "updated_at": now, "status": "active", "messages": list(msg_list)}
            user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": body.instruction, "created_at": now}
            assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": _IRRELEVANT_RESPONSES.get(lang, _IRRELEVANT_RESPONSES["en"]), "created_at": now}
            thread_dict["messages"] = list(thread_dict.get("messages", [])) + [user_msg, assistant_msg]
            cached_thread = _thread_cache_update(thread_id, {"messages": thread_dict["messages"], "updated_at": now})
            _save_thread_record(cached_thread)
            yield _encode_sse("done", {"title": cached_thread.get("title"), "status": "active", "messages": cached_thread.get("messages", [])})
            return

        feedback = (clean_state.get("human_feedback_copywriter") or "").strip()
        system_content = SOCIAL_POST_SYSTEM_PROMPT
        if feedback:
            system_content += (
                "\n\nIMPORTANT USER FEEDBACK (previous social post was rejected):\n"
                f'"{feedback}"\n'
                "Use this feedback to improve the new post.\n"
            )
        system_content += f"\n\nProduct type: {body.product_type}"
        if recent_context:
            system_content += "\n\nRecent conversation context:\n" + recent_context
        if last_assistant_text:
            system_content += f"\n\nReference content:\n{last_assistant_text[:500]}"

        messages: list = [
            SystemMessage(content=system_content),
            HumanMessage(content=instruction),
        ]

        messages = _middleware.before_model(messages, clean_state)

        result_parts: list[str] = []
        stream_chunks: list[Any] = []
        async for chunk in llm.astream(messages):
            stream_chunks.append(chunk)
            content = chunk.content if hasattr(chunk, "content") else ""
            if isinstance(content, str) and content:
                result_parts.append(content)
                yield _encode_sse("delta", {"text": content})

        raw_result = "".join(result_parts).strip()

        # Extract usage from stream chunks
        usage_dict = None
        from backend.tracking.usage import extract_usage_from_chunks, UsageInfo
        chunk_usage = extract_usage_from_chunks(stream_chunks)
        if chunk_usage:
            model_name = "openai/gpt-5-mini"
            u = UsageInfo(model=model_name, input_tokens=chunk_usage.get("input_tokens", 0), output_tokens=chunk_usage.get("output_tokens", 0))
            usage_dict = u.to_dict()
        if not usage_dict and stream_chunks:
            last = stream_chunks[-1]
            meta = getattr(last, "response_metadata", None) or {}
            token_usage = meta.get("token_usage") or meta.get("usage")
            if token_usage and isinstance(token_usage, dict):
                input_tokens = int(token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0) or 0)
                output_tokens = int(token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0) or 0)
                if input_tokens or output_tokens:
                    u = UsageInfo(model="openai/gpt-5-mini", input_tokens=input_tokens, output_tokens=output_tokens)
                    usage_dict = u.to_dict()

        # Apply after_model middleware
        response = type("Response", (), {"content": raw_result})()
        response = _middleware.after_model(response, clean_state)

        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id, "user_id": body.user_id, "title": None,
            "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(),
            "status": "active", "messages": [],
        }
        thread = _thread_cache_set(thread)

        now = _utc_now_iso()
        assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": raw_result or f"Could not generate social media post for {body.product_type}.", "created_at": now}
        if usage_dict:
            assistant_msg["usage"] = usage_dict
        rag_trace_data = get_rag_trace()
        if rag_trace_data:
            assistant_msg["rag_trace"] = rag_trace_data
        assistant_msg["suggestions"] = [
            {"action": "generate_image_prompt", "label": "Create Image Prompt", "icon": "i-lucide-wand", "description": "Generate a product image prompt"},
            {"action": "generate_seo", "label": "SEO Metadata", "icon": "i-lucide-search", "description": "Generate SEO title, keywords and description"},
            {"action": "copy_clipboard", "label": "Copy to Clipboard", "icon": "i-lucide-copy", "description": "Copy this post to clipboard"},
        ]

        thread["messages"] = list(thread.get("messages", [])) + [assistant_msg]
        cached_thread = _thread_cache_update(thread_id, {"messages": thread["messages"], "updated_at": now})
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "title": cached_thread.get("title"),
            "status": "active",
            "messages": cached_thread.get("messages", []),
        })

    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("generate social post failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


def _message_signature(message: dict) -> tuple[str, str]:
    return (
        str(message.get("role") or ""),
        str(message.get("content") or "").strip(),
    )


def _merge_thread_messages(existing_messages: list[dict], extracted_messages: list[dict]) -> list[dict]:
    """Merge graph-extracted messages into stored thread history without losing prior turns.

    LangGraph state may include only a compacted window. We preserve existing persisted
    history and append only new tail messages by suffix/prefix overlap.
    """
    if not existing_messages:
        return list(extracted_messages)
    if not extracted_messages:
        return list(existing_messages)

    max_overlap = min(len(existing_messages), len(extracted_messages))
    overlap = 0

    for size in range(max_overlap, 0, -1):
        existing_tail = [_message_signature(m) for m in existing_messages[-size:]]
        extracted_head = [_message_signature(m) for m in extracted_messages[:size]]
        if existing_tail == extracted_head:
            overlap = size
            break

    if overlap > 0:
        return [*existing_messages, *extracted_messages[overlap:]]

    existing_signatures = [_message_signature(m) for m in existing_messages]
    extracted_signatures = [_message_signature(m) for m in extracted_messages]

    # If extracted window already exists contiguously anywhere, keep existing history unchanged.
    max_window = min(len(existing_signatures), len(extracted_signatures))
    for size in range(max_window, 0, -1):
        target = extracted_signatures[:size]
        for start in range(0, len(existing_signatures) - size + 1):
            if existing_signatures[start:start + size] == target:
                if size == len(extracted_signatures):
                    return list(existing_messages)
                return [*existing_messages, *extracted_messages[size:]]

    # Fallback: avoid dropping existing history when there is no detectable overlap.
    merged = list(existing_messages)
    seen_signatures = set(existing_signatures)
    last_signature = _message_signature(existing_messages[-1]) if existing_messages else None
    for message in extracted_messages:
        signature = _message_signature(message)
        if signature == last_signature:
            continue
        if signature in seen_signatures:
            # Already present in persisted history (user or assistant).
            continue
        merged.append(message)
        seen_signatures.add(signature)
        last_signature = signature
    return merged


def _filter_internal_user_messages(messages: list[dict]) -> list[dict]:
    """Remove internal rewrite/regeneration user prompts from persisted thread history."""
    internal_prefixes = (
        "bitte regeneriere den copy",
        "bitte ueberarbeite den copy",
        "regenerate new copy",
        "rewrite your answer",
        "create a different answer",
        "i did not like your answer",
    )

    filtered: list[dict] = []
    for message in messages or []:
        if (message.get("role") or "") != "user":
            filtered.append(message)
            continue

        content = str(message.get("content") or "").strip().lower()
        if any(content.startswith(prefix) for prefix in internal_prefixes):
            continue
        filtered.append(message)
    return filtered


def _strip_leading_user_echo(text: str, messages: list[dict]) -> str:
    """Remove leading user prompt echoed at start of assistant draft."""
    content = (text or "").strip()
    if not content:
        return content

    user_prompts: list[str] = []
    for message in messages or []:
        if (message.get("role") or "") != "user":
            continue
        candidate = str(message.get("content") or "").strip()
        if candidate:
            user_prompts.append(candidate)

    if not user_prompts:
        return content

    lines = [line.rstrip() for line in content.splitlines()]
    if not lines:
        return content

    first = lines[0].strip().lstrip("-•* ").strip()
    first_lower = first.lower()
    for prompt in user_prompts:
        prompt_lower = prompt.lower()
        if first_lower == prompt_lower or first_lower.startswith(prompt_lower + ":"):
            cleaned = "\n".join(lines[1:]).strip()
            return cleaned or content
    return content


def _coerce_plain_assistant_content(text: str) -> str:
    """Convert JSON-like assistant content into the final plain-text copy format."""
    raw = (text or "").strip()
    if not raw:
        return raw

    candidate = raw
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)

    payload = None
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        match = re.search(r"\{[\s\S]*\}", candidate)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = None

    if not payload:
        return raw

    hook = str(payload.get("hook") or "").strip()
    body = str(payload.get("body") or "").strip()
    cta = str(payload.get("cta") or "").strip()
    hashtags_raw = payload.get("hashtags") or []
    hashtags = " ".join(
        [tag if str(tag).startswith("#") else f"#{tag}" for tag in hashtags_raw if str(tag).strip()]
    ).strip()
    blocks = [segment for segment in [hook, body, cta, hashtags] if segment]
    return "\n\n".join(blocks) if blocks else raw


def _normalize_thread_messages(messages: list[dict]) -> list[dict]:
    """Normalize messages for frontend: strip assistant prompt-echo and collapse immediate duplicates."""
    normalized: list[dict] = []
    for message in messages or []:
        item = dict(message)
        role = str(item.get("role") or "")
        if role == "assistant":
            item["content"] = _coerce_plain_assistant_content(str(item.get("content") or ""))
            item["content"] = _strip_leading_user_echo(str(item.get("content") or ""), normalized)

        if normalized and _message_signature(normalized[-1]) == _message_signature(item):
            continue
        normalized.append(item)

    for index, message in enumerate(normalized):
        message["id"] = str(index)
    return normalized


def _compose_copy_from_parts(parts: dict[str, str], hashtags: list[str] | None = None) -> str:
    hook = (parts.get("hook") or "").strip()
    body = (parts.get("body") or "").strip()
    cta = (parts.get("cta") or "").strip()
    blocks = [segment for segment in [hook, body, cta] if segment]
    hashtags_line = " ".join([h if str(h).startswith("#") else f"#{h}" for h in (hashtags or [])]).strip()
    if hashtags_line:
        blocks.append(hashtags_line)
    return "\n\n".join(blocks)


def _sanitize_title_seed(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""

    lower = cleaned.lower()
    for prefix in (
        "necesito ", "quiero ", "genera ", "genera un ", "genera una ", "crea ",
        "haz ", "escribe ", "promote ", "erstelle ", "schreib ", "write ", "create ",
    ):
        if lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break

    cleaned = cleaned.strip(" .,:;-")
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def _derive_thread_title_from_messages(messages: list[dict], max_len: int = 72) -> str:
    user_texts: list[str] = []
    for msg in messages or []:
        if msg.get("role") != "user":
            continue
        content = _sanitize_title_seed(str(msg.get("content") or ""))
        if content:
            user_texts.append(content)

    if not user_texts:
        return "Konversation"

    first = user_texts[0]
    last = user_texts[-1]
    base = first if first.lower() == last.lower() else f"{first} | {last}"

    if len(base) <= max_len:
        return base
    return base[: max_len - 1].rstrip() + "..."


def _extract_sources(state_values: dict) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    metadata_sources = (state_values.get("copy_metadata") or {}).get("sources") or []
    for source in metadata_sources:
        if not isinstance(source, dict):
            continue
        label = str(source.get("label") or "Quelle").strip()
        url = str(source.get("url") or "").strip()
        key = f"{label}|{url}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({"label": label, "url": url, "type": str(source.get("type") or "reference")})

    for context_line in state_values.get("product_context", []) or []:
        if not isinstance(context_line, str):
            continue
        name = ""
        sku = ""
        for segment in context_line.split("|"):
            segment = segment.strip()
            if segment.startswith("NAME="):
                name = segment.split("=", 1)[1].strip()
            elif segment.startswith("SKU="):
                sku = segment.split("=", 1)[1].strip()

        if not name and not sku:
            continue
        label = name or sku
        source = {
            "label": label,
            "url": "",
            "type": "product",
        }
        key = f"{source['label']}|{source['url']}"
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)

    for source in sources:
        if not source.get("url") and source.get("type") == "product":
            source["url"] = "https://thegeekcat.de"

    return sources[:6]


def _build_thread_payload(thread: dict, state_values: dict | None = None) -> dict:
    """Return a frontend-ready thread payload from stored thread and graph state."""
    values = state_values or {}

    draft_copy = values.get("draft_copy_de") or ""
    approval_status = values.get("approval_status")
    metadata = values.get("copy_metadata") or {}

    status = thread.get("status", "active")
    if approval_status == "approved":
        status = "published"
    elif draft_copy and approval_status != "approved":
        status = "awaiting_approval"
    elif values:
        status = "active"

    messages = _filter_internal_user_messages(thread.get("messages", []))
    if values:
        extracted_messages = _filter_internal_user_messages(_extract_messages(values))
        messages = _merge_thread_messages(messages, extracted_messages)
        if draft_copy:
            draft_copy = _coerce_plain_assistant_content(draft_copy)
            draft_copy = _strip_leading_user_echo(draft_copy, messages)
        has_visible_assistant_reply = bool(messages and messages[-1].get("role") == "assistant")
        if draft_copy and not has_visible_assistant_reply and not any(
            m.get("role") == "assistant" and str(m.get("content") or "").strip() == draft_copy
            for m in messages
        ):
            messages.append({
                "id": str(len(messages)),
                "role": "assistant",
                "content": draft_copy,
                "created_at": _utc_now_iso(),
            })

    messages = _normalize_thread_messages(messages)

    pending_copy = None
    if status == "awaiting_approval" and draft_copy:
        sources = _extract_sources(values)
        pending_copy = {
            "content": draft_copy,
            "hashtags": metadata.get("hashtags", []),
            "product_name": metadata.get("product_name"),
            "product_url": metadata.get("product_url"),
            "parts": metadata.get("parts", {}),
            "sources": sources,
        }

    return {
        **thread,
        "status": status,
        "messages": messages,
        "pending_copy": pending_copy,
    }

router = APIRouter(prefix="/api")


def init_routes(
    graph: CompiledStateGraph,
    middleware: GeekCatMiddleware,
    memory: MemoryManager,
):
    global _graph, _middleware, _memory
    _graph = graph
    _middleware = middleware
    _memory = memory


# ── Chat Threads ──

@router.get("/chat/threads", response_model=list[ThreadListItemResponse])
async def list_threads(user_id: str, q: str = ""):
    """List all chat threads for a user."""
    records = _list_thread_records(user_id, q)
    if not records and _migrate_single_owner_threads_to_user_id(user_id):
        records = _list_thread_records(user_id, q)

    for record in records:
        cached = _thread_cache_get(record["id"])
        if cached:
            record["messages"] = cached.get("messages", [])
            record["status"] = cached.get("status", record["status"])
            record["title"] = cached.get("title") or record["title"]
            record["updated_at"] = cached.get("updated_at", record["updated_at"])

        if not record.get("title"):
            derived_messages = record.get("messages", [])
            if not derived_messages and _graph:
                try:
                    state = await _graph.aget_state({"configurable": {"thread_id": record["id"]}})
                    if state and state.values:
                        derived_messages = _extract_messages(state.values)
                        record["messages"] = derived_messages
                except Exception:
                    derived_messages = []

            if derived_messages:
                record["title"] = _derive_thread_title_from_messages(derived_messages)
                record["updated_at"] = _utc_now_iso()
                _save_thread_record(record)

        if not record.get("title"):
            record["title"] = "Konversation"

        if record["id"] in _threads:
            _thread_cache_update(record["id"], {"title": record["title"]})
    records.sort(key=lambda thread: thread.get("updated_at") or "", reverse=True)
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
            "status": t["status"],
            "message_count": len(t["messages"]),
        }
        for t in records
    ]


@router.post("/chat/threads", response_model=ThreadDetailResponse)
def create_thread(body: ThreadCreateRequest):
    """Create a new chat thread for a user."""
    thread_id = str(uuid.uuid4())
    now = _utc_now_iso()
    thread = {
        "id": thread_id,
        "user_id": body.user_id,
        "title": None,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "messages": [],
    }
    _thread_cache_set(thread)
    _save_thread_record(thread)
    logger.info("create_thread: user=%s thread=%s", body.user_id, thread_id)
    return thread


@router.get("/chat/threads/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(thread_id: str, user_id: str = ""):
    """Get a specific thread with its messages from graph state."""
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, user_id or None)
    if not thread and user_id and _migrate_single_owner_threads_to_user_id(user_id):
        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    _thread_cache_set(thread)
    # Pull latest messages from graph state when available
    if _graph:
        try:
            config = {"configurable": {"thread_id": thread_id}}
            state = await _graph.aget_state(config)
            if state and state.values:
                payload = _build_thread_payload(thread, state.values)
                payload_title = _derive_thread_title_from_messages(payload.get("messages", []))
                if thread_id in _threads:
                    cached_thread = _thread_cache_update(thread_id, {
                        "title": payload_title,
                        "status": payload["status"],
                        "messages": payload["messages"],
                        "updated_at": _utc_now_iso(),
                    })
                    _save_thread_record(cached_thread)
                payload["title"] = payload_title
                return payload
        except Exception as exc:
            logger.warning("get_thread graph state failed for %s: %s", thread_id, exc)
    payload = _build_thread_payload(thread)
    payload_title = _derive_thread_title_from_messages(payload.get("messages", []))
    cached_thread = _thread_cache_update(thread_id, {
        "title": payload_title,
        "user_id": thread.get("user_id", ""),
    })
    _save_thread_record(cached_thread)
    payload["title"] = payload_title
    return payload


@router.post("/chat/threads/{thread_id}/messages", response_model=ThreadActionResponse)
async def send_message(thread_id: str, body: MessageSendRequest):
    """Send a user message to the agent and execute the marketing pipeline."""
    if not _graph or not _middleware:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    try:
        return await _send_message_impl(thread_id, body)
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        logger.error("send_message unhandled error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


@router.post("/chat/threads/{thread_id}/messages/stream")
def send_message_stream(thread_id: str, body: MessageSendRequest):
    if not _graph or not _middleware:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    return StreamingResponse(
        _sse_send_message(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/chat/threads/{thread_id}/image-prompt/stream")
def generate_image_prompt_stream(thread_id: str, body: ImagePromptRequest):
    return StreamingResponse(
        _sse_generate_image_prompt(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/chat/threads/{thread_id}/seo/stream")
def generate_seo_stream(thread_id: str, body: SEORequest):
    return StreamingResponse(
        _sse_generate_seo(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/chat/threads/{thread_id}/image/stream")
def generate_image_stream(thread_id: str, body: ImageGenerationRequest):
    return StreamingResponse(
        _sse_generate_image(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/chat/threads/{thread_id}/social-post/stream")
def generate_social_post_stream(thread_id: str, body: SocialPostRequest):
    return StreamingResponse(
        _sse_generate_social_post(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


_ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@router.post("/chat/threads/{thread_id}/upload-image")
async def upload_thread_image(
    thread_id: str,
    user_id: str = "",
    file: UploadFile = File(...),
):
    """Upload a product image or logo and attach it to the thread."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_IMAGE_EXTENSIONS))}",
        )

    images_dir = Path(__file__).resolve().parent.parent / "static" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = images_dir / filename
    content = await file.read()
    filepath.write_bytes(content)

    api_base = os.environ.get("API_BASE_URL", "http://localhost:8000")
    image_url = f"{api_base}/static/images/{filename}"

    # Attach the image as an assistant message in the thread
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, user_id or None) or {
        "id": thread_id, "user_id": user_id, "title": None,
        "created_at": _utc_now_iso(), "updated_at": _utc_now_iso(),
        "status": "active", "messages": [],
    }
    thread = _thread_cache_set(thread)
    now = _utc_now_iso()
    user_msg = {"id": str(uuid.uuid4()), "role": "user", "content": f"[Uploaded image: {file.filename}]", "created_at": now}
    assistant_msg = {"id": str(uuid.uuid4()), "role": "assistant", "content": "", "created_at": now, "image_url": image_url}
    assistant_msg["suggestions"] = [
        {"action": "generate_image_prompt", "label": "Create Image Prompt", "icon": "i-lucide-wand", "description": "Generate a product image prompt from this image"},
        {"action": "generate_seo", "label": "SEO Metadata", "icon": "i-lucide-search", "description": "Generate SEO title, keywords and description"},
        {"action": "create_post", "label": "Create Social Media Post", "icon": "i-lucide-megaphone", "description": "Create a marketing post for this image"},
    ]
    thread["messages"] = list(thread.get("messages", [])) + [user_msg, assistant_msg]
    cached_thread = _thread_cache_update(thread_id, {"messages": thread["messages"], "updated_at": now})
    _save_thread_record(cached_thread)

    return {"image_url": image_url, "thread_id": thread_id, "status": "active", "messages": cached_thread.get("messages", [])}


@router.delete("/chat/threads/{thread_id}", response_model=DeleteThreadResponse)
def delete_thread(thread_id: str, user_id: str):
    deleted = _delete_thread_record(thread_id, user_id)
    if not deleted and _migrate_single_owner_threads_to_user_id(user_id):
        deleted = _delete_thread_record(thread_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted", "thread_id": thread_id}


async def _send_message_impl(thread_id: str, body: MessageSendRequest):
    config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": body.user_id,
        }
    }

    # ── Hook 1: before_agent ──
    initial_state = _middleware.before_agent(
        {
            "messages": [HumanMessage(content=body.content)],
            "user_id": body.user_id,
            "thread_id": thread_id,
        },
        config,
    )

    # ── Graph invocation ──
    try:
        result = await _graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        logger.error("graph.invoke failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(exc)}")

    # ── Hook 6: after_agent ──
    result = _middleware.after_agent(result, config)

    # Check if paused at HITL
    state_snapshot = await _graph.aget_state(config)
    snap_values = state_snapshot.values if state_snapshot else result

    existing_thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
        "id": thread_id,
        "user_id": body.user_id,
        "title": None,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "status": "active",
        "messages": [],
    }
    existing_thread = _thread_cache_set(existing_thread)

    payload = _build_thread_payload(existing_thread, snap_values)

    # Update in-memory thread store
    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "messages": payload["messages"],
        "status": payload["status"],
        "title": _derive_thread_title_from_messages(payload["messages"]),
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    return {
        "title": cached_thread["title"],
        "status": payload["status"],
        "messages": payload["messages"],
        "pending_copy": payload["pending_copy"],
    }


@router.get("/chat/threads/{thread_id}/state", response_model=ThreadStateResponse)
async def get_thread_state(thread_id: str, user_id: str = ""):
    """Get the current state of a thread (for polling HITL status)."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = await _graph.aget_state(config)
    except Exception:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, user_id or None)
    if not thread:
        thread = {
            "id": thread_id,
            "user_id": user_id or "",
            "title": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "status": "active",
            "messages": [],
        }

    payload = _build_thread_payload(thread, state.values or {})

    return {
        "status": payload["status"],
        "messages": payload["messages"],
        "pending_copy": payload["pending_copy"],
    }


@router.post("/chat/threads/{thread_id}/approve", response_model=ThreadActionResponse)
async def approve_copy(thread_id: str, body: ApprovalRequest):
    """Save a positive evaluation (thumbs-up) as a retrievable memory and on the message."""
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Update the message's rating in the thread
    messages = list(thread.get("messages", []))
    if body.message_id:
        for msg in messages:
            if msg.get("id") == body.message_id:
                msg["rating"] = "up"
                break
    else:
        # fallback: last assistant message
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                msg["rating"] = "up"
                break

    copy_text = (body.edited_copy or "").strip()
    if not copy_text:
        if messages:
            last_assistant = [m for m in messages if m.get("role") == "assistant"]
            if last_assistant:
                copy_text = last_assistant[-1].get("content", "").strip()

    if _memory:
        try:
            coll = _memory.ltm.vector_store.get_user_collection(body.user_id)
            existing = coll.get(where={"type": "user_evaluation", "thread_id": thread_id})
            if existing and existing.get("ids"):
                for doc_id in existing["ids"]:
                    _memory.ltm.delete(body.user_id, doc_id)
        except Exception:
            logger.warning("approve: failed to clear prior evaluations", exc_info=True)

        target_node = _determine_evaluation_target_node(messages)
        text_to_save = copy_text or "thumbs_up"
        _memory.ltm.save(body.user_id, f"Positive Bewertung: {text_to_save}", {
            "type": "user_evaluation",
            "thread_id": thread_id,
            "rating": "up",
            "target_node": target_node,
        })

        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "up",
            "feedback": body.feedback or "thumbs_up",
            "edited": bool(body.edited_copy or body.edited_parts),
        })

    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "messages": messages,
        "status": "active",
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    return {
        "status": "active",
        "messages": cached_thread.get("messages", []),
        "pending_copy": cached_thread.get("pending_copy"),
    }


@router.post("/chat/threads/{thread_id}/reject", response_model=ThreadActionResponse)
async def reject_copy(thread_id: str, body: ApprovalRequest):
    """Save a negative evaluation (thumbs-down) as a retrievable memory and on the message."""
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Update the message's rating in the thread
    messages = list(thread.get("messages", []))
    if body.message_id:
        for msg in messages:
            if msg.get("id") == body.message_id:
                msg["rating"] = "down"
                break
    else:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                msg["rating"] = "down"
                break

    if _memory:
        try:
            coll = _memory.ltm.vector_store.get_user_collection(body.user_id)
            existing = coll.get(where={"type": "user_evaluation", "thread_id": thread_id})
            if existing and existing.get("ids"):
                for doc_id in existing["ids"]:
                    _memory.ltm.delete(body.user_id, doc_id)
        except Exception:
            logger.warning("reject: failed to clear prior evaluations", exc_info=True)

        copy_text = (body.feedback or "").strip()
        if not copy_text:
            messages_list = thread.get("messages", [])
            if messages_list:
                last_assistant = [m for m in messages_list if m.get("role") == "assistant"]
                if last_assistant:
                    copy_text = last_assistant[-1].get("content", "").strip()

        target_node = _determine_evaluation_target_node(messages)
        _memory.ltm.save(body.user_id, f"Negative Bewertung: {copy_text or 'thumbs_down'}", {
            "type": "user_evaluation",
            "thread_id": thread_id,
            "rating": "down",
            "target_node": target_node,
        })

        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "down",
            "feedback": body.feedback or "thumbs_down",
        })

    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "messages": messages,
        "status": "active",
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    return {
        "status": "active",
        "messages": cached_thread.get("messages", []),
        "pending_copy": cached_thread.get("pending_copy"),
    }


@router.post("/chat/threads/{thread_id}/regenerate", response_model=ThreadActionResponse)
async def regenerate_copy(thread_id: str, body: RegenerateRequest):
    """Regenerate copy from latest thread context with optional user instruction."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    payload = await _regenerate_impl(thread_id, body)
    return payload


@router.post("/chat/threads/{thread_id}/regenerate/stream")
def regenerate_copy_stream(thread_id: str, body: RegenerateRequest):
    """Stream regeneration via SSE."""
    if not _graph or not _middleware:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    return StreamingResponse(
        _sse_regenerate(thread_id, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _regenerate_impl(thread_id: str, body: RegenerateRequest) -> dict:
    """Core regenerate logic, shared by sync and streaming endpoints."""
    config = {"configurable": {"thread_id": thread_id, "user_id": body.user_id}}
    instruction = (body.instruction or "Bitte erstelle eine neue Variante mit anderem Hook und CTA.").strip()

    initial_state = {
        "messages": [],
        "user_id": body.user_id,
        "thread_id": thread_id,
        "approval_status": None,
        "human_feedback": instruction,
        "product_skus": [],
        "trend_insights": "",
        "meme_references": [],
        "draft_copy_de": "",
        "copy_metadata": {},
        "publication_result": None,
        "seo_title": "",
        "seo_keywords": [],
        "seo_description": "",
        "meta_description": "",
        "url_slug": "",
        "alt_text": "",
        "brand_rules": {},
        "ltm_context": [],
        "_analytics_log": [],
        "_current_node": "",
    }

    if _middleware:
        initial_state = _middleware.before_agent(initial_state, config)

    try:
        result = await _graph.ainvoke(initial_state, config=config)
    except Exception as exc:
        logger.error("regenerate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Regenerate failed: {str(exc)}")

    if _middleware:
        result = _middleware.after_agent(result, config)

    state_snapshot = await _graph.aget_state(config)
    snap_values = state_snapshot.values if state_snapshot else result

    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
        "id": thread_id,
        "user_id": body.user_id,
        "title": None,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "status": "active",
        "messages": [],
    }
    thread = _thread_cache_set(thread)
    payload = _build_thread_payload(thread, snap_values)

    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "status": payload["status"],
        "messages": payload["messages"],
        "title": _derive_thread_title_from_messages(payload["messages"]),
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    return {
        "status": cached_thread["status"],
        "messages": cached_thread["messages"],
        "pending_copy": payload["pending_copy"],
        "title": cached_thread["title"],
    }


async def _sse_regenerate(thread_id: str, body: RegenerateRequest):
    try:
        config = {"configurable": {"thread_id": thread_id, "user_id": body.user_id}}
        instruction = (body.instruction or "Bitte erstelle eine neue Variante mit anderem Hook und CTA.").strip()

        initial_state = {
            "messages": [],
            "user_id": body.user_id,
            "thread_id": thread_id,
            "approval_status": None,
            "human_feedback": instruction,
            "product_skus": [],
            "trend_insights": "",
            "meme_references": [],
            "draft_copy_de": "",
            "copy_metadata": {},
            "publication_result": None,
            "seo_title": "",
            "seo_keywords": [],
            "seo_description": "",
            "meta_description": "",
            "url_slug": "",
            "alt_text": "",
            "brand_rules": {},
            "ltm_context": [],
            "_analytics_log": [],
            "_current_node": "",
        }

        if _middleware:
            initial_state = _middleware.before_agent(initial_state, config)

        # Fetch URL content from the instruction and add to graph context
        urls = _extract_urls(instruction)
        url_texts: list[str] = list(initial_state.get("product_context") or [])
        for url in urls:
            url_text = _fetch_url_text(url)
            if url_text:
                url_texts.append(f"[URL] {url}\n{url_text}")
        if url_texts:
            initial_state["product_context"] = url_texts

        yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})

        usage_tracker = UsageTracker()
        stream_chunks: list[Any] = []

        async for event in _graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            if kind == "on_chat_model_stream" and name == "ChatOpenAI":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content"):
                    stream_chunks.append(chunk)
                    token = chunk.content
                    if isinstance(token, str) and token:
                        yield _encode_sse("delta", {"text": token})

            if kind == "on_llm_end" and name == "ChatOpenAI":
                output = event.get("data", {}).get("output")
                if output:
                    usage = extract_usage_from_llm_output(output)
                    if usage:
                        model = getattr(output, "response_metadata", {}).get("model", "") or ""
                        usage_tracker.add(
                            model=model,
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                        )

        # Fallback: if no usage was tracked via on_llm_end, try accumulated chunks
        if not usage_tracker._calls and stream_chunks:
            from backend.tracking.usage import extract_usage_from_chunks, UsageInfo
            chunk_usage = extract_usage_from_chunks(stream_chunks)
            if chunk_usage:
                model_name = "openai/gpt-5-mini"
                u = UsageInfo(model=model_name, input_tokens=chunk_usage.get("input_tokens", 0), output_tokens=chunk_usage.get("output_tokens", 0))
                usage_tracker.add(model=model_name, input_tokens=u.input_tokens, output_tokens=u.output_tokens)

        state_snapshot = await _graph.aget_state(config)
        snap_values = state_snapshot.values if state_snapshot else {}

        if _middleware:
            snap_values = _middleware.after_agent(snap_values, config)

        thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
            "id": thread_id,
            "user_id": body.user_id,
            "title": None,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "status": "active",
            "messages": [],
        }
        thread = _thread_cache_set(thread)
        payload = _build_thread_payload(thread, snap_values)

        # Attach aggregate token usage and RAG trace to the last assistant message
        usage_dict = usage_tracker.to_dict() if usage_tracker._calls else None
        middleware_trace = get_rag_trace()
        state_trace = snap_values.get("_rag_trace", [])
        if state_trace and middleware_trace:
            if len(middleware_trace) >= 2:
                rag_trace_data = middleware_trace[:-1] + state_trace + middleware_trace[-1:]
            else:
                rag_trace_data = middleware_trace + state_trace
        elif state_trace:
            rag_trace_data = state_trace
        else:
            rag_trace_data = middleware_trace
        for msg in reversed(payload.get("messages", [])):
            if msg.get("role") == "assistant":
                if usage_dict:
                    msg["usage"] = usage_dict
                if rag_trace_data:
                    msg["rag_trace"] = rag_trace_data
                seo = {
                    k: snap_values.get(k)
                    for k in ("seo_title", "seo_keywords", "seo_description", "meta_description", "url_slug", "alt_text")
                    if snap_values.get(k)
                }
                if seo:
                    msg["seo_metadata"] = seo
                suggestions = _compute_suggestions(snap_values)
                if suggestions:
                    msg["suggestions"] = suggestions
                break

        now = _utc_now_iso()
        cached_thread = _thread_cache_update(thread_id, {
            "status": payload["status"],
            "messages": payload["messages"],
            "title": _derive_thread_title_from_messages(payload["messages"]),
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "title": cached_thread.get("title"),
            "status": payload["status"],
            "messages": payload["messages"],
            "pending_copy": payload["pending_copy"],
        })
    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("stream regenerate failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


# ── Long-Term Memory ──

@router.get("/memory/{user_id}", response_model=MemoryListResponse)
def list_memories(user_id: str):
    """List all long-term memories for a user."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")

    items = _memory.list_memories(user_id)
    return MemoryListResponse(
        user_id=user_id,
        total=len(items),
        items=[MemoryItem(id=m["id"], text=m["text"]) for m in items],
    )


@router.delete("/memory/{user_id}/{doc_id}", response_model=DeleteMemoryResponse)
def delete_memory(user_id: str, doc_id: str):
    """Delete a specific memory."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    _memory.delete_memory(user_id, doc_id)
    return {"status": "deleted", "doc_id": doc_id}


@router.get("/memory/{user_id}/brand-rules", response_model=BrandRuleResponse)
def get_brand_rules(user_id: str):
    """Get all brand style rules for a user."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    rules = _memory.get_brand_rules(user_id)
    return {"rules": rules}


@router.post("/memory/{user_id}/brand-rules", response_model=BrandRuleSaveResponse)
def save_brand_rule(user_id: str, body: BrandRuleSaveRequest):
    """Save or update a brand style rule."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    _memory.save_brand_rule(user_id, body.key, body.value)
    return {"status": "saved", "key": body.key}


# ── Product Catalog (RAG ingestion) ──

@router.post("/catalog/products/bulk", response_model=ProductCatalogBulkLoadResponse)
def bulk_load_product_catalog(body: ProductCatalogBulkLoadRequest):
    """Bulk load product documents into the global product catalog vector store."""
    if not body.items:
        return {"loaded": 0}

    items = [
        {
            "id": item.id,
            "text": item.text,
            "metadata": item.metadata,
        }
        for item in body.items
    ]
    loaded = load_products_to_catalog(items)
    return {"loaded": loaded}
