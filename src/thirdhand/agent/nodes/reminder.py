"""Reminder flow nodes - validate datetime and save reminder."""

import dateparser
import pytz

from src.thirdhand.agent.state import AgentState
from src.thirdhand.config import settings


def validate_datetime_node(state: AgentState) -> dict:
    """Validate and parse the reminder datetime.

    Uses dateparser to convert natural language to a datetime object.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with validated datetime or error.
    """
    raw_datetime = state.reminder_datetime or state.entities.get("remind_at", "")

    if not raw_datetime:
        return {
            "response_text": "⚠️ Укажи, пожалуйста, когда напомнить (например, 'в четверг в 2 часа').",
            "response_type": "error",
        }

    tz_name = settings.DEFAULT_TIMEZONE
    parsed = dateparser.parse(raw_datetime, settings={"TIMEZONE": tz_name, "RETURN_AS_TIMEZONE_AWARE": True})

    if parsed is None:
        return {
            "response_text": f"⚠️ Не удалось распознать дату/время: '{raw_datetime}'. Попробуй ещё раз.",
            "response_type": "error",
        }

    return {
        "reminder_datetime": parsed.isoformat(),
    }


def save_reminder_node(state: AgentState) -> dict:
    """Save the reminder to the database.

    Note: Actual DB save happens via Celery task; this node prepares the data.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with confirmation message.
    """
    title = state.reminder_title or state.entities.get("title", "Напоминание")
    description = state.reminder_description or state.entities.get("description", "")
    remind_at = state.reminder_datetime

    return {
        "response_text": f"✅ Напоминание '{title}' создано на {remind_at}",
        "response_type": "reminder_confirm",
        "reminder_title": title,
        "reminder_description": description,
        "reminder_datetime": remind_at,
    }
