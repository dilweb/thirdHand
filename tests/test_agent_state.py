"""Tests for AgentState."""

from src.thirdhand.agent.state import AgentState


class TestAgentState:
    """Tests for AgentState dataclass."""

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        state = AgentState()

        assert state.user_id == 0
        assert state.message_text == ""
        assert state.intent == "chat"
        assert state.entities == {}
        assert state.reminder_id is None
        assert state.reminder_title == ""
        assert state.reminder_datetime == ""
        assert state.reminder_description == ""
        assert state.search_query == ""
        assert state.search_results == []
        assert state.profile_updates == {}
        assert state.response_text == ""
        assert state.response_type == "text"
        assert state.conversation_history == []
        assert state.user_profile == {}

    def test_custom_values(self) -> None:
        """Test that custom values can be set."""
        state = AgentState(
            user_id=123,
            message_text="test message",
            intent="reminder",
            reminder_title="Meeting",
            reminder_datetime="2026-04-30T14:00:00",
        )

        assert state.user_id == 123
        assert state.message_text == "test message"
        assert state.intent == "reminder"
        assert state.reminder_title == "Meeting"
        assert state.reminder_datetime == "2026-04-30T14:00:00"

    def test_search_results_append(self) -> None:
        """Test that search results can be appended."""
        state = AgentState()
        state.search_results.append({"title": "Result 1"})
        state.search_results.append({"title": "Result 2"})

        assert len(state.search_results) == 2
        assert state.search_results[0]["title"] == "Result 1"

    def test_conversation_history_append(self) -> None:
        """Test that conversation history can be appended."""
        state = AgentState()
        state.conversation_history.append({"role": "user", "content": "Hello"})
        state.conversation_history.append({"role": "assistant", "content": "Hi!"})

        assert len(state.conversation_history) == 2
