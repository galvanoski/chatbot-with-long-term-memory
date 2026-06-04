import logging
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from backend.graph.state import AgentState
from backend.graph.nodes.research import research_node
from backend.graph.nodes.copywriter import copywriter_node
from backend.graph.nodes.publisher import publisher_node
from backend.graph.conditions import should_approve

logger = logging.getLogger("geekcat.graph.builder")
_checkpoint_conn: sqlite3.Connection | None = None


def _get_checkpoint_saver() -> SqliteSaver:
    global _checkpoint_conn
    if _checkpoint_conn is None:
        checkpoint_path = Path(__file__).resolve().parents[2] / "threads.db"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        _checkpoint_conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        _checkpoint_conn.execute("PRAGMA journal_mode=WAL")
    return SqliteSaver(_checkpoint_conn)


def build_marketing_graph(
    middleware: object | None = None,
) -> StateGraph:
    """Build the multi-agent marketing pipeline StateGraph.

    Flow:
      research → copywriter → [HITL interrupt] → publisher → END

    Args:
        middleware: Optional GeekCatMiddleware instance. If provided,
                    the copywriter node will invoke hooks 2, 3, and 5.

    Returns:
        A compiled StateGraph ready for invocation.
    """
    builder = StateGraph(AgentState)

    # ── Register nodes ──
    builder.add_node("research", research_node)
    builder.add_node(
        "copywriter",
        lambda state: copywriter_node(state, mw=middleware),
    )
    builder.add_node("publisher", publisher_node)

    # ── Edges ──
    builder.set_entry_point("research")
    builder.add_edge("research", "copywriter")

    # Conditional: HITL before publish
    builder.add_conditional_edges(
        "copywriter",
        should_approve,
        {
            "publisher": "publisher",
            "human_feedback": END,
        },
    )

    builder.add_edge("publisher", END)

    # ── Compile with checkpointer + HITL interrupt ──
    checkpointer = _get_checkpoint_saver()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["publisher"],
    )

    logger.info("marketing graph built successfully")
    return graph
