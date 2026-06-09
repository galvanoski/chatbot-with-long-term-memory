import datetime as dt
import asyncio
import json
import logging
import os
import sqlite3
import threading
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
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
    ImagePromptRequest,
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
from backend.middleware.geekcat import GeekCatMiddleware

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
        thread = dict(_threads.get(thread_id, {"id": thread_id}))
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

        yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})

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
                    token = chunk.content
                    if isinstance(token, str) and token:
                        yield _encode_sse("delta", {"text": token})

        state_snapshot = await _graph.aget_state(config)
        snap_values = state_snapshot.values if state_snapshot else {}

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


async def _sse_generate_image_prompt(thread_id: str, body: ImagePromptRequest):
    try:
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": body.user_id,
            }
        }

        initial_state = _middleware.before_agent(
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

        async for event in _graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")
            metadata = event.get("metadata") or {}
            node_name = str(metadata.get("langgraph_node") or "")

            if kind == "on_chat_model_stream" and name == "ChatOpenAI":
                if node_name and node_name != "image_prompt_generator":
                    continue
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content"):
                    token = chunk.content
                    if isinstance(token, str) and token:
                        yield _encode_sse("delta", {"text": token})

        state_snapshot = await _graph.aget_state(config)
        snap_values = state_snapshot.values if state_snapshot else {}
        raw_result = (snap_values.get("image_prompt_result") or "").strip()

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
        msg_id = str(uuid.uuid4())
        content = f"🎨 **Image Prompt:**\n\n{raw_result}" if raw_result else "Could not generate image prompt."
        assistant_msg = {"id": msg_id, "role": "assistant", "content": content, "created_at": now}
        thread["messages"] = list(thread.get("messages", [])) + [assistant_msg]

        cached_thread = _thread_cache_update(thread_id, {
            "messages": thread["messages"],
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "status": "active",
            "messages": cached_thread.get("messages", []),
            "pending_copy": cached_thread.get("pending_copy"),
        })
    except HTTPException as exc:
        yield _encode_sse("error", {"detail": exc.detail, "status_code": exc.status_code})
    except Exception as exc:
        logger.exception("generate image prompt failed")
        yield _encode_sse("error", {"detail": str(exc), "status_code": 500})


def _extract_messages(state_values: dict) -> list[dict]:
    """Convert LangGraph AnyMessage list to frontend Message dicts.

    Filters out internal messages: tool results and regeneration prompts.
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
    return msgs


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
        if signature in seen_signatures and str(message.get("role") or "") == "user":
            # Prevent repeated user prompts from being re-appended by compacted graph windows.
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
    cached_thread = _thread_cache_update(thread_id, {"title": payload_title})
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
    """Save a positive evaluation (thumbs-up) as a retrievable memory."""
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    copy_text = (body.edited_copy or "").strip()
    if not copy_text:
        messages = thread.get("messages", [])
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

        text_to_save = copy_text or "thumbs_up"
        _memory.ltm.save(body.user_id, f"Positive Bewertung: {text_to_save}", {
            "type": "user_evaluation",
            "thread_id": thread_id,
            "rating": "up",
        })

        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "up",
            "feedback": body.feedback or "thumbs_up",
            "edited": bool(body.edited_copy or body.edited_parts),
        })

    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
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
    """Save a negative evaluation (thumbs-down) as a retrievable memory."""
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

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
            messages = thread.get("messages", [])
            if messages:
                last_assistant = [m for m in messages if m.get("role") == "assistant"]
                if last_assistant:
                    copy_text = last_assistant[-1].get("content", "").strip()

        _memory.ltm.save(body.user_id, f"Negative Bewertung: {copy_text or 'thumbs_down'}", {
            "type": "user_evaluation",
            "thread_id": thread_id,
            "rating": "down",
        })

        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "down",
            "feedback": body.feedback or "thumbs_down",
        })

    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
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
            "brand_rules": {},
            "ltm_context": [],
            "_analytics_log": [],
            "_current_node": "",
        }

        if _middleware:
            initial_state = _middleware.before_agent(initial_state, config)

        yield _encode_sse("start", {"status": "active", "title": None, "pending_copy": None})

        async for event in _graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            if kind == "on_chat_model_stream" and name == "ChatOpenAI":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content"):
                    token = chunk.content
                    if isinstance(token, str) and token:
                        yield _encode_sse("delta", {"text": token})

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

        now = _utc_now_iso()
        cached_thread = _thread_cache_update(thread_id, {
            "status": payload["status"],
            "messages": payload["messages"],
            "title": _derive_thread_title_from_messages(payload["messages"]),
            "updated_at": now,
        })
        _save_thread_record(cached_thread)

        yield _encode_sse("done", {
            "status": cached_thread["status"],
            "messages": cached_thread["messages"],
            "pending_copy": payload["pending_copy"],
            "title": cached_thread["title"],
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
