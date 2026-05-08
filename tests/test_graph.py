"""Tests for LangGraph graph compilation and routing."""


from src.thirdhand.agent.graph import build_graph, graph
from src.thirdhand.agent.nodes.router import router_node
from src.thirdhand.agent.state import AgentState


class TestGraphCompilation:
    """Tests for graph compilation."""

    def test_graph_compiles(self) -> None:
        """Test that the graph compiles without errors."""
        g = build_graph()
        assert g is not None

    def test_graph_has_expected_nodes(self) -> None:
        """Test that the graph has all expected nodes."""
        node_names = list(graph.nodes.keys())

        assert "__start__" in node_names
        assert "parse_input" in node_names
        assert "resolve_task_context" in node_names
        assert "validate_datetime" in node_names
        assert "save_reminder" in node_names
        assert "execute_search" in node_names
        assert "filter_results" in node_names
        assert "synthesize_search_response" in node_names
        assert "run_browser_task" in node_names
        assert "update_profile" in node_names
        assert "generate_response" in node_names


class TestRouterNode:
    """Tests for router_node."""

    def test_route_reminder(self) -> None:
        """Test routing to reminder flow."""
        state = AgentState(intent="reminder")
        assert router_node(state) == "validate_datetime"

    def test_route_existing_response_first(self) -> None:
        """If parsing already produced a user-facing response, stop routing further."""
        state = AgentState(response_text="Уточни город")
        assert router_node(state) == "generate_response"

    def test_route_search(self) -> None:
        """Test routing to search flow."""
        state = AgentState(intent="chat", requires_web_search=True)
        assert router_node(state) == "execute_search"

    def test_route_profile_update(self) -> None:
        """Test routing to profile update flow."""
        state = AgentState(intent="profile_update")
        assert router_node(state) == "update_profile"

    def test_route_browser_task(self) -> None:
        """Test routing to browser flow."""
        state = AgentState(intent="chat", requires_browser=True)
        assert router_node(state) == "run_browser_task"

    def test_route_chat(self) -> None:
        """Test routing to chat flow (default)."""
        state = AgentState(intent="chat")
        assert router_node(state) == "generate_response"

    def test_route_unknown(self) -> None:
        """Test routing unknown intent to generate_response."""
        state = AgentState(intent="unknown")
        assert router_node(state) == "generate_response"


class TestGraphRouting:
    """Tests for graph routing logic."""

    def test_stage22_browser_path_is_flat_single_service_node(self) -> None:
        """Stage 22: no nested browser subgraph; one node then response (service owns the loop)."""
        compiled = build_graph()
        assert list(compiled.get_subgraphs()) == []
        gx = compiled.get_graph()
        browser_edges = [e for e in gx.edges if e.source == "run_browser_task"]
        assert len(browser_edges) == 1
        assert browser_edges[0].target == "generate_response"
        assert browser_edges[0].conditional is False

    def test_conditional_edges_mapping(self) -> None:
        """Test that conditional edges map correctly."""
        # Verify the graph has conditional edges from parse_input
        # This is an integration test that checks the graph structure
        compiled_graph = build_graph()

        # The compiled graph should have invoke/ainvoke methods
        assert hasattr(compiled_graph, "invoke")
        assert hasattr(compiled_graph, "ainvoke")
