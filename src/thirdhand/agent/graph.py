"""LangGraph agent graph definition."""

from langgraph.graph import END, StateGraph

from src.thirdhand.agent.nodes import (
    execute_search_node,
    filter_results_node,
    generate_response_node,
    parse_input_node,
    resolve_task_context_node,
    run_browser_task_node,
    router_node,
    save_reminder_node,
    update_profile_node,
    validate_datetime_node,
)
from src.thirdhand.agent.state import AgentState


def route_by_intent(state: AgentState) -> str:
    """Conditional edge function that routes to the appropriate flow."""
    return router_node(state)


def build_graph() -> StateGraph:
    """Build and compile the agent graph.

    Returns:
        Compiled graph ready for invocation.
    """
    # Initialize graph with state
    workflow = StateGraph(AgentState)

    # === Add nodes ===
    workflow.add_node("parse_input", parse_input_node)
    workflow.add_node("resolve_task_context", resolve_task_context_node)
    workflow.add_node("validate_datetime", validate_datetime_node)
    workflow.add_node("save_reminder", save_reminder_node)
    workflow.add_node("execute_search", execute_search_node)
    workflow.add_node("filter_results", filter_results_node)
    workflow.add_node("run_browser_task", run_browser_task_node)
    workflow.add_node("update_profile", update_profile_node)
    workflow.add_node("generate_response", generate_response_node)

    # === Set entry point ===
    workflow.set_entry_point("parse_input")

    # === Add edges ===
    workflow.add_edge("parse_input", "resolve_task_context")

    # After analysis and context resolution, route based on intent/capabilities
    workflow.add_conditional_edges(
        "resolve_task_context",
        route_by_intent,
        {
            "validate_datetime": "validate_datetime",
            "execute_search": "execute_search",
            "run_browser_task": "run_browser_task",
            "update_profile": "update_profile",
            "generate_response": "generate_response",
        },
    )

    # Reminder flow
    workflow.add_edge("validate_datetime", "save_reminder")
    workflow.add_edge("save_reminder", "generate_response")

    # Search flow
    workflow.add_edge("execute_search", "filter_results")
    workflow.add_edge("filter_results", "generate_response")

    # Browser automation flow
    workflow.add_edge("run_browser_task", "generate_response")

    # Profile flow
    workflow.add_edge("update_profile", "generate_response")

    # Response is always the end
    workflow.add_edge("generate_response", END)

    # Compile
    graph = workflow.compile()

    return graph


# Module-level singleton
graph = build_graph()
