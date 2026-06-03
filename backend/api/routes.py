import datetime as dt
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph

from backend.api.schemas import (
    ApprovalRequest,
    BrandRuleSaveRequest,
    MemoryItem,
    MemoryListResponse,
    MessageSendRequest,
    ThreadCreateRequest,
)
from backend.memory.manager import MemoryManager
from backend.middleware.geekcat import GeekCatMiddleware

logger = logging.getLogger("geekcat.api")

# Global graph instance — set in main.py
_graph: CompiledStateGraph | None = None
_middleware: GeekCatMiddleware | None = None
_memory: MemoryManager | None = None

# In-memory thread store (keyed by thread_id)
_threads: dict[str, dict] = {}


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
            "created_at": dt.datetime.utcnow().isoformat(),
        })
    return msgs

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

@router.get("/chat/threads")
def list_threads(user_id: str):
    """List all chat threads for a user."""
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
            "status": t["status"],
            "message_count": len(t["messages"]),
        }
        for t in _threads.values()
        if t["user_id"] == user_id
    ]


@router.post("/chat/threads")
def create_thread(body: ThreadCreateRequest):
    """Create a new chat thread for a user."""
    thread_id = str(uuid.uuid4())
    now = dt.datetime.utcnow().isoformat()
    thread = {
        "id": thread_id,
        "user_id": body.user_id,
        "title": None,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "messages": [],
    }
    _threads[thread_id] = thread
    logger.info("create_thread: user=%s thread=%s", body.user_id, thread_id)
    return thread


@router.get("/chat/threads/{thread_id}")
def get_thread(thread_id: str, user_id: str = ""):
    """Get a specific thread with its messages from graph state."""
    thread = _threads.get(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    # Pull latest messages from graph state when available
    if _graph:
        try:
            config = {"configurable": {"thread_id": thread_id}}
            state = _graph.get_state(config)
            if state and state.values:
                thread = {**thread, "messages": _extract_messages(state.values)}
        except Exception:
            pass
    return thread


@router.post("/chat/threads/{thread_id}/messages")
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

    draft_copy = result.get("draft_copy_de") or snap_values.get("draft_copy_de", "")
    approval_status = result.get("approval_status") or snap_values.get("approval_status")
    metadata = result.get("copy_metadata") or snap_values.get("copy_metadata") or {}

    # Determine status for frontend
    if approval_status == "approved":
        status = "published"
    elif draft_copy and approval_status != "approved":
        status = "awaiting_approval"
    else:
        status = "active"

    # Build messages list: user message + assistant reply when draft exists
    messages = _extract_messages(snap_values)
    if draft_copy and not any(m["role"] == "assistant" for m in messages):
        messages.append({
            "id": str(len(messages)),
            "role": "assistant",
            "content": draft_copy,
            "created_at": dt.datetime.utcnow().isoformat(),
        })

    # Build pending_copy when awaiting approval
    pending_copy = None
    if status == "awaiting_approval" and draft_copy:
        pending_copy = {
            "content": draft_copy,
            "hashtags": metadata.get("hashtags", []),
            "product_name": metadata.get("product_name"),
            "product_url": metadata.get("product_url"),
        }

    # Update in-memory thread store
    now = dt.datetime.utcnow().isoformat()
    if thread_id in _threads:
        _threads[thread_id]["messages"] = messages
        _threads[thread_id]["status"] = status
        _threads[thread_id]["updated_at"] = now

    return {"status": status, "messages": messages, "pending_copy": pending_copy}


@router.get("/chat/threads/{thread_id}/state")
def get_thread_state(thread_id: str):
    """Get the current state of a thread (for polling HITL status)."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = _graph.get_state(config)
    except Exception:
        raise HTTPException(status_code=404, detail="Thread not found")

    return {
        "thread_id": thread_id,
        "approval_status": state.values.get("approval_status"),
        "draft_copy_de": state.values.get("draft_copy_de"),
        "next_nodes": list(state.next) if state.next else [],
        "is_interrupted": "publisher" in (state.next or []),
    }


@router.post("/chat/threads/{thread_id}/approve")
def approve_copy(thread_id: str, body: ApprovalRequest):
    """Approve the generated copy and resume the graph to publisher node."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}

    # Update state with approval
    _graph.update_state(config, {"approval_status": "approved"})

    # Resume graph execution
    try:
        result = _graph.invoke(None, config=config)
    except Exception as exc:
        logger.error("resume after approve failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Resume failed: {str(exc)}")

    messages = _extract_messages(result)

    # Update thread store
    now = dt.datetime.utcnow().isoformat()
    if thread_id in _threads:
        _threads[thread_id]["status"] = "published"
        _threads[thread_id]["messages"] = messages
        _threads[thread_id]["updated_at"] = now

    return {"status": "published", "messages": messages}


@router.post("/chat/threads/{thread_id}/reject")
def reject_copy(thread_id: str, body: ApprovalRequest):
    """Reject the generated copy with optional feedback."""
    if not _graph:
        raise HTTPException(status_code=503, detail="Graph not initialised")

    config = {"configurable": {"thread_id": thread_id}}
    _graph.update_state(config, {
        "approval_status": "rejected",
        "human_feedback": body.feedback,
    })

    logger.info("copy rejected: thread=%s feedback=%s", thread_id, body.feedback)

    # Update thread store
    now = dt.datetime.utcnow().isoformat()
    if thread_id in _threads:
        _threads[thread_id]["status"] = "active"
        _threads[thread_id]["updated_at"] = now

    return {"status": "rejected", "messages": _threads.get(thread_id, {}).get("messages", [])}


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


@router.delete("/memory/{user_id}/{doc_id}")
def delete_memory(user_id: str, doc_id: str):
    """Delete a specific memory."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    _memory.delete_memory(user_id, doc_id)
    return {"status": "deleted", "doc_id": doc_id}


@router.get("/memory/{user_id}/brand-rules")
def get_brand_rules(user_id: str):
    """Get all brand style rules for a user."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    rules = _memory.get_brand_rules(user_id)
    return {"rules": rules}


@router.post("/memory/{user_id}/brand-rules")
def save_brand_rule(user_id: str, body: BrandRuleSaveRequest):
    """Save or update a brand style rule."""
    if not _memory:
        raise HTTPException(status_code=503, detail="Memory not initialised")
    _memory.save_brand_rule(user_id, body.key, body.value)
    return {"status": "saved", "key": body.key}
