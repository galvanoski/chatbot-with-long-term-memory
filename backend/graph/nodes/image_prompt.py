import logging
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState

logger = logging.getLogger("geekcat.nodes.image_prompt")

IMAGE_PROMPT_SYSTEM_PROMPT = (
    "You are a creative image prompt generator for 'The Geek Cat' brand \u2014 "
    "a German print-on-demand store selling sarcastic IT/cat-themed merchandise. "
    "Generate a detailed image generation prompt suitable for Midjourney / DALL-E / Stable Diffusion. "
    "The prompt must be in English, describe a logo or illustration style, include visual details "
    "(colors, composition, style), and fit on merchandise (t-shirts, mugs, stickers). "
    "Output ONLY the prompt text, no explanations, no markdown."
)


def image_prompt_node(state: AgentState, mw=None) -> dict:
    instruction = (state.get("image_prompt_instruction") or "").strip()
    if not instruction:
        instruction = "a cat programmer logo"

    logger.info("image_prompt_node: generating prompt for '%s'", instruction[:60])

    llm = ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    messages = [
        SystemMessage(content=IMAGE_PROMPT_SYSTEM_PROMPT),
        HumanMessage(content=instruction),
    ]

    _rag_trace: list[dict] = []

    if mw:
        context_start = time.time()
        messages = mw.before_model(messages, state)
        context_latency = (time.time() - context_start) * 1000
        context_s = state.get("brand_rules", {})
        _rag_trace.append({
            "stage": "context_inject",
            "latency_ms": round(context_latency, 1),
            "ltm_docs": len(state.get("ltm_context", [])),
            "brand_rules": list(context_s.keys()),
            "product_skus": state.get("product_skus", []),
        })

    llm_start = time.time()
    response = llm.invoke(messages)
    llm_latency = (time.time() - llm_start) * 1000
    usage = {}
    if hasattr(response, "usage_metadata"):
        usage = response.usage_metadata or {}
    _rag_trace.append({
        "stage": "llm_generate",
        "latency_ms": round(llm_latency, 1),
        "node": "image_prompt_generator",
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    })

    if mw:
        response = mw.after_model(response, state)

    validation = getattr(response, "_validation", {})
    _rag_trace.append({
        "stage": "model_output",
        "has_german": validation.get("has_german", False),
        "hashtag_count": validation.get("hashtag_count", 0),
        "char_count": validation.get("char_count", 0),
        "validation": validation,
    })

    content = (response.content if hasattr(response, "content") else str(response)).strip()

    return {
        "image_prompt_result": content,
        "_current_node": "image_prompt_generator",
        "_rag_trace": _rag_trace,
    }
