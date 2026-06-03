import logging

from backend.graph.state import AgentState

logger = logging.getLogger("geekcat.graph.conditions")


def should_approve(state: AgentState) -> str:
    """Conditional edge from copywriter node.

    Returns the next node name:
      - "publisher"       → if human approved
      - "human_feedback"  → if pending or rejected → END (interrupted)
    """
    status = state.get("approval_status")
    logger.debug("should_approve: approval_status=%s", status)

    if status == "approved":
        return "publisher"
    return "human_feedback"  # Goes to END, paused by interrupt_before


def has_published(state: AgentState) -> str:
    """Terminal check after publisher node. Always ends the graph."""
    return "end"
