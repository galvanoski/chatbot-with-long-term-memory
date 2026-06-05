import logging

from backend.graph.state import AgentState
from backend.graph.tools.rag import query_product_catalog, query_meme_repository

logger = logging.getLogger("geekcat.nodes.research")


def _build_research_query(state: AgentState, max_messages: int = 4) -> str:
    """Build a context-aware research query from the latest conversation turns."""
    rendered: list[str] = []
    for message in state.get("messages", [])[-max_messages:]:
        msg_type = getattr(message, "type", "")
        role = "assistant" if msg_type in ("ai", "assistant") else "user"
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else str(content)
        if text.strip():
            rendered.append(f"{role}: {text.strip()}")
    return "\n".join(rendered).strip()


def research_node(state: AgentState) -> dict:
    """Research node: queries RAG for relevant products, trends, and memes
    based on the latest user message context."""
    user_message = state["messages"][-1].content if state["messages"] else ""
    research_query = _build_research_query(state) or user_message
    logger.info("research_node: query=%s", research_query[:80])

    # Query RAG for relevant products and memes
    products = query_product_catalog(research_query, top_k=3)
    memes = query_meme_repository(research_query, top_k=2)

    product_context = []
    for p in products:
        metadata = p.get("metadata", {}) or {}
        sku = metadata.get("sku", p.get("id", "unknown-sku"))
        name = metadata.get("name", "Unnamed product")
        sarcastic_legend = metadata.get("sarcastic_legend") or metadata.get("tagline") or ""
        audience = metadata.get("audience", "IT pros and cat lovers")
        category = metadata.get("category", "pod")
        base_text = p.get("text", "")

        snippet = (
            f"SKU={sku} | NAME={name} | CATEGORY={category} | AUDIENCE={audience}\n"
            f"SARCASTIC_LEGEND={sarcastic_legend}\n"
            f"PRODUCT_NOTES={base_text}"
        )
        product_context.append(snippet)

    # Trend insights (could call an external trends API)
    trend_insights = (
        "Current IT trends: AI agents adoption, Rust ecosystem growth, "
        "Web3 infrastructure maturation, Linux kernel drama, "
        "cloud cost optimization, edge computing expansion."
    )

    return {
        "product_skus": [p.get("metadata", {}).get("sku", p["id"]) for p in products],
        "product_context": product_context,
        "trend_insights": trend_insights,
        "meme_references": [m["text"] for m in memes],
        "_current_node": "research",
    }
