"""Tests for Celery tasks."""

from unittest.mock import patch, MagicMock


from src.thirdhand.celery_app.tasks import (
    send_reminder_notification,
    periodic_interest_search,
    cleanup_old_reminders,
    schedule_reminder,
)


class TestSendReminderNotification:
    """Tests for send_reminder_notification task."""

    def test_task_exists(self) -> None:
        """Test that the task is defined."""
        assert send_reminder_notification is not None

    def test_task_has_retry(self) -> None:
        """Test that the task has retry configuration."""
        assert send_reminder_notification.max_retries == 3


class TestPeriodicInterestSearch:
    """Tests for periodic_interest_search task."""

    def test_task_exists(self) -> None:
        """Test that the task is defined."""
        assert periodic_interest_search is not None


class TestCleanupOldReminders:
    """Tests for cleanup_old_reminders task."""

    def test_task_exists(self) -> None:
        """Test that the task is defined."""
        assert cleanup_old_reminders is not None


class TestScheduleReminder:
    """Tests for schedule_reminder task."""

    @patch("src.thirdhand.celery_app.tasks.send_reminder_notification")
    def test_schedules_reminder(self, mock_task: MagicMock) -> None:
        """Test that schedule_reminder creates a Celery task."""
        mock_task.apply_async = MagicMock(return_value=MagicMock(id="test-task-id"))

        result = schedule_reminder(1, "2026-04-30T14:00:00")

        mock_task.apply_async.assert_called_once()
        call_args = mock_task.apply_async.call_args
        assert call_args[1]["args"] == [1]
        assert result == "test-task-id"
