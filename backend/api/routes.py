import datetime as dt
import logging
import json
import sqlite3
import threading
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from backend.api.schemas import (
    ApprovalRequest,
    BrandRuleResponse,
    BrandRuleSaveRequest,
    BrandRuleSaveResponse,
    DeleteMemoryResponse,
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
            existing_columns = {
                row[1]
                for row in _thread_db_conn.execute("PRAGMA table_info(chat_threads)").fetchall()
            }
            if "messages_json" not in existing_columns:
                _thread_db_conn.execute(
                    "ALTER TABLE chat_threads ADD COLUMN messages_json TEXT NOT NULL DEFAULT '[]'"
                )
            _thread_db_conn.commit()
        return _thread_db_conn


def _serialize_messages(messages: list[dict] | None) -> str:
    return json.dumps(messages or [], ensure_ascii=False)


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
            _serialize_messages(thread.get("messages", [])),
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


def _list_thread_records(user_id: str) -> list[dict]:
    conn = _get_thread_db_conn()
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
    conn.commit()

    with _threads_lock:
        for thread_id, thread in list(_threads.items()):
            if thread.get("user_id") == source_user_id:
                updated_thread = dict(thread)
                updated_thread["user_id"] = user_id
                _threads[thread_id] = updated_thread

    logger.info("migrated chat threads from %s to %s", source_user_id, user_id)
    return True


def _extract_messages(state_values: dict) -> list[dict]:
    """Convert LangGraph AnyMessage list to frontend Message dicts."""
    msgs = []
    for i, m in enumerate(state_values.get("messages", [])):
        role = "user" if getattr(m, "type", "") in ("human", "user") else "assistant"
        content = m.content if isinstance(m.content, str) else str(m.content)
        msgs.append({
            "id": str(i),
            "role": role,
            "content": content,
            "created_at": _utc_now_iso(),
        })
    return msgs


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

    messages = thread.get("messages", [])
    if values:
        messages = _extract_messages(values)
        if draft_copy and status == "awaiting_approval":
            # Keep a single canonical assistant draft at the end to avoid chatty duplicates.
            while messages and messages[-1].get("role") == "assistant":
                messages.pop()

            messages.append({
                "id": str(len(messages)),
                "role": "assistant",
                "content": draft_copy,
                "created_at": _utc_now_iso(),
            })
        elif draft_copy and not any(m["role"] == "assistant" for m in messages):
            messages.append({
                "id": str(len(messages)),
                "role": "assistant",
                "content": draft_copy,
                "created_at": _utc_now_iso(),
            })

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
def list_threads(user_id: str):
    """List all chat threads for a user."""
    records = _list_thread_records(user_id)
    if not records and _migrate_single_owner_threads_to_user_id(user_id):
        records = _list_thread_records(user_id)

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
                    state = _graph.get_state({"configurable": {"thread_id": record["id"]}})
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
def get_thread(thread_id: str, user_id: str = ""):
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
            state = _graph.get_state(config)
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
def send_message(thread_id: str, body: MessageSendRequest):
    """Send a user message to the agent and execute the marketing pipeline."""
    if not _graph or not _middleware:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    try:
     return _send_message_impl(thread_id, body)
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        logger.error("send_message unhandled error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def _send_message_impl(thread_id: str, body: MessageSendRequest):
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
            "approval_status": None,
            "product_skus": [],
            "trend_insights": "",
            "meme_references": [],
            "draft_copy_de": "",
            "copy_metadata": {},
            "human_feedback": None,
            "publication_result": None,
            "brand_rules": {},
            "ltm_context": [],
            "_analytics_log": [],
            "_current_node": "",
        },
        config,
    )

    # ── Graph invocation ──
    try:
        result = _graph.invoke(initial_state, config=config)
    except Exception as exc:
        logger.error("graph.invoke failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {str(exc)}")

    # ── Hook 6: after_agent ──
    result = _middleware.after_agent(result, config)

    # Check if paused at HITL
    state_snapshot = _graph.get_state(config)
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
def get_thread_state(thread_id: str, user_id: str = ""):
    """Get the current state of a thread (for polling HITL status)."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = _graph.get_state(config)
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
def approve_copy(thread_id: str, body: ApprovalRequest):
    """Approve the generated copy and resume the graph to publisher node."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}

    if body.edited_parts or body.edited_copy:
        state_snapshot = _graph.get_state(config)
        values = state_snapshot.values if state_snapshot else {}
        copy_metadata = dict(values.get("copy_metadata") or {})
        hashtags = copy_metadata.get("hashtags", [])

        edited_parts = body.edited_parts or {}
        next_copy = body.edited_copy or _compose_copy_from_parts(edited_parts, hashtags)

        if edited_parts:
            copy_metadata["parts"] = {
                "hook": edited_parts.get("hook", ""),
                "body": edited_parts.get("body", ""),
                "cta": edited_parts.get("cta", ""),
            }
        copy_metadata["char_count"] = len(next_copy)

        _graph.update_state(config, {
            "draft_copy_de": next_copy,
            "copy_metadata": copy_metadata,
        })

    # Update state with approval
    _graph.update_state(config, {"approval_status": "approved"})

    # Resume graph execution
    try:
        result = _graph.invoke(None, config=config)
    except Exception as exc:
        logger.error("resume after approve failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(exc)}")

    messages = _extract_messages(result)
    thread = _thread_cache_get(thread_id) or _load_thread_record(thread_id, body.user_id) or {
        "id": thread_id,
        "user_id": body.user_id,
        "title": None,
        "created_at": _utc_now_iso(),
        "updated_at": _utc_now_iso(),
        "status": "published",
        "messages": [],
    }
    thread = _thread_cache_set(thread)
    if not messages:
        messages = thread.get("messages", [])

    # Update thread store
    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "status": "published",
        "messages": messages,
        "title": _derive_thread_title_from_messages(messages),
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    if _memory:
        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "up",
            "feedback": body.feedback or "thumbs_up",
            "edited": bool(body.edited_copy or body.edited_parts),
        })

    return {"status": "published", "messages": messages}


