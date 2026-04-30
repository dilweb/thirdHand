"""Shared pytest fixtures for tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message, User

from src.thirdhand.agent.state import AgentState


@pytest.fixture
def mock_user() -> User:
    """Create a mock Telegram User."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        username="test_user",
        language_code="ru",
    )


@pytest.fixture
def mock_message(mock_user: User) -> Message:
    """Create a mock Telegram Message."""
    msg = MagicMock(spec=Message)
    msg.from_user = mock_user
    msg.message_id = 1
    msg.text = ""
    msg.answer = AsyncMock()
    return msg


@pytest.fixture
def mock_session() -> AsyncMock:
    """Create a mock async database session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def agent_state() -> AgentState:
    """Create a default AgentState."""
    return AgentState(
        user_id=123456789,
        message_text="",
    )


@pytest.fixture
def reminder_state(agent_state: AgentState) -> AgentState:
    """Create an AgentState for reminder testing."""
    agent_state.message_text = "Напомни в четверг в 2 часа о собеседовании"
    agent_state.intent = "reminder"
    agent_state.reminder_title = "собеседование"
    agent_state.reminder_datetime = "2026-04-30T14:00:00"
    agent_state.reminder_description = "Собеседование на позицию разработчика"
    return agent_state


@pytest.fixture
def search_state(agent_state: AgentState) -> AgentState:
    """Create an AgentState for search testing."""
    agent_state.message_text = "Найди новости про AI"
    agent_state.intent = "search"
    agent_state.search_query = "AI news"
    return agent_state


@pytest.fixture
def chat_state(agent_state: AgentState) -> AgentState:
    """Create an AgentState for chat testing."""
    agent_state.message_text = "Привет, как дела?"
    agent_state.intent = "chat"
    return agent_state


@pytest.fixture
def profile_state(agent_state: AgentState) -> AgentState:
    """Create an AgentState for profile update testing."""
    agent_state.message_text = "Я работаю питон разработчиком"
    agent_state.intent = "profile_update"
    agent_state.profile_updates = {
        "topic": "programming",
        "keywords": ["python", "developer"],
    }
    return agent_state
