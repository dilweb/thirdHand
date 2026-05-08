"""Tests for reminder flow nodes."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from src.thirdhand.agent.nodes.reminder import validate_datetime_node, save_reminder_node
from src.thirdhand.agent.state import AgentState


class TestValidateDatetimeNode:
    """Tests for validate_datetime_node."""

    def test_valid_datetime(self) -> None:
        """Test parsing a valid datetime string."""
        future_dt = "2030-04-30T14:00:00"
        state = AgentState(
            user_id=123,
            reminder_datetime=future_dt,
        )

        result = validate_datetime_node(state)

        assert "reminder_datetime" in result
        assert "2030-04-30" in result["reminder_datetime"]

    def test_natural_language_datetime(self) -> None:
        """Test parsing natural language datetime."""
        state = AgentState(
            user_id=123,
            reminder_datetime="tomorrow at 3pm",
        )

        result = validate_datetime_node(state)

        assert "reminder_datetime" in result
        # dateparser should parse this successfully

    def test_weekday_prefers_future_date(self) -> None:
        """Weekday-only reminders should resolve to the next future occurrence."""
        tz = pytz.timezone("Asia/Almaty")
        now = datetime.now(tz)
        state = AgentState(
            user_id=123,
            reminder_datetime="во вторник в 18:00",
        )

        result = validate_datetime_node(state)

        assert "reminder_datetime" in result
        parsed = datetime.fromisoformat(result["reminder_datetime"])
        assert parsed > now
        assert parsed.weekday() == 1

    def test_empty_datetime(self) -> None:
        """Test handling of empty datetime."""
        state = AgentState(
            user_id=123,
            reminder_datetime="",
        )

        result = validate_datetime_node(state)

        assert result["response_type"] == "error"
        assert "Укажи" in result["response_text"]

    def test_invalid_datetime(self) -> None:
        """Test handling of invalid datetime."""
        state = AgentState(
            user_id=123,
            reminder_datetime="not a valid datetime at all xyz123",
        )

        result = validate_datetime_node(state)

        # dateparser may or may not parse this, but the node should handle it
        assert "reminder_datetime" in result or result.get("response_type") == "error"


class TestSaveReminderNode:
    """Tests for save_reminder_node."""

    @pytest.mark.asyncio
    async def test_save_reminder_with_title(self) -> None:
        """Test saving a reminder with a title."""
        mock_session = AsyncMock()
        mock_reminder = MagicMock(id=1, celery_task_id=None)
        state = AgentState(
            user_id=123,
            reminder_title="Meeting",
            reminder_datetime="2026-04-30T14:00:00",
            reminder_description="Team standup",
            db_session=mock_session,
        )

        with (
            patch(
                "src.thirdhand.agent.nodes.reminder.ReminderQueries.create_reminder",
                new=AsyncMock(return_value=mock_reminder),
            ),
            patch("src.thirdhand.agent.nodes.reminder.schedule_reminder", return_value="task-123"),
        ):
            result = await save_reminder_node(state)

        assert result["response_type"] == "reminder_confirm"
        assert "Meeting" in result["response_text"]
        assert result["reminder_title"] == "Meeting"
        assert result["reminder_id"] == 1

    @pytest.mark.asyncio
    async def test_save_reminder_from_entities(self) -> None:
        """Test saving a reminder from entities."""
        mock_session = AsyncMock()
        mock_reminder = MagicMock(id=2, celery_task_id=None)
        state = AgentState(
            user_id=123,
            entities={
                "title": "Doctor appointment",
                "remind_at": "2026-05-01T10:00:00",
                "description": "Annual checkup",
            },
            db_session=mock_session,
            reminder_datetime="2026-05-01T10:00:00",
        )

        with (
            patch(
                "src.thirdhand.agent.nodes.reminder.ReminderQueries.create_reminder",
                new=AsyncMock(return_value=mock_reminder),
            ),
            patch("src.thirdhand.agent.nodes.reminder.schedule_reminder", return_value="task-456"),
        ):
            result = await save_reminder_node(state)

        assert result["response_type"] == "reminder_confirm"
        assert "Doctor appointment" in result["response_text"]

    @pytest.mark.asyncio
    async def test_save_reminder_default_title(self) -> None:
        """Test saving a reminder with default title."""
        mock_session = AsyncMock()
        mock_reminder = MagicMock(id=3, celery_task_id=None)
        state = AgentState(
            user_id=123,
            reminder_datetime="2026-04-30T14:00:00",
            db_session=mock_session,
        )

        with (
            patch(
                "src.thirdhand.agent.nodes.reminder.ReminderQueries.create_reminder",
                new=AsyncMock(return_value=mock_reminder),
            ),
            patch("src.thirdhand.agent.nodes.reminder.schedule_reminder", return_value="task-789"),
        ):
            result = await save_reminder_node(state)

        assert result["response_type"] == "reminder_confirm"
        assert "Напоминание" in result["response_text"]
