import logging

from backend.graph.state import AgentState
from backend.graph.tools.rag import query_product_catalog, query_meme_repository

logger = logging.getLogger("geekcat.nodes.research")


def research_node(state: AgentState) -> dict:
    """Research node: queries RAG for relevant products, trends, and memes
    based on the latest user message context."""
    user_message = state["messages"][-1].content if state["messages"] else ""
    logger.info("research_node: query=%s", user_message[:80])

    # Query RAG for relevant products and memes
    products = query_product_catalog(user_message, top_k=3)
    memes = query_meme_repository(user_message, top_k=2)

    # Trend insights (could call an external trends API)
    trend_insights = (
        "Current IT trends: AI agents adoption, Rust ecosystem growth, "
        "Web3 infrastructure maturation, Linux kernel drama, "
        "cloud cost optimization, edge computing expansion."
    )

    return {
        "product_skus": [p.get("metadata", {}).get("sku", p["id"]) for p in products],
        "trend_insights": trend_insights,
        "meme_references": [m["text"] for m in memes],
        "_current_node": "research",
    }
