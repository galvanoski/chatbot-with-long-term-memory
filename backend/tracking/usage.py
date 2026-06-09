"""Token usage tracking and cost calculation for LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODEL_PRICING: dict[str, dict[str, float]] = {
    "openai/gpt-5-mini": {"input": 0.00015, "output": 0.00060},
    "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
}


@dataclass
class UsageInfo:
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def cost(self) -> float:
        pricing = MODEL_PRICING.get(self.model) or MODEL_PRICING.get("openai/gpt-5-mini", {"input": 0.00015, "output": 0.00060})
        return (self.input_tokens / 1000) * pricing["input"] + (self.output_tokens / 1000) * pricing["output"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "cost": round(self.cost, 6),
        }


class UsageTracker:
    """Accumulates token usage across one or more LLM calls."""

    def __init__(self) -> None:
        self._calls: list[UsageInfo] = []

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self._calls.append(UsageInfo(model=model, input_tokens=input_tokens, output_tokens=output_tokens))

    def total(self) -> UsageInfo:
        total = UsageInfo()
        for info in self._calls:
            total.input_tokens += info.input_tokens
            total.output_tokens += info.output_tokens
            if not total.model and info.model:
                total.model = info.model
        return total

    def to_dict(self) -> dict[str, Any]:
        return self.total().to_dict()


def extract_usage_from_llm_output(output: Any) -> dict[str, int] | None:
    """Extract token usage metadata from a LangChain LLM output object."""
    usage = getattr(output, "usage_metadata", None)
    if usage and isinstance(usage, dict):
        return usage
    response_meta = getattr(output, "response_metadata", None) or {}
    token_usage = response_meta.get("token_usage") or response_meta.get("usage")
    if token_usage and isinstance(token_usage, dict):
        return token_usage
    return None


def extract_usage_from_chunks(chunks: list[Any]) -> dict[str, int] | None:
    """Extract aggregate usage from accumulated stream chunks."""
    for chunk in reversed(chunks):
        usage = getattr(chunk, "usage_metadata", None)
        if usage and isinstance(usage, dict):
            return usage
        response_meta = getattr(chunk, "response_metadata", None) or {}
        token_usage = response_meta.get("token_usage") or response_meta.get("usage")
        if token_usage and isinstance(token_usage, dict):
            return token_usage
    return None
