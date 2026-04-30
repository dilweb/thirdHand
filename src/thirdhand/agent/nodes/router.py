"""Router node - routes to the appropriate flow based on intent."""

from src.thirdhand.agent.state import AgentState


def router_node(state: AgentState) -> str:
    """Route to the appropriate flow based on intent.

    Args:
        state: Current agent state.

    Returns:
        String indicating the next node to visit.
    """
    intent = state.intent

    match intent:
        case "reminder":
            return "validate_datetime"
        case "search":
            return "execute_search"
        case "profile_update":
            return "update_profile"
        case _:
            return "generate_response"
