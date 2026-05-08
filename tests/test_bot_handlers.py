"""Tests for bot handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from src.thirdhand.bot.handlers.main import (
    _sync_pending_task,
    cmd_help,
    cmd_reset_session,
    cmd_start,
    handle_message,
)
from src.thirdhand.models import UserProfileQueries

_GRAPH_STUB_RESULT = {
    "response_text": "OK",
    "response_type": "text",
    "browser_needs_user_input": True,
}


class TestCmdStart:
    """Tests for /start command handler."""

    @pytest.mark.asyncio
    async def test_cmd_start_sends_welcome_message(
        self, mock_message: Message, mock_session
    ) -> None:
        """Test that /start sends a welcome message."""
        with patch("src.thirdhand.bot.handlers.main.UserProfileQueries.get_or_create") as mock_get:
            mock_profile = MagicMock()
            mock_profile.context_summary = {}
            mock_get.return_value = mock_profile

            await cmd_start(mock_message, mock_session)

        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args[0][0]
        assert "thirdHand" in call_args
        assert "Напоминать" in call_args
        assert "Искать" in call_args


class TestCmdHelp:
    """Tests for /help command handler."""

    @pytest.mark.asyncio
    async def test_cmd_help_sends_help_message(self, mock_message: Message) -> None:
        """Test that /help sends a help message."""
        await cmd_help(mock_message)

        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args[0][0]
        assert "/start" in call_args
        assert "/help" in call_args
        assert "/reset_session" in call_args
        assert "напомни" in call_args.lower()


class TestCmdResetSession:
    """Tests for /reset_session command."""

    @pytest.mark.asyncio
    async def test_reset_session_clears_redis_and_parked_browser(
        self, mock_message: Message
    ) -> None:
        with (
            patch(
                "src.thirdhand.bot.handlers.main.discard_parked_browser_session_for_user",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_discard,
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.clear_session_redis",
                new_callable=AsyncMock,
                return_value=(True, True),
            ) as mock_clear,
        ):
            await cmd_reset_session(mock_message)

        mock_discard.assert_awaited_once_with(mock_message.from_user.id)
        mock_clear.assert_awaited_once_with(mock_message.from_user.id)
        mock_message.answer.assert_awaited_once()
        body = mock_message.answer.call_args[0][0]
        assert "Сессия сброшена" in body
        assert "История сообщений: удалена" in body
        assert "pending" in body.lower() or "Незавершённая" in body
        assert "закрыт" in body


class TestHandleMessage:
    """Tests for main message handler."""

    @pytest.mark.asyncio
    async def test_handle_message_invokes_graph(self, mock_message: Message, mock_session) -> None:
        """Test that handle_message invokes the LangGraph."""
        mock_message.text = "test message"
        mock_profile = MagicMock()
        mock_profile.context_summary = {}
        mock_profile.session_summaries = []

        with (
            patch("src.thirdhand.bot.handlers.main.graph") as mock_graph,
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.push_message", new_callable=AsyncMock
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_pending_task",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.clear_pending_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
                new_callable=AsyncMock,
            ),
            patch.object(
                UserProfileQueries,
                "get_or_create",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            mock_graph.ainvoke = AsyncMock(return_value=dict(_GRAPH_STUB_RESULT))
            await handle_message(mock_message, mock_session)

        mock_graph.ainvoke.assert_awaited_once()
        mock_message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_handle_message_empty_text(self, mock_message: Message, mock_session) -> None:
        """Test handling empty message text."""
        mock_message.text = ""
        mock_profile = MagicMock()
        mock_profile.context_summary = {}
        mock_profile.session_summaries = []

        with (
            patch("src.thirdhand.bot.handlers.main.graph") as mock_graph,
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.push_message", new_callable=AsyncMock
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_pending_task",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.clear_pending_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
                new_callable=AsyncMock,
            ),
            patch.object(
                UserProfileQueries,
                "get_or_create",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            mock_graph.ainvoke = AsyncMock(return_value=dict(_GRAPH_STUB_RESULT))
            await handle_message(mock_message, mock_session)

        mock_graph.ainvoke.assert_awaited_once()
        mock_message.answer.assert_awaited()

    @pytest.mark.asyncio
    async def test_handle_message_routes_diagnostic_question_through_graph(
        self, mock_message: Message, mock_session
    ) -> None:
        """Questions about an active browser task should still go through the graph, not a handler shortcut."""
        mock_message.text = "ты использовал тул для распознавания картинки?"
        mock_profile = MagicMock()
        mock_profile.context_summary = {}
        mock_profile.session_summaries = []

        pending = {
            "intent": "browser_task",
            "awaiting_user_step": True,
            "browser_final_url": "https://example.com/2fa",
        }

        with (
            patch("src.thirdhand.bot.handlers.main.graph") as mock_graph,
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_history",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.push_message", new_callable=AsyncMock
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.get_pending_task",
                new_callable=AsyncMock,
                return_value=pending,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.clear_pending_task",
                new_callable=AsyncMock,
            ),
            patch(
                "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
                new_callable=AsyncMock,
            ),
            patch.object(
                UserProfileQueries,
                "get_or_create",
                new_callable=AsyncMock,
                return_value=mock_profile,
            ),
        ):
            mock_graph.ainvoke = AsyncMock(return_value=dict(_GRAPH_STUB_RESULT))
            await handle_message(mock_message, mock_session)

        mock_graph.ainvoke.assert_awaited_once()
        mock_message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_sync_pending_task_persists_browser_barrier_fields() -> None:
    """Stage 13: structured browser fields round-trip into the pending-task envelope."""
    result = {
        "browser_needs_user_input": True,
        "browser_goal": "open example",
        "canonical_user_objective": "visit example.com",
        "user_goal": "visit example.com",
        "browser_blocker_type": "login",
        "browser_final_url": "https://example.com/login",
        "browser_debug_note": "bootstrap blocked",
        "browser_auth_facts": {"facts_version": 1},
        "browser_barrier_kind": "login",
        "browser_barrier_facts": {"facts_version": 1, "page_url": "https://example.com/login"},
        "browser_next_user_action": "Sign in manually",
        "browser_resume_strategy": "await_user_message",
        "browser_sub_intent": "browser_apply_to_targets",
        "response_text": "Нужно твоё действие",
    }
    with patch(
        "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
        new_callable=AsyncMock,
    ) as mock_set:
        await _sync_pending_task(99, "hi", result)

    mock_set.assert_awaited_once()
    payload = mock_set.call_args[0][1]
    assert payload["browser_barrier_kind"] == "login"
    assert payload["browser_barrier_facts"]["page_url"] == "https://example.com/login"
    assert payload["browser_next_user_action"] == "Sign in manually"
    assert payload["browser_resume_strategy"] == "await_user_message"
    assert payload["browser_sub_intent"] == "browser_apply_to_targets"
    assert payload["browser_auth_facts"]["facts_version"] == 1
    assert payload["canonical_user_objective"] == "visit example.com"
    assert payload["user_goal"] == "visit example.com"


@pytest.mark.asyncio
async def test_sync_pending_browser_clarification_sets_awaiting_step() -> None:
    """Clarifying questions for browser_task must keep pending resumable (awaiting + blocker)."""
    result = {
        "intent": "browser_task",
        "requires_browser": True,
        "missing_context": ["phone_number"],
        "clarification_question": "Пришлите номер",
        "browser_goal": "Вход на hh",
        "user_goal": "hh",
        "routing_reason": "test",
    }
    with patch(
        "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
        new_callable=AsyncMock,
    ) as mock_set:
        await _sync_pending_task(7, "зайди на hh", result)

    mock_set.assert_awaited_once()
    payload = mock_set.call_args[0][1]
    assert payload["awaiting_user_step"] is True
    assert payload["blocker_type"] == "missing_info"


@pytest.mark.asyncio
async def test_sync_pending_task_preserves_active_browser_task_for_chat_followup() -> None:
    pending = {
        "intent": "browser_task",
        "requires_browser": True,
        "awaiting_user_step": True,
        "browser_goal": "Откликнуться на вакансии",
        "browser_final_url": "https://hh.ru/search/vacancy",
    }
    result = {
        "intent": "chat",
        "preserve_pending_task": True,
        "active_task_context": pending,
    }

    with (
        patch(
            "src.thirdhand.bot.handlers.main.redis_history.set_pending_task",
            new_callable=AsyncMock,
        ) as mock_set,
        patch(
            "src.thirdhand.bot.handlers.main.redis_history.clear_pending_task",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        await _sync_pending_task(7, "что случилось?", result)

    mock_set.assert_awaited_once_with(7, pending)
    mock_clear.assert_not_awaited()
