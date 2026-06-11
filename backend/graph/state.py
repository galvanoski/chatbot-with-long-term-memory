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
    human_feedback: Optional[str]     # Legacy, kept for backward compat
    human_feedback_copywriter: Optional[str]
    human_feedback_image_prompt_generator: Optional[str]
    human_feedback_image_generator: Optional[str]
    human_feedback_seo_generator: Optional[str]

    # ── Publisher node output ──
    publication_result: Optional[dict]

    # ── SEO generator node output ──
    seo_title: str
    seo_keywords: list[str]
    seo_description: str
    meta_description: str
    url_slug: str
    alt_text: str

    # ── Image prompt generator ──
    image_prompt_instruction: Optional[str]
    image_prompt_result: Optional[str]

    # ── Image generator ──
    image_url: Optional[str]
    image_generation_requested: Optional[bool]

    # ── Copywriter trigger (after SEO/image flow) ──
    run_copywriter_requested: Optional[bool]

    # ── Flow tracking (set by middleware) ──
    _entry_node: Optional[str]

    # ── Long-term memory context (set by middleware.before_agent) ──
    brand_rules: dict
    ltm_context: list[str]

    # ── RAG trace (accumulated across nodes) ──
    _rag_trace: Annotated[list[dict], operator.add]

    # ── Analytics (internal, stripped before response) ──
    _analytics_log: list[dict]
    _current_node: str
