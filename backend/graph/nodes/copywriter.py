import logging
import os

from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState
from backend.middleware.base import AgentMiddleware

logger = logging.getLogger("geekcat.nodes.copywriter")


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
    trend = state.get("trend_insights", "")
    memes = state.get("meme_references", [])

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
        f"{rules_text}\n\n"
        f"Current trend context:\n{trend}\n\n"
        f"Meme references:\n{chr(10).join(f'- {m}' for m in memes)}\n\n"
        "Generate ONLY the post copy. No explanations."
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
    hashtags = [w for w in content.split() if w.startswith("#")]

    return {
        "draft_copy_de": content,
        "copy_metadata": {
            "hashtags": hashtags,
            "char_count": len(content),
            "validation": getattr(response, "_validation", {}),
        },
        "approval_status": "pending",
        "_current_node": "copywriter",
    }
