import logging
import os
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI

logger = logging.getLogger("geekcat.compaction")


class CompactionEngine:
    """Adaptive message compaction with multi-stage summarisation and
    graceful fallback.

    Stages (each attempted in order):
      1. Extractive pruning (remove tool messages, trim to last N)
      2. Abstractive LLM summarisation with primary model (gpt-5-mini)
      3. Abstractive LLM summarisation with fallback model (gpt-4o-mini)
      4. Hard truncation (keep first system + last 6 messages)
    """

    def __init__(
        self,
        primary_llm: ChatOpenAI | None = None,
        fallback_llm: ChatOpenAI | None = None,
    ):
        self.primary_llm = primary_llm or ChatOpenAI(
            model="openai/gpt-5-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        self.fallback_llm = fallback_llm or ChatOpenAI(
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

    # ── Public entry point ──

    def compact(
        self,
        messages: list,
        context_window: int = 128_000,
        trigger_ratio: float = 0.70,
    ) -> list:
        """Check token pressure, and if above threshold, compact messages.

        Returns compacted (or original) message list.
        """
        total_tokens = self._count_tokens(messages)
        threshold = int(context_window * trigger_ratio)

        if total_tokens <= threshold:
            return messages

        overshoot = (total_tokens - threshold) / threshold
        target_ratio = min(0.2 + overshoot * 0.6, 0.85)
        target_tokens = int(total_tokens * (1 - target_ratio))

        logger.info(
            "compaction: tokens=%d threshold=%d target_ratio=%.2f target_tokens=%d",
            total_tokens, threshold, target_ratio, target_tokens,
        )

        # Stage 1: Extractive pruning
        stage1 = self._stage1_extractive(messages)
        if self._count_tokens(stage1) <= target_tokens:
            logger.debug("compaction: stage1 extractive sufficient")
            return stage1

        # Stage 2: Abstractive summarisation (primary model)
        try:
            stage2 = self._stage2_abstractive(stage1, self.primary_llm)
            if self._count_tokens(stage2) <= target_tokens:
                logger.debug("compaction: stage2 primary sufficient")
                return stage2
        except Exception as exc:
            logger.warning("compaction: stage2 primary failed: %s", exc)

        # Stage 3: Abstractive summarisation (fallback model)
        try:
            stage3 = self._stage2_abstractive(stage1, self.fallback_llm)
            if self._count_tokens(stage3) <= target_tokens:
                logger.debug("compaction: stage3 fallback sufficient")
                return stage3
        except Exception as exc:
            logger.warning("compaction: stage3 fallback failed: %s", exc)

        # Stage 4: Hard truncation — always succeeds
        stage4 = self._stage4_truncate(messages)
        logger.warning("compaction: stage4 hard truncation to %d messages", len(stage4))
        return stage4

    # ── Stage 1: Extractive Pruning ──

    def _stage1_extractive(self, messages: list) -> list:
        """Remove tool messages, consolidate, trim to minimum viable set."""
        pruned = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                continue
            if isinstance(msg, AIMessage) and not msg.content and not msg.tool_calls:
                continue
            pruned.append(msg)

        # Keep system messages + last 15 conversation messages
        system_msgs = [m for m in pruned if isinstance(m, SystemMessage)]
        conversation = [m for m in pruned if not isinstance(m, SystemMessage)]
        return system_msgs + conversation[-15:]

    # ── Stage 2 & 3: Abstractive Summarisation ──

    def _stage2_abstractive(self, messages: list, llm: ChatOpenAI) -> list:
        """Use LLM to generate a compact summary that replaces the bulk of
        conversation history, preserving facts, decisions, and context."""
        conversation_text = "\n".join(
            f"{type(m).__name__}: {m.content}"
            for m in messages
            if not isinstance(m, SystemMessage) and m.content
        )[:4000]

        response = llm.invoke([
            SystemMessage(
                content=(
                    "You are a conversation summariser for a marketing AI agent. "
                    "Summarise the following conversation in German. Preserve: "
                    "1) All user preferences and personal facts, "
                    "2) All content decisions and approvals, "
                    "3) Product SKUs discussed, "
                    "4) Tone and style guidelines established. "
                    "Output ONLY the summary, 3-5 sentences."
                ),
            ),
            HumanMessage(content=f"Conversation:\n\n{conversation_text}"),
        ])

        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        last_msg = messages[-1] if messages else None

        result = list(system_msgs)
        result.append(AIMessage(content=f"[Compacted summary]\n{response.content}"))
        if last_msg and not isinstance(last_msg, SystemMessage):
            result.append(last_msg)
        return result

    # ── Stage 4: Hard Truncation ──

    def _stage4_truncate(self, messages: list) -> list:
        """Last resort: keep system messages + last 6 conversation messages."""
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        conversation = [m for m in messages if not isinstance(m, SystemMessage)]
        return system_msgs + conversation[-6:]

    # ── Utilities ──

    def _count_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            content = getattr(msg, "content", "") or ""
            total += len(content) // 4 + 10
        return total

    def flush_important_facts(
        self,
        messages: list,
        max_candidates: int = 10,
    ) -> list[str]:
        """Extract important facts from recent messages for pre-compaction flush.

        Used by MemoryManager before compaction to avoid losing data.
        Returns list of fact strings.
        """
        candidates = []
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage) and msg.content:
                candidates.append(("human", msg.content))
            elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                candidates.append(("ai", msg.content))
            if len(candidates) >= max_candidates:
                break

        facts = []
        for role, content in candidates:
            if role == "human":
                # Simple extraction: sentences containing personal keywords
                for sentence in content.split("."):
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    keywords = [
                        "ich bin", "ich mag", "ich habe", "ich arbeite",
                        "ich wohne", "mein name", "meine", "mein",
                        "ich möchte", "ich will", "ich brauche",
                    ]
                    if any(kw in sentence.lower() for kw in keywords):
                        facts.append(sentence + ".")
            elif role == "ai":
                # Extract confirmations of facts
                for sentence in content.split("."):
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    if any(kw in sentence.lower() for kw in [
                        "gespeichert", "merke", "erinnere", "notiert",
                        "verstanden", "confirmed", "saved",
                    ]):
                        facts.append(sentence + ".")

        return facts[:5]
