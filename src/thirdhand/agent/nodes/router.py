"""Router node - routes to the appropriate flow based on intent."""

from src.thirdhand.agent.state import AgentState


def router_node(state: AgentState) -> str:
    """Route to the appropriate flow based on intent.

    Args:
        state: Current agent state.

    Returns:
        String indicating the next node to visit.
    """
    if state.response_text:
        return "generate_response"

    # Route by capabilities first so we do not depend on brittle keyword lists.
    if state.intent == "reminder":
        return "validate_datetime"
    if state.intent == "profile_update":
        return "update_profile"
    if state.requires_browser:
        return "run_browser_task"
    if state.requires_web_search:
        return "execute_search"
    return "generate_response"
