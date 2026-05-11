"""Router node - routes to the appropriate flow based on intent."""

import structlog

from src.thirdhand.agent.state import AgentState

logger = structlog.get_logger(__name__)


def router_node(state: AgentState) -> str:
    """Route to the appropriate flow based on intent.

    Args:
        state: Current agent state.

    Returns:
        String indicating the next node to visit.
    """
    logger.debug(
        "router_node_invoked",
        user_id=state.user_id,
        intent=state.intent,
        requires_browser=state.requires_browser,
        requires_web_search=state.requires_web_search,
        has_response_text=bool(state.response_text),
    )
    
    if state.response_text:
        logger.info("router_decision", user_id=state.user_id, route="generate_response", reason="has_response_text")
        return "generate_response"

    # Route by capabilities first so we do not depend on brittle keyword lists.
    if state.intent == "reminder":
        logger.info("router_decision", user_id=state.user_id, route="validate_datetime", reason="intent=reminder")
        return "validate_datetime"
    if state.intent == "profile_update":
        logger.info("router_decision", user_id=state.user_id, route="update_profile", reason="intent=profile_update")
        return "update_profile"
    if state.requires_browser:
        logger.info("router_decision", user_id=state.user_id, route="run_browser_task", reason="requires_browser=True")
        return "run_browser_task"
    if state.requires_web_search:
        logger.info("router_decision", user_id=state.user_id, route="execute_search", reason="requires_web_search=True")
        return "execute_search"
    logger.info("router_decision", user_id=state.user_id, route="generate_response", reason="default_fallback")
    return "generate_response"