@router.post("/chat/threads/{thread_id}/reject", response_model=ThreadActionResponse)
def reject_copy(thread_id: str, body: ApprovalRequest):
    """Reject the generated copy with optional feedback."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}
    _graph.update_state(config, {
        "approval_status": "rejected",
        "human_feedback": body.feedback,
    })

    feedback_text = (body.feedback or "Bitte verfeinere den Text und behebe die genannten Probleme.").strip()
    regeneration_prompt = f"Bitte ueberarbeite den Copy basierend auf diesem Feedback: {feedback_text}"

    # Start a fresh generation pass that carries explicit human feedback context.
    initial_state = {
        "messages": [HumanMessage(content=regeneration_prompt)],
        "user_id": body.user_id,
        "thread_id": thread_id,
        "approval_status": None,
        "human_feedback": feedback_text,
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
        result = _graph.invoke(initial_state, config=config)
    except Exception as exc:
        logger.error("resume after reject failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(exc)}")

    if _middleware:
        result = _middleware.after_agent(result, config)

    state_snapshot = _graph.get_state(config)
    snap_values = state_snapshot.values if state_snapshot else result

    logger.info("copy rejected: thread=%s feedback=%s", thread_id, body.feedback)
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

    # Update thread store
    now = _utc_now_iso()
    cached_thread = _thread_cache_update(thread_id, {
        "status": payload["status"],
        "messages": payload["messages"],
        "title": _derive_thread_title_from_messages(payload["messages"]),
        "updated_at": now,
    })
    _save_thread_record(cached_thread)

    if _memory:
        _memory.save_analytics(body.user_id, "human_feedback", {
            "thread_id": thread_id,
            "rating": "down",
            "feedback": body.feedback or "thumbs_down",
        })

    return {
        "status": cached_thread["status"],
        "messages": cached_thread["messages"],
        "pending_copy": payload["pending_copy"],
        "title": cached_thread["title"],
    }


@router.post("/chat/threads/{thread_id}/regenerate", response_model=ThreadActionResponse)
def regenerate_copy(thread_id: str, body: RegenerateRequest):
    """Regenerate copy from latest thread context with optional user instruction."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id, "user_id": body.user_id}}
    instruction = (body.instruction or "Bitte erstelle eine neue Variante mit anderem Hook und CTA.").strip()

    state_snapshot = _graph.get_state(config)
    values = state_snapshot.values if state_snapshot else {}
    prior_feedback = (values.get("human_feedback") or "").strip()
    prompt = f"Bitte regeneriere den Copy. Zusatzeinweisung: {instruction}"
    if prior_feedback:
        prompt += f" Beruecksichtige auch dieses letzte Feedback: {prior_feedback}"

    initial_state = {
        "messages": [HumanMessage(content=prompt)],
        "user_id": body.user_id,
        "thread_id": thread_id,
        "approval_status": None,
        "human_feedback": prior_feedback or None,
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
        result = _graph.invoke(initial_state, config=config)
    except Exception as exc:
        logger.error("regenerate failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Regenerate failed: {str(exc)}")

    if _middleware:
        result = _middleware.after_agent(result, config)

    state_snapshot = _graph.get_state(config)
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
