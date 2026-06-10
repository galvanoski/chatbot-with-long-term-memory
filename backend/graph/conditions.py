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


def should_generate_image(state: AgentState) -> str:
    """Check if the user requested image generation after a post or image prompt."""
    image_url = state.get("image_url")
    requested = state.get("image_generation_requested")
    if requested or image_url:
        return "image_generator"
    return "skip_image"


def _image_generator_router(state: AgentState) -> str:
    """Route from image_generator based on entry context.

    Flow A (from copywriter, social media post) → END (no SEO).
    Flow B (from image_prompt, merchandise) → seo_generator.
      - If copywriter already ran in Flow B → END (second image_generator pass).
    """
    entry = state.get("_entry_node", "")
    if entry == "image_prompt_generator":
        draft = state.get("draft_copy_de", "").strip()
        existing_prompt = state.get("image_prompt_result", "").strip()
        if draft and existing_prompt:
            # Copywriter already ran after image_prompt → second pass → END
            return "end"
        return "seo_generator"
    return "end"


def should_run_copywriter(state: AgentState) -> str:
    """Check if the user wants to run the copywriter after SEO/image flow."""
    if state.get("run_copywriter_requested"):
        return "copywriter"
    return "end"


def _seo_generator_router(state: AgentState) -> str:
    """Route from seo_generator based on entry context.

    Flow A (from copywriter, skip_image) → publish flow.
    Flow B (from image_prompt) → ask if copywriter should run.
    """
    entry = state.get("_entry_node", "")
    if entry == "image_prompt_generator":
        # Flow B: ask if copywriter should run
        if state.get("run_copywriter_requested"):
            return "copywriter"
        return "end"
    # Flow A (copywriter skip_image): publish flow
    status = state.get("approval_status")
    if status == "approved":
        return "publisher"
    return "human_feedback"


def has_published(state: AgentState) -> str:
    """Terminal check after publisher node. Always ends the graph."""
    return "end"
