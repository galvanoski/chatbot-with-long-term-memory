import logging

from backend.graph.state import AgentState
from backend.graph.tools.social import simulate_post

logger = logging.getLogger("geekcat.nodes.publisher")


def publisher_node(state: AgentState) -> dict:
    """Publisher node: posts the approved copy to social media.

    Only proceeds if approval_status == "approved".
    """
    if state.get("approval_status") != "approved":
        logger.warning("publisher_node: skipping — not approved (%s)", state.get("approval_status"))
        return {
            "publication_result": {
                "success": False,
                "error": f"Not approved (status={state.get('approval_status')})",
            },
            "_current_node": "publisher",
        }

    content = state.get("draft_copy_de", "")
    user_id = state.get("user_id", "unknown")

    logger.info("publisher_node: publishing copy for user=%s", user_id)

    # In production, determine platform from thread config
    platform = state.get("copy_metadata", {}).get("platform", "instagram")

    result = simulate_post(
        platform=platform,
        content=content,
        user_id=user_id,
    )

    return {
        "publication_result": result,
        "_current_node": "publisher",
    }
