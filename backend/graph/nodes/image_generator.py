import json
import logging
import os
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState

logger = logging.getLogger("geekcat.nodes.image_generator")

IMAGE_PROMPT_GENERATOR_PROMPT = (
    "You are an expert at crafting image generation prompts for 'The Geek Cat' brand — "
    "a German print-on-demand store selling sarcastic IT/cat-themed merchandise. "
    "Given a marketing post, create an optimized, detailed prompt for generating a product "
    "image suitable for print-on-demand (t-shirts, mugs, stickers). "
    "The prompt must describe a visually appealing illustration, logo, or design. "
    "Output ONLY the prompt text, no markdown, no explanations."
)


_IMAGE_MODELS = [
    ("google/gemini-2.5-flash-image", ["image", "text"]),
    ("google/gemini-3.1-flash-image-preview", ["image", "text"]),
    ("google/gemini-3-pro-image-preview", ["image", "text"]),
]


def _save_base64_image(data_url: str, images_dir: str | None = None) -> str | None:
    """Save a base64 data URL to disk and return an absolute URL.

    Returns None if saving fails (instead of the raw base64 URL) to avoid
    sending multi-megabyte strings over SSE.
    """
    try:
        if not data_url.startswith("data:image/"):
            return None
        header, _, b64data = data_url.partition(",")
        ext = "png"
        if ";" in header:
            ext = header.split(";")[0].split("/")[-1].split("+")[0]
        if not ext or ext == "octet-stream":
            ext = "png"
        import base64
        import uuid
        from pathlib import Path
        raw = base64.b64decode(b64data)
        images_dir = images_dir or str(Path(__file__).resolve().parent.parent.parent / "static" / "images")
        Path(images_dir).mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = Path(images_dir) / filename
        filepath.write_bytes(raw)
        logger.info("_save_base64_image: saved %s (%d bytes)", filename, len(raw))
        api_base = os.environ.get("API_BASE_URL", "http://localhost:8000")
        return f"{api_base}/static/images/{filename}"
    except Exception as exc:
        logger.warning("_save_base64_image failed: %s", exc)
        return None


def _call_openrouter_image(prompt: str, timeout: int = 120) -> tuple[str | None, dict | None]:
    """Call OpenRouter Chat Completions API with an image-generation model.

    Returns (url_or_none, usage_dict_or_none).
    """
    for model, modalities in _IMAGE_MODELS:
        result = _try_image_model(model, modalities, prompt, timeout)
        if result:
            data_url, usage = result
            saved = _save_base64_image(data_url)
            if saved:
                return saved, usage
    return None, None


def _try_image_model(model: str, modalities: list[str], prompt: str, timeout: int) -> tuple[str, dict] | None:
    """Try a single image model and return (data_url, usage) or None."""
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            timeout=timeout,
        )
        raw_response = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"modalities": modalities},
            stream=False,
        )
        # Access raw JSON to preserve non-standard fields like "images"
        body = raw_response.http_response.json()
        logger.info("_try_image_model(%s): status=%s body_keys=%s", model, raw_response.http_response.status_code, list(body.keys()))
        usage = body.get("usage") or {}
        if raw_response.http_response.status_code != 200:
            logger.warning("_try_image_model(%s): error: %s", model, json.dumps(body)[:500])
            return None
        choices = body.get("choices", [])
        if not choices:
            logger.warning("_try_image_model(%s): no choices", model)
            return None
        message = choices[0].get("message", {})
        msg_keys = list(message.keys())
        images = message.get("images", None)
        logger.info("_try_image_model(%s): msg_keys=%s has_images=%s", model, msg_keys, images is not None)
        data_url: str | None = None
        if images:
            img = images[0].get("image_url", {})
            url = img.get("url")
            if url:
                logger.info("_try_image_model(%s): got image len=%s", model, len(url))
                data_url = url
        if not data_url:
            content = message.get("content", "") or ""
            if content and content.startswith("data:"):
                logger.info("_try_image_model(%s): got image in content", model)
                data_url = content
        if data_url:
            return data_url, usage
    except Exception as exc:
        logger.warning("_try_image_model(%s) failed: %s", model, exc)
    return None


def _generate_image_prompt(source_text: str, feedback: str = "") -> str:
    """Silently generate an image prompt from a draft post using GPT-5-mini."""
    try:
        llm = ChatOpenAI(
            model="openai/gpt-5-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        system_content = IMAGE_PROMPT_GENERATOR_PROMPT
        if feedback:
            system_content += (
                "\n\nIMPORTANT USER FEEDBACK (previous image was rejected):\n"
                f'"{feedback}"\n'
                "Use this feedback to improve the new image prompt.\n"
            )
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=f"Generate an image prompt for this post:\n\n{source_text}"),
        ]
        response = llm.invoke(messages)
        return (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception as exc:
        logger.warning("_generate_image_prompt failed: %s", exc)
        return source_text


def image_generator_node(state: AgentState, mw=None) -> dict:
    """Generate a product image using Riverflow V2.5 Pro via OpenRouter.

    - If ``image_prompt_result`` already exists → use it directly.
    - If only ``draft_copy_de`` exists → silently generate an image prompt
      first, then use that prompt for image generation.
    """
    draft = state.get("draft_copy_de", "").strip()
    existing_prompt = state.get("image_prompt_result", "").strip()
    feedback = (state.get("human_feedback_image_generator") or "").strip()

    _rag_trace: list[dict] = []

    if existing_prompt:
        source_text = existing_prompt
        logger.info("image_generator_node: using existing image prompt (%s chars)", len(source_text))
    elif draft:
        logger.info("image_generator_node: generating image prompt from draft")
        llm_start = time.time()
        source_text = _generate_image_prompt(draft, feedback=feedback)
        llm_latency = (time.time() - llm_start) * 1000
        _rag_trace.append({
            "stage": "prompt_generation",
            "latency_ms": round(llm_latency, 1),
            "node": "image_generator",
        })
    else:
        source_text = state.get("image_prompt_instruction", "a cat programmer logo")

    api_start = time.time()
    image_url, image_usage = _call_openrouter_image(source_text)  # noqa: F841 (usage not persisted)
    api_latency = (time.time() - api_start) * 1000

    _rag_trace.append({
        "stage": "image_api",
        "latency_ms": round(api_latency, 1),
        "prompt_length": len(source_text),
        "success": image_url is not None,
    })

    result: dict[str, Any] = {
        "image_url": image_url or "",
        "_current_node": "image_generator",
        "_rag_trace": _rag_trace,
    }
    if image_url:
        result["image_prompt_result"] = source_text

    return result
