import logging
import os
import time

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState

logger = logging.getLogger("geekcat.nodes.seo")

SEO_SYSTEM_PROMPT = (
    "You are an expert SEO copywriter specializing in Rank Math optimization for e-commerce product pages. "
    "Your goal is to achieve a perfect 100/100 Rank Math score.\n\n"
    "RANK MATH 100/100 CRITERIA:\n"
    "1. Focus Keyword: Choose ONE primary keyword and 2-3 secondary keywords. The primary keyword must appear in:\n"
    "   - SEO Title\n"
    "   - Meta Description\n"
    "   - First paragraph of the description\n"
    "   - At least one heading\n"
    "2. SEO Title: Max 60 characters. Include the primary keyword near the beginning. Must be compelling for clicks.\n"
    "3. Meta Description: Around 155-160 characters. Include primary keyword naturally + a call to action.\n"
    "4. URL Slug: Derived from the primary keyword, lowercase with hyphens.\n"
    "5. Content: The SEO description must be 600+ words, well-structured with headings, readable (short paragraphs, clear language).\n"
    "6. Images: Suggest an alt text for the product hero image that includes the primary keyword.\n"
    "7. Internal/External links: The description should naturally reference related content.\n\n"
    "OUTPUT FORMAT — Return ONLY valid JSON with this exact schema:\n"
    "{\n"
    "  \"seo_title\": \"string (max 60 chars, includes primary keyword)\",\n"
    "  \"focus_keyword\": \"string (primary keyword)\",\n"
    "  \"secondary_keywords\": [\"string\", \"string\", \"string\"],\n"
    "  \"meta_description\": \"string (around 155-160 chars)\",\n"
    "  \"seo_description\": \"string (600+ words, SEO-optimized product description — start with `##` followed by the actual product name as the main heading, then write paragraphs; use `##` for each subsection heading too; plain text only, no other formatting)\",\n"
    "  \"url_slug\": \"string (lowercase with hyphens)\",\n"
    "  \"alt_text\": \"string (product image alt text with primary keyword)\",\n"
    "  \"score_estimation\": \"string (estimated Rank Math score based on criteria fulfilled)\"\n"
    "}\n"
    "No markdown, no extra text. Return ONLY the JSON object."
)


def seo_node(state: AgentState) -> dict:
    """SEO node: generates Rank Math-optimized SEO metadata for the product."""
    product_skus = state.get("product_skus", [])
    product_context = state.get("product_context", [])
    brand_rules = state.get("brand_rules", {})
    draft_copy = state.get("draft_copy_de", "")

    if not product_skus and not product_context:
        logger.warning("seo_node: no product data available, skipping")
        return {"_current_node": "seo_generator", "_rag_trace": []}

    logger.info("seo_node: generating SEO for SKUs=%s", product_skus)

    _rag_trace: list[dict] = []

    context_parts = []
    if product_skus:
        context_parts.append("Product SKUs: " + ", ".join(product_skus))
    if product_context:
        context_parts.append("Product context:\n" + "\n\n".join(product_context[:3]))
    if brand_rules:
        rules_text = "\n".join(f"- {k}: {v}" for k, v in brand_rules.items())
        context_parts.append("Brand rules:\n" + rules_text)
    if draft_copy:
        context_parts.append("Generated marketing copy (use as reference for tone):\n" + draft_copy[:500])

    user_prompt = "Generate Rank Math-optimized SEO metadata for the following product:\n\n" + "\n\n".join(context_parts)

    llm = ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    llm_start = time.time()
    response = llm.invoke([
        SystemMessage(content=SEO_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ])
    llm_latency = (time.time() - llm_start) * 1000

    import json
    import re

    content = response.content if hasattr(response, "content") else str(response)
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        payload = json.loads(match.group(0)) if match else {}

    seo_title = str(payload.get("seo_title") or "").strip()
    focus_keyword = str(payload.get("focus_keyword") or "").strip()
    secondary_keywords = list(payload.get("secondary_keywords") or [])
    meta_description = str(payload.get("meta_description") or "").strip()
    seo_description = str(payload.get("seo_description") or "").strip()
    url_slug = str(payload.get("url_slug") or "").strip()
    alt_text = str(payload.get("alt_text") or "").strip()

    usage = {}
    if hasattr(response, "usage_metadata"):
        usage = response.usage_metadata or {}

    _rag_trace.append({
        "stage": "seo_generate",
        "latency_ms": round(llm_latency, 1),
        "node": "seo_generator",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "seo_title": seo_title[:60],
        "focus_keyword": focus_keyword,
        "secondary_keywords": secondary_keywords,
        "meta_description": meta_description[:200],
        "url_slug": url_slug,
        "alt_text": alt_text[:200],
    })

    return {
        "seo_title": seo_title,
        "seo_keywords": [focus_keyword] + secondary_keywords,
        "seo_description": seo_description,
        "meta_description": meta_description,
        "url_slug": url_slug,
        "alt_text": alt_text,
        "_current_node": "seo_generator",
        "_rag_trace": _rag_trace,
    }
