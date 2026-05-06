"""Tests for task context resolution node."""

from src.thirdhand.agent.nodes.task_context import resolve_task_context_node
from src.thirdhand.agent.state import AgentState


class TestResolveTaskContextNode:
    """Tests for resolve_task_context_node."""

    def test_returns_clarification_when_context_missing(self) -> None:
        state = AgentState(
            missing_context=["location"],
            clarification_question="В каком городе посмотреть погоду?",
            requires_web_search=True,
        )

        result = resolve_task_context_node(state)

        assert result["response_text"] == "В каком городе посмотреть погоду?"
        assert result["response_type"] == "text"
        assert result["requires_web_search"] is False

    def test_blocks_empty_search_query(self) -> None:
        state = AgentState(
            requires_web_search=True,
            search_query="",
        )

        result = resolve_task_context_node(state)

        assert "что именно нужно найти" in result["response_text"]

    def test_allows_valid_browser_goal(self) -> None:
        state = AgentState(
            requires_browser=True,
            browser_goal="зайди на hh.ru и откликнись на 3 вакансии",
        )

        result = resolve_task_context_node(state)

        assert result == {}
