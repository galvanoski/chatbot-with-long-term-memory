import logging
import os
import json
import re

from langchain_core.messages import AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.graph.state import AgentState
from backend.middleware.base import AgentMiddleware

logger = logging.getLogger("geekcat.nodes.copywriter")


def _previous_structured_parts(state: AgentState) -> dict[str, str]:
    parts = (state.get("copy_metadata") or {}).get("parts") or {}
    return {
        "hook": str(parts.get("hook") or "").strip(),
        "body": str(parts.get("body") or "").strip(),
        "cta": str(parts.get("cta") or "").strip(),
    }


def _previous_hashtags(state: AgentState) -> list[str]:
    hashtags = (state.get("copy_metadata") or {}).get("hashtags") or []
    return [str(tag).strip() for tag in hashtags if str(tag).strip()]


def _latest_assistant_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") in ("ai", "assistant"):
            content = getattr(message, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


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


def _build_recent_conversation(state: AgentState, max_messages: int = 6) -> str:
    rendered: list[str] = []
    for message in state.get("messages", [])[-max_messages:]:
        msg_type = getattr(message, "type", "")
        role = "assistant" if msg_type in ("ai", "assistant") else "user"
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else str(content)
        rendered.append(f"{role.upper()}: {text}")
    return "\n".join(rendered)


def _infer_edit_plan(
    llm: ChatOpenAI,
    user_message: str,
    previous_assistant_message: str,
    conversation_context: str,
) -> dict:
    planner_prompt = (
        "You are an intent planner for a multi-turn marketing copy chat.\n"
        "Decide whether the latest user message requests a full new post or a targeted revision of the previous assistant draft.\n"
        "Prefer 'revise' when the user is asking to refine, adjust, expand, shorten, or change only part of the current draft.\n"
        "Use the whole conversation, not keyword matching.\n"
        "Return ONLY valid JSON with this schema:\n"
        "{\n"
        "  \"mode\": \"revise\" | \"new\",\n"
        "  \"explicit_new_post_request\": true | false,\n"
        "  \"reason\": \"short explanation\",\n"
        "  \"edit_scope\": \"what should change and what should stay\",\n"
        "  \"fields_to_update\": [\"hook\" | \"body\" | \"cta\" | \"hashtags\" | \"full_post\"]\n"
        "}\n\n"
        "Set explicit_new_post_request=true ONLY when the user clearly asks for a new/alternative full post.\n"
        "If the user asks to tweak style, tone, humor, or one section, explicit_new_post_request must be false.\n"
        "If unsure, default to revise with explicit_new_post_request=false.\n\n"
        f"Conversation:\n{conversation_context or '(empty)'}\n\n"
        f"Previous assistant draft:\n{previous_assistant_message or '(none)'}\n\n"
        f"Latest user message:\n{user_message or '(empty)'}"
    )

    response = llm.invoke([SystemMessage(content=planner_prompt)])
    payload = _extract_json_payload(response.content if hasattr(response, "content") else str(response)) or {}
    mode = str(payload.get("mode") or "revise").strip().lower()
    if mode not in {"revise", "new"}:
        mode = "revise" if previous_assistant_message.strip() else "new"
    return {
        "mode": mode,
        "explicit_new_post_request": bool(payload.get("explicit_new_post_request", False)),
        "reason": str(payload.get("reason") or "").strip(),
        "edit_scope": str(payload.get("edit_scope") or "").strip(),
        "fields_to_update": [
            str(field).strip().lower()
            for field in (payload.get("fields_to_update") or [])
            if str(field).strip().lower() in {"hook", "body", "cta", "hashtags", "full_post"}
        ],
    }


def _infer_edit_guardrails(
    llm: ChatOpenAI,
    user_message: str,
    previous_assistant_message: str,
    conversation_context: str,
) -> dict:
    guard_prompt = (
        "You are a strict change-guard for a multi-turn marketing copy chat.\n"
        "Your job is to prevent accidental edits when user intent is ambiguous.\n"
        "Use semantic understanding of the full conversation, not keyword matching.\n"
        "Return ONLY valid JSON with this schema:\n"
        "{\n"
        "  \"explicit_new_post_request\": true | false,\n"
        "  \"change_fields\": [\"hook\" | \"body\" | \"cta\" | \"hashtags\" | \"full_post\"],\n"
        "  \"preserve_fields\": [\"hook\" | \"body\" | \"cta\" | \"hashtags\"],\n"
        "  \"notes\": \"short reason\"\n"
        "}\n\n"
        "Rules:\n"
        "- Mark a field in change_fields only when user intent is clearly requesting that field.\n"
        "- If a field is uncertain, place it in preserve_fields.\n"
        "- full_post is allowed only when the user clearly asks for a totally new or alternative full post.\n"
        "- If unsure, prefer preserve_fields and explicit_new_post_request=false.\n\n"
        f"Conversation:\n{conversation_context or '(empty)'}\n\n"
        f"Previous assistant draft:\n{previous_assistant_message or '(none)'}\n\n"
        f"Latest user message:\n{user_message or '(empty)'}"
    )

    response = llm.invoke([SystemMessage(content=guard_prompt)])
    payload = _extract_json_payload(response.content if hasattr(response, "content") else str(response)) or {}
    valid_fields = {"hook", "body", "cta", "hashtags", "full_post"}
    preserve_valid_fields = {"hook", "body", "cta", "hashtags"}

    return {
        "explicit_new_post_request": bool(payload.get("explicit_new_post_request", False)),
        "change_fields": [
            str(field).strip().lower()
            for field in (payload.get("change_fields") or [])
            if str(field).strip().lower() in valid_fields
        ],
        "preserve_fields": [
            str(field).strip().lower()
            for field in (payload.get("preserve_fields") or [])
            if str(field).strip().lower() in preserve_valid_fields
        ],
        "notes": str(payload.get("notes") or "").strip(),
    }


def _strip_prompt_echo(text: str, user_prompt: str) -> str:
    """Remove common LLM behavior of echoing the user instruction as first line."""
    content = (text or "").strip()
    prompt = (user_prompt or "").strip()
    if not content or not prompt:
        return content

    prompt_lower = prompt.lower()
    lines = [line.rstrip() for line in content.splitlines()]
    if not lines:
        return content

    first = lines[0].strip().lstrip("-•* ").strip()
    if first.lower() == prompt_lower:
        lines = lines[1:]
    elif first.lower().startswith(prompt_lower):
        lines[0] = first[len(prompt):].lstrip("-:.,; ").strip()

    cleaned = "\n".join(lines).strip()
    return cleaned or content


def _build_visible_reply(
    mode: str,
    fields_to_update: set[str],
    hook: str,
    body: str,
    cta: str,
    hashtags: list[str],
    full_content: str,
) -> str:
    if mode != "revise" or "full_post" in fields_to_update or not fields_to_update:
        return full_content

    segments: list[str] = []
    if "hook" in fields_to_update and hook:
        segments.append(hook)
    if "body" in fields_to_update and body:
        segments.append(body)
    if "cta" in fields_to_update and cta:
        segments.append(cta)
    if "hashtags" in fields_to_update and hashtags:
        segments.append(" ".join(hashtags))

    return "\n\n".join([segment for segment in segments if segment]) or full_content


def copywriter_node(state: AgentState, mw: AgentMiddleware | None = None) -> dict:
    """Copywriter node: generates marketing copy in German.

    Integrates middleware hooks 2 (before_model), 3 (wrap_model_call),
    and 5 (after_model) when middleware is provided.
    """
    human_feedback = (state.get("human_feedback", "") or "").strip()

    if state["messages"]:
        user_message = state["messages"][-1].content
    elif human_feedback:
        user_message = human_feedback
    else:
        user_message = ""

    previous_assistant_message = _latest_assistant_message(state)
    conversation_context = _build_recent_conversation(state)
    previous_parts = _previous_structured_parts(state)
    previous_hashtags = _previous_hashtags(state)

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

    conversation_section = (
        "\n\nCONVERSATION MODE:\n"
        "- This is a multi-turn chat, not a one-shot generator.\n"
        "- Use the latest user message as an instruction over the ongoing conversation.\n"
        "- Follow the inferred edit plan exactly.\n"
        "- If the latest user message asks for changes to only one part of the copy, keep the rest of the previous draft aligned and stable.\n"
        "- Only create a completely new post if the user explicitly asks for a new, different, or alternative full post.\n"
        "- If the user asks only for hashtag changes or more hashtag options, preserve the hook, body, and CTA and update only the hashtags.\n"
        "- If the user asks for a tweak in tone/length/CTA/hook, revise only those requested parts while preserving the main idea of the previous draft.\n"
    )

    previous_draft_section = ""
    if previous_assistant_message:
        previous_draft_section = (
            "\n\nPREVIOUS ASSISTANT DRAFT (use as the base text unless the user explicitly requests a full replacement):\n"
            f"{previous_assistant_message}\n"
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
        "- ALWAYS include EXACTLY 5 relevant hashtags (no more, no less).\n"
        "- ALWAYS include emojis throughout the text to make it expressive and engaging (at least 3 emojis spread across hook, body, and CTA).\n"
        "- Target audience: IT pros who code by day and pet cats by night.\n"
        "- If product context contains a sarcastic legend, use it as the hook nucleus.\n"
        f"{rules_text}\n\n"
        "Product context (authoritative, use this first):\n"
        f"{product_context_text}\n\n"
        f"Current trend context:\n{trend}\n\n"
        f"Meme references:\n{chr(10).join(f'- {m}' for m in memes)}\n\n"
        f"{feedback_section}"
        f"{conversation_section}"
        f"{previous_draft_section}"
        "Return ONLY valid JSON with this exact schema:\n"
        "{\n"
        "  \"hook\": \"short sarcastic opener in German with emojis\",\n"
        "  \"body\": \"main copy in German with emojis\",\n"
        "  \"cta\": \"short CTA in German with emojis\",\n"
        "  \"hashtags\": [\"#tag1\", \"#tag2\", \"#tag3\", \"#tag4\", \"#tag5\"],\n"
        "  \"style_notes\": {\"sarcasm_level\": \"1-10\", \"used_product_legend\": true}\n"
        "}\n"
        "IMPORTANT: hashtags array MUST contain EXACTLY 5 items. No markdown. No additional text."
    )

    # ── Hook 3: wrap_model_call ──
    llm = ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    inferred_plan = _infer_edit_plan(llm, user_message, previous_assistant_message, conversation_context)
    inferred_guardrails = _infer_edit_guardrails(llm, user_message, previous_assistant_message, conversation_context)

    planner_fields = set(inferred_plan.get("fields_to_update") or [])
    guard_change_fields = set(inferred_guardrails.get("change_fields") or [])
    guard_preserve_fields = set(inferred_guardrails.get("preserve_fields") or [])

    explicit_new_post_request = bool(
        inferred_plan.get("explicit_new_post_request", False)
        and inferred_guardrails.get("explicit_new_post_request", False)
    )

    mode = inferred_plan.get("mode", "revise")
    if previous_assistant_message and not explicit_new_post_request and mode == "new":
        mode = "revise"

    if planner_fields and guard_change_fields:
        fields_to_update = planner_fields & guard_change_fields
    else:
        fields_to_update = planner_fields or guard_change_fields

    fields_to_update = {
        field for field in fields_to_update if field in {"hook", "body", "cta", "hashtags", "full_post"}
    }

    # Guardrail wins when a field is explicitly marked for preservation.
    fields_to_update -= guard_preserve_fields

    if mode == "revise":
        if not explicit_new_post_request:
            fields_to_update.discard("full_post")
        if not fields_to_update:
            # Conservative default for revise: adjust copy text but keep hashtags stable.
            fields_to_update = {"hook", "body", "cta"}

    planning_section = (
        "\n\nINFERRED EDIT PLAN:\n"
        f"- Mode: {mode}\n"
        f"- Explicit new post request (consensus): {explicit_new_post_request}\n"
        f"- Reason: {inferred_plan['reason'] or 'No extra reason provided'}\n"
        f"- Guard notes: {inferred_guardrails['notes'] or 'No extra notes'}\n"
        f"- Scope: {inferred_plan['edit_scope'] or 'Apply the latest user instruction with minimal necessary changes'}\n"
        f"- Fields to update (consensus): {', '.join(sorted(fields_to_update)) or 'unspecified'}\n"
        f"- Fields to preserve (guardrails): {', '.join(sorted(guard_preserve_fields)) or 'none'}\n"
    )

    system_prompt = system_prompt.replace(
        "Return ONLY valid JSON with this exact schema:\n",
        f"{planning_section}Return ONLY valid JSON with this exact schema:\n",
    )

    if mode == "revise" and previous_assistant_message:
        system_prompt += (
            "\n\nREVISION ENFORCEMENT:\n"
            "- Revise the previous assistant draft instead of replacing it wholesale.\n"
            "- Preserve unchanged sections unless the inferred scope or latest user request requires edits.\n"
            "- If the user asks for hashtags or a narrow tweak, keep the hook, body, and CTA semantically stable.\n"
        )
        if any(previous_parts.values()) or previous_hashtags:
            system_prompt += (
                "\n\nPREVIOUS STRUCTURED PARTS (preserve fields not selected for update):\n"
                f"- Hook: {previous_parts['hook'] or '(empty)'}\n"
                f"- Body: {previous_parts['body'] or '(empty)'}\n"
                f"- CTA: {previous_parts['cta'] or '(empty)'}\n"
                f"- Hashtags: {' '.join(previous_hashtags) or '(empty)'}\n"
            )

    messages = [SystemMessage(content=system_prompt), *state["messages"]]

    # ── Hook 2: before_model ──
    if mw:
        messages = mw.before_model(messages, state)

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
        # Enforce exactly 5 hashtags: pad with generic fallbacks if model returned fewer.
        _fallback_tags = ["#TheGeekCat", "#GeekLife", "#CatCoder", "#TechHumor", "#CatAndCode"]
        while len(hashtags) < 5:
            for tag in _fallback_tags:
                if tag not in hashtags:
                    hashtags.append(tag)
                if len(hashtags) == 5:
                    break
        hashtags = hashtags[:5]

        if mode == "revise" and "full_post" not in fields_to_update:
            if "hook" not in fields_to_update and previous_parts["hook"]:
                hook = previous_parts["hook"]
            if "body" not in fields_to_update and previous_parts["body"]:
                body = previous_parts["body"]
            if "cta" not in fields_to_update and previous_parts["cta"]:
                cta = previous_parts["cta"]
            if "hashtags" not in fields_to_update and previous_hashtags:
                hashtags = [
                    tag if tag.startswith("#") else f"#{tag}"
                    for tag in previous_hashtags
                ][:5]

        canonical_content = "\n\n".join(part for part in [hook, body, cta, " ".join(hashtags)] if part)
        canonical_content = _strip_prompt_echo(canonical_content, user_message)
        content = _build_visible_reply(
            mode,
            fields_to_update,
            hook,
            body,
            cta,
            hashtags,
            canonical_content,
        )
        content = _strip_prompt_echo(content, user_message)
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
        content = _strip_prompt_echo(content, user_message)
        hashtags = [w for w in content.split() if w.startswith("#")]
        copy_validation = {
            **getattr(response, "_validation", {}),
            "structured_output": False,
            "schema_version": "copywriter.v1",
        }
        structured_parts = {}

    return {
        "messages": [AIMessage(content=content)],
        "draft_copy_de": canonical_content if payload else content,
        "copy_metadata": {
            "hashtags": hashtags,
            "char_count": len(canonical_content if payload else content),
            "validation": copy_validation,
            "parts": structured_parts,
        },
        "approval_status": "pending",
        "_current_node": "copywriter",
    }
