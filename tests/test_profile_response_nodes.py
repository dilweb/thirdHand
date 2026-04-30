"""Tests for profile and response nodes."""

from src.thirdhand.agent.nodes.profile import update_profile_node
from src.thirdhand.agent.nodes.response import generate_response_node
from src.thirdhand.agent.state import AgentState


class TestUpdateProfileNode:
    """Tests for update_profile_node."""

    def test_update_with_topic_and_keywords(self) -> None:
        """Test updating profile with topic and keywords."""
        state = AgentState(
            user_id=123,
            profile_updates={
                "topic": "programming",
                "keywords": ["python", "langchain"],
            },
        )

        result = update_profile_node(state)

        assert result["response_type"] == "text"
        assert "запомнил" in result["response_text"]
        assert "programming" in result["response_text"]
        assert "python" in result["response_text"]

    def test_update_with_topic_only(self) -> None:
        """Test updating profile with topic only."""
        state = AgentState(
            user_id=123,
            profile_updates={
                "topic": "AI",
                "keywords": [],
            },
        )

        result = update_profile_node(state)

        assert "AI" in result["response_text"]
        assert "Ключевые слова" not in result["response_text"]

    def test_update_empty(self) -> None:
        """Test updating profile with empty data."""
        state = AgentState(
            user_id=123,
            profile_updates={},
        )

        result = update_profile_node(state)

        assert result["response_type"] == "text"
        assert "запомнил" in result["response_text"]


class TestGenerateResponseNode:
    """Tests for generate_response_node."""

    def test_passthrough_existing_response(self) -> None:
        """Test that existing response is passed through."""
        state = AgentState(
            user_id=123,
            response_text="✅ Напоминание создано",
            response_type="reminder_confirm",
        )

        result = generate_response_node(state)

        assert result["response_text"] == "✅ Напоминание создано"
        assert result["response_type"] == "reminder_confirm"

    def test_passthrough_search_response(self) -> None:
        """Test that search response is passed through."""
        state = AgentState(
            user_id=123,
            response_text="🔍 Вот что нашёл:\n1. Result",
            response_type="search_results",
        )

        result = generate_response_node(state)

        assert result["response_text"] == "🔍 Вот что нашёл:\n1. Result"
        assert result["response_type"] == "search_results"
