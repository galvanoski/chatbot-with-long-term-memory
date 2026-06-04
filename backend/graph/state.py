import operator
from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class AgentState(TypedDict):
    """Global state for the marketing agent pipeline."""

    # ── Core conversation ──
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    thread_id: str

    # ── Research node output ──
    product_skus: list[str]
    product_context: list[str]
    trend_insights: str
    meme_references: list[str]

    # ── Copywriter node output ──
    draft_copy_de: str
    copy_metadata: dict

    # ── HITL (Human-in-the-Loop) ──
    approval_status: Optional[str]    # None | "pending" | "approved" | "rejected"
    human_feedback: Optional[str]

    # ── Publisher node output ──
    publication_result: Optional[dict]

    # ── Long-term memory context (set by middleware.before_agent) ──
    brand_rules: dict
    ltm_context: list[str]

    # ── Analytics (internal, stripped before response) ──
    _analytics_log: list[dict]
    _current_node: str
