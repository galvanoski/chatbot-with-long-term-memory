import logging
import os
import json
import re

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState
from backend.middleware.base import AgentMiddleware

logger = logging.getLogger("geekcat.nodes.copywriter")


def _extract_json_payload(raw: str) -> dict | None:
    text = raw.strip()

    # Strip optional markdown code fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        pass

    # Fallback: grab the first JSON object in the response.
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def copywriter_node(state: AgentState, mw: AgentMiddleware | None = None) -> dict:
    """Copywriter node: generates marketing copy in German.

    Integrates middleware hooks 2 (before_model), 3 (wrap_model_call),
    and 5 (after_model) when middleware is provided.
    """
    user_message = state["messages"][-1].content if state["messages"] else ""
    logger.info("copywriter_node: generating copy for: %s", user_message[:60])

    # Build base system prompt
    brand_rules = state.get("brand_rules", {})
    rules_text = "\n".join(f"- {k}: {v}" for k, v in brand_rules.items()) if brand_rules else ""
    product_context = state.get("product_context", [])
    product_context_text = "\n\n".join(product_context[:3]) if product_context else "(no product context found)"
    trend = state.get("trend_insights", "")
    memes = state.get("meme_references", [])
    human_feedback = (state.get("human_feedback", "") or "").strip()
    feedback_section = ""
    if human_feedback:
        feedback_section = (
            "\n\nIMPORTANT USER FEEDBACK (previous copy was rejected):\n"
            f'"{human_feedback}"\n'
            "Use this feedback to improve the new copy. Address the user's concerns directly.\n"
        )

    system_prompt = (
        "You are a sarcastic, ironic, and humorous marketing copywriter "
        "for 'The Geek Cat' (thegeekcat.de), a Print-on-Demand store for "
        "IT professionals, blockchain engineers, and AI researchers who "
        "love cats.\n\n"
        "Rules:\n"
        "- Write ONLY in German (Deutsch).\n"
        "- Tone: sarcastic, tech-savvy, brutally funny.\n"
        "- Always include a cat pun or a tech reference.\n"
        "- Keep it concise — Instagram caption style (max 2200 chars).\n"
        "- Include 3-5 relevant hashtags.\n"
        "- Target audience: IT pros who code by day and pet cats by night.\n"
        "- If product context contains a sarcastic legend, use it as the hook nucleus.\n"
        f"{rules_text}\n\n"
        "Product context (authoritative, use this first):\n"
        f"{product_context_text}\n\n"
        f"Current trend context:\n{trend}\n\n"
        f"Meme references:\n{chr(10).join(f'- {m}' for m in memes)}\n\n"
        f"{feedback_section}"
        "Return ONLY valid JSON with this exact schema:\n"
        "{\n"
        "  \"hook\": \"short sarcastic opener in German\",\n"
        "  \"body\": \"main copy in German\",\n"
        "  \"cta\": \"short CTA in German\",\n"
        "  \"hashtags\": [\"#tag1\", \"#tag2\", \"#tag3\"],\n"
        "  \"style_notes\": {\"sarcasm_level\": \"1-10\", \"used_product_legend\": true}\n"
        "}\n"
        "No markdown. No additional text."
    )

    messages = [SystemMessage(content=system_prompt), *state["messages"]]

    # ── Hook 2: before_model ──
    if mw:
        messages = mw.before_model(messages, state)

    # ── Hook 3: wrap_model_call ──
    llm = ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    def invoke(msgs):
        return llm.invoke(msgs)

    state["_current_node"] = "copywriter"
    if mw:
        response = mw.wrap_model_call(invoke, messages, state)
    else:
        response = invoke(messages)

    # ── Hook 5: after_model ──
    if mw:
        response = mw.after_model(response, state)

    content = response.content if hasattr(response, "content") else str(response)
    payload = _extract_json_payload(content)

    if payload:
        hook = str(payload.get("hook", "")).strip()
        body = str(payload.get("body", "")).strip()
        cta = str(payload.get("cta", "")).strip()
        raw_hashtags = payload.get("hashtags", [])
        hashtags = [
            h if str(h).startswith("#") else f"#{h}"
            for h in raw_hashtags
            if str(h).strip()
        ]
        hashtags = hashtags[:5]

        content = "\n\n".join(part for part in [hook, body, cta, " ".join(hashtags)] if part)
        copy_validation = {
            **getattr(response, "_validation", {}),
            "structured_output": True,
            "schema_version": "copywriter.v1",
        }
        structured_parts = {
            "hook": hook,
            "body": body,
            "cta": cta,
            "style_notes": payload.get("style_notes", {}),
        }
    else:
        hashtags = [w for w in content.split() if w.startswith("#")]
        copy_validation = {
            **getattr(response, "_validation", {}),
            "structured_output": False,
            "schema_version": "copywriter.v1",
        }
        structured_parts = {}

    return {
        "draft_copy_de": content,
        "copy_metadata": {
            "hashtags": hashtags,
            "char_count": len(content),
            "validation": copy_validation,
            "parts": structured_parts,
        },
        "approval_status": "pending",
        "_current_node": "copywriter",
    }
