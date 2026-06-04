import logging
import os
import time
import json
from contextvars import ContextVar
from typing import Any, Callable

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.middleware.base import AgentMiddleware
from backend.memory.manager import MemoryManager

logger = logging.getLogger("geekcat.middleware")

_request_start_time: ContextVar[float | None] = ContextVar("geekcat_start_time", default=None)
_request_current_node: ContextVar[str] = ContextVar("geekcat_current_node", default="")
_request_analytics: ContextVar[list[dict] | None] = ContextVar("geekcat_analytics", default=None)


class GeekCatMiddleware(AgentMiddleware):
    """Concrete middleware for The Geek Cat marketing agent.

    Implements all 6 hooks with business logic specific to the POD
    marketing pipeline (German copy, sarcastic tone, IT audience).
    """

    def __init__(
        self,
        memory: MemoryManager,
        llm_registry: dict[str, str] | None = None,
    ):
        self.memory = memory
        self.llm_registry = llm_registry or {
            "research": "openai/gpt-4o-mini",
            "copywriter": "openai/gpt-5-mini",
            "publisher": "openai/gpt-4o-mini",
        }

    # ──────────────────────────────────────────────
    # Hook 1: before_agent
    # ──────────────────────────────────────────────
    def before_agent(self, state: dict, config: dict) -> dict:
        """Validate request, load brand rules + LTM context, init analytics."""
        user_id = config["configurable"].get("user_id", "anonymous")
        query = state["messages"][-1].content if state["messages"] else ""

        logger.info("before_agent: user=%s thread=%s", user_id, config["configurable"].get("thread_id"))

        # Load brand rules from LTM
        brand_rules = self.memory.get_brand_rules(user_id)
        state["brand_rules"] = brand_rules

        # Retrieve relevant LTM context via hybrid search
        ltm_context = self.memory.retrieve(user_id, query, k=5)
        state["ltm_context"] = [m["text"] for m in ltm_context]

        # Init analytics
        _request_start_time.set(time.time())
        analytics = [{"event": "agent_start", "timestamp": time.time()}]
        _request_analytics.set(analytics)
        state["_analytics_log"] = analytics

        return state

    # ──────────────────────────────────────────────
    # Hook 2: before_model
    # ──────────────────────────────────────────────
    def before_model(
        self,
        messages: list[BaseMessage],
        state: dict,
    ) -> list[BaseMessage]:
        """Inject RAG + LTM context into system prompt, adaptive compaction."""
        # Adaptive compaction check
        messages = self.memory.compact_if_needed(messages)

        # Collect context from state
        ltm_context = state.get("ltm_context", [])
        brand_rules = state.get("brand_rules", {})
        product_skus = state.get("product_skus", [])
        product_context = state.get("product_context", [])

        # Build context block
        context_parts = []
        if ltm_context:
            context_parts.append("Relevant user memories:\n" + "\n".join(f"- {m}" for m in ltm_context))
        if brand_rules:
            context_parts.append("Brand rules:\n" + "\n".join(f"- {k}: {v}" for k, v in brand_rules.items()))
        if product_skus:
            context_parts.append("Selected products:\n" + "\n".join(f"- {sku}" for sku in product_skus[:5]))
        if product_context:
            context_parts.append(
                "Product context snippets (use sarcastic legend as message core):\n"
                + "\n\n".join(product_context[:3])
            )

        # Inject into the first SystemMessage, or prepend one
        context_block = "\n\n".join(context_parts) if context_parts else ""

        if context_block:
            if messages and isinstance(messages[0], SystemMessage):
                messages[0] = SystemMessage(
                    content=messages[0].content + "\n\n" + context_block
                )
            else:
                messages.insert(0, SystemMessage(content=context_block))

        # Trim to last 20 messages
        return messages[-20:]

    # ──────────────────────────────────────────────
    # Hook 3: wrap_model_call
    # ──────────────────────────────────────────────
    def wrap_model_call(
        self,
        invoke_func: Callable[[list[BaseMessage]], Any],
        messages: list[BaseMessage],
        state: dict,
    ) -> Any:
        """Dynamic model selection per node, logging, retry with fallback."""
        task = _request_current_node.get() or state.get("_current_node", "copywriter")
        model_name = self.llm_registry.get(task, "openai/gpt-5-mini")

        logger.info("wrap_model_call: task=%s model=%s", task, model_name)
        _request_current_node.set(task)

        try:
            start = time.time()
            response = invoke_func(messages)
            elapsed = time.time() - start

            # Log usage
            usage = {}
            if hasattr(response, "usage_metadata"):
                usage = response.usage_metadata
                logger.debug("tokens: %s", usage)

            analytics = _request_analytics.get() or []
            analytics.append({
                "event": "llm_call",
                "task": task,
                "model": model_name,
                "elapsed_ms": round(elapsed * 1000),
                "usage": usage,
            })
            _request_analytics.set(analytics)
            return response

        except Exception as exc:
            logger.error("wrap_model_call error (task=%s): %s", task, exc)
            # Attempt fallback to cheaper model
            if task != "research":
                logger.info("retrying with fallback model gpt-4o-mini")
                fallback_llm = ChatOpenAI(
                    model="openai/gpt-4o-mini",
                    base_url="https://openrouter.ai/api/v1",
                    api_key=os.environ["OPENROUTER_API_KEY"],
                )
                response = fallback_llm.invoke(messages)
                analytics = _request_analytics.get() or []
                analytics.append({
                    "event": "llm_call_fallback",
                    "task": task,
                    "model": "gpt-4o-mini",
                    "error": str(exc),
                })
                _request_analytics.set(analytics)
                return response
            raise

    # ──────────────────────────────────────────────
    # Hook 4: wrap_tool_call
    # ──────────────────────────────────────────────
    def wrap_tool_call(
        self,
        tool_func: Callable[..., Any],
        tool_name: str,
        args: dict,
        state: dict,
    ) -> Any:
        """Authorisation and audit logging for tool execution."""
        logger.info("wrap_tool_call: tool=%s args=%s", tool_name, args)

        # Block publish if not approved
        if tool_name == "simulate_post" and state.get("approval_status") != "approved":
            raise PermissionError(f"Cannot publish: approval_status={state.get('approval_status')}")

        try:
            result = tool_func(**args)
            analytics = _request_analytics.get() or []
            analytics.append({
                "event": "tool_call",
                "tool": tool_name,
                "args": args,
                "success": True,
            })
            _request_analytics.set(analytics)
            return result

        except Exception as exc:
            analytics = _request_analytics.get() or []
            analytics.append({
                "event": "tool_call",
                "tool": tool_name,
                "args": args,
                "success": False,
                "error": str(exc),
            })
            _request_analytics.set(analytics)
            raise

    # ──────────────────────────────────────────────
    # Hook 5: after_model
    # ──────────────────────────────────────────────
    def after_model(self, response: Any, state: dict) -> Any:
        """Validate German language, hashtags, length limits."""
        content = response.content if hasattr(response, "content") else str(response)
        validation = {}

        # 1. German language check
        has_german = any(c in content for c in "äöüßÄÖÜ")
        validation["has_german"] = has_german
        if not has_german:
            logger.warning("after_model: copy lacks German characters")

        # 2. Hashtag check
        hashtags = [w for w in content.split() if w.startswith("#")]
        validation["hashtag_count"] = len(hashtags)
        if len(hashtags) < 2:
            logger.warning("after_model: fewer than 2 hashtags")

        # 3. Instagram length limit
        char_count = len(content)
        validation["char_count"] = char_count
        if char_count > 2200:
            logger.warning("after_model: copy exceeds 2200 chars (len=%d)", char_count)

        # Attach validation to response
        response._validation = validation
        return response

    # ──────────────────────────────────────────────
    # Hook 6: after_agent
    # ──────────────────────────────────────────────
    def after_agent(self, state: dict, config: dict) -> dict:
        """Pre-compaction flush, save analytics, rebuild BM25, cleanup."""
        elapsed = time.time() - (_request_start_time.get() or time.time())
        user_id = config["configurable"].get("user_id", "anonymous")
        messages = state.get("messages", [])

        logger.info("after_agent: user=%s elapsed=%.2fs", user_id, elapsed)

        # Pre-compaction memory flush
        self.memory.flush_before_compaction(user_id, messages)

        # Save analytics
        self.memory.save_analytics(user_id, "agent_complete", {
            "elapsed_seconds": round(elapsed, 2),
            "approval_status": str(state.get("approval_status") or ""),
            "product_skus": json.dumps(state.get("product_skus") or []),
            "log": json.dumps(_request_analytics.get() or []),
        })

        # Rebuild BM25 index
        self.memory.rebuild_bm25_index(user_id)

        # Strip temp fields before returning
        state.pop("_analytics_log", None)
        state.pop("ltm_context", None)
        _request_start_time.set(None)
        _request_current_node.set("")
        _request_analytics.set(None)

        return state
