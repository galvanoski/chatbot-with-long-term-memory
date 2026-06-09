from __future__ import annotations

import contextvars
import time
from typing import Any

_rag_trace_var: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "geekcat_rag_trace",
    default=[],
)


def reset_rag_trace() -> None:
    _rag_trace_var.set([])


def add_rag_trace(stage: str, **fields: Any) -> None:
    events = list(_rag_trace_var.get())
    event: dict[str, Any] = {"stage": stage, "timestamp": time.time()}
    event.update(fields)
    events.append(event)
    _rag_trace_var.set(events)


def get_rag_trace() -> list[dict[str, Any]]:
    return list(_rag_trace_var.get())
