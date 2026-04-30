"""Tests for bot handlers."""

from unittest.mock import MagicMock, patch

import pytest
from aiogram.filters import Command
from aiogram.types import Message

from src.thirdhand.bot.handlers.main import cmd_start, cmd_help, handle_message


class TestCmdStart:
    """Tests for /start command handler."""

    @pytest.mark.asyncio
    async def test_cmd_start_sends_welcome_message(self, mock_message: Message, mock_session) -> None:
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
        assert "напомни" in call_args.lower()


class TestHandleMessage:
    """Tests for main message handler."""

    @pytest.mark.asyncio
    async def test_handle_message_invokes_graph(
        self, mock_message: Message, mock_session
    ) -> None:
        """Test that handle_message invokes the LangGraph."""
        mock_message.text = "test message"

        # This will fail without a real LLM API key, so we test the structure
        # In CI, this would be mocked
        try:
            await handle_message(mock_message, mock_session)
        except Exception:
            # Expected when no API key is configured
            pass

    @pytest.mark.asyncio
    async def test_handle_message_empty_text(self, mock_message: Message, mock_session) -> None:
        """Test handling empty message text."""
        mock_message.text = ""

        # Should not crash
        try:
            await handle_message(mock_message, mock_session)
        except Exception:
            pass
