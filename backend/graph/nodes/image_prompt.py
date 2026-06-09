import logging
import os

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

    if mw:
        messages = mw.before_model(messages, state)

    response = llm.invoke(messages)

    if mw:
        response = mw.after_model(response, state)

    content = (response.content if hasattr(response, "content") else str(response)).strip()

    return {
        "image_prompt_result": content,
        "_current_node": "image_prompt_generator",
    }
