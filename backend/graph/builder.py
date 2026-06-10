import logging
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from backend.graph.state import AgentState
from backend.graph.nodes.research import research_node
from backend.graph.nodes.copywriter import copywriter_node
from backend.graph.nodes.seo import seo_node
from backend.graph.nodes.publisher import publisher_node
from backend.graph.nodes.image_prompt import image_prompt_node
from backend.graph.nodes.image_generator import image_generator_node
from backend.graph.conditions import (
    should_generate_image,
    _image_generator_router,
    _seo_generator_router,
)

logger = logging.getLogger("geekcat.graph.builder")
_checkpoint_saver: AsyncSqliteSaver | None = None
_checkpoint_cm = None


async def _get_checkpoint_saver() -> AsyncSqliteSaver:
    global _checkpoint_saver, _checkpoint_cm
    if _checkpoint_saver is None:
        checkpoint_path = Path(__file__).resolve().parents[2] / "threads.db"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        _checkpoint_cm = AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
        _checkpoint_saver = await _checkpoint_cm.__aenter__()
        await _checkpoint_saver.setup()
    return _checkpoint_saver


def _entry_router(state: AgentState) -> str:
    if state.get("_current_node") == "image_prompt_generator":
        return "image_prompt_generator"
    return "research"


async def build_marketing_graph(
    middleware: object | None = None,
) -> StateGraph:
    """Build the multi-agent marketing pipeline StateGraph.

    Two main flows:

    **Flow A — Social media post** (copywriter → image → END):
      research → copywriter → [image_generator?] → image_generator → END
                                                         └skip → seo → publisher → END

    **Flow B — Merchandise product** (image_prompt → image → SEO → copywriter?):
      image_prompt_generator → image_generator → seo_generator → [copywriter?]
                                                                     ├yes → copywriter → Flow A
                                                                     └no → END
    """

    builder = StateGraph(AgentState)

    # ── Register nodes ──
    builder.add_node("research", research_node)
    builder.add_node(
        "copywriter",
        lambda state: copywriter_node(state, mw=middleware),
    )
    builder.add_node("image_generator", lambda state: image_generator_node(state, mw=middleware))
    builder.add_node("seo_generator", seo_node)
    builder.add_node("publisher", publisher_node)
    builder.add_node(
        "image_prompt_generator",
        lambda state: image_prompt_node(state, mw=middleware),
    )

    # ── Entry routing ──
    builder.add_conditional_edges(
        START,
        _entry_router,
        {
            "research": "research",
            "image_prompt_generator": "image_prompt_generator",
        },
    )

    # ── Main pipeline edges ──
    builder.add_edge("research", "copywriter")

    # After copywriter: ask about image → skip or generate
    builder.add_conditional_edges(
        "copywriter",
        should_generate_image,
        {
            "image_generator": "image_generator",
            "skip_image": "seo_generator",
        },
    )

    # After image_generator: route based on entry context
    # Flow A (from copywriter) → END; Flow B (from image_prompt) → seo_generator
    builder.add_conditional_edges(
        "image_generator",
        _image_generator_router,
        {
            "seo_generator": "seo_generator",
            "end": END,
        },
    )

    # After image_prompt: ask about image → skip or generate
    builder.add_conditional_edges(
        "image_prompt_generator",
        should_generate_image,
        {
            "image_generator": "image_generator",
            "skip_image": END,
        },
    )

    # After seo_generator: route based on entry context
    # Flow A (copywriter skip_image) → publish; Flow B (image_prompt) → copywriter?
    builder.add_conditional_edges(
        "seo_generator",
        _seo_generator_router,
        {
            "publisher": "publisher",
            "human_feedback": END,
            "copywriter": "copywriter",
            "end": END,
        },
    )

    builder.add_edge("publisher", END)

    # ── Compile with checkpointer + HITL interrupt ──
    checkpointer = await _get_checkpoint_saver()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["publisher"],
    )

    logger.info("marketing graph built successfully")
    return graph
