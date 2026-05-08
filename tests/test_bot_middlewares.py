"""Tests for bot middlewares."""

from unittest.mock import MagicMock, patch

import pytest
from aiogram.types import Message

from src.thirdhand.bot.middlewares.db_session import DbSessionMiddleware
from src.thirdhand.bot.middlewares.user_sync import UserSyncMiddleware


class TestDbSessionMiddleware:
    """Tests for DbSessionMiddleware."""

    @pytest.mark.asyncio
    async def test_injects_session(self, mock_message: Message, mock_session) -> None:
        """Test that middleware injects session into handler data."""
        middleware = DbSessionMiddleware()
        data: dict = {}

        async def mock_handler(event, data):
            assert "session" in data
            return "ok"

        # We need to mock the session factory
        # This is a simplified test - in production, use a real test DB
        result = await middleware(mock_handler, mock_message, data)

        assert result == "ok"


class TestUserSyncMiddleware:
    """Tests for UserSyncMiddleware."""

    @pytest.mark.asyncio
    async def test_syncs_user_on_message(self, mock_message: Message, mock_session) -> None:
        """Test that middleware syncs user data on message."""
        middleware = UserSyncMiddleware()
        data: dict = {"session": mock_session}

        async def mock_handler(event, data):
            return "ok"

        # Mock the get_or_create to return a tuple (user, created)
        with patch("src.thirdhand.models.queries.UserQueries.get_or_create") as mock_get:
            mock_user_obj = MagicMock()
            mock_user_obj.username = None
            mock_user_obj.first_name = None
            mock_user_obj.last_name = None
            mock_user_obj.language_code = None
            mock_get.return_value = (mock_user_obj, True)

            result = await middleware(mock_handler, mock_message, data)

        assert result == "ok"
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_missing_session(self, mock_message: Message) -> None:
        """Test handling when session is not in data."""
        middleware = UserSyncMiddleware()
        data: dict = {}

        async def mock_handler(event, data):
            return "ok"

        # Should not crash when session is missing
        result = await middleware(mock_handler, mock_message, data)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_syncs_callback_query(self, mock_session) -> None:
        """Test that middleware syncs user data on CallbackQuery."""
        from aiogram.types import CallbackQuery

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 999
        callback.from_user.username = "callback_user"
        callback.from_user.first_name = "Callback"
        callback.from_user.last_name = None
        callback.from_user.language_code = "en"
        callback.id = "12345"
        callback.data = "button_click"

        middleware = UserSyncMiddleware()
        data: dict = {"session": mock_session}

        async def mock_handler(event, data):
            return "ok"

        with patch("src.thirdhand.models.queries.UserQueries.get_or_create") as mock_get:
            mock_user_obj = MagicMock()
            mock_get.return_value = (mock_user_obj, True)

            result = await middleware(mock_handler, callback, data)

        assert result == "ok"
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_event_without_user(self, mock_session) -> None:
        """Test that middleware skips events without user."""
        # ChatMemberUpdated without from_user
        event = MagicMock()
        event.from_user = None

        middleware = UserSyncMiddleware()
        data: dict = {"session": mock_session}

        async def mock_handler(event, data):
            return "ok"

        result = await middleware(mock_handler, event, data)

        assert result == "ok"
