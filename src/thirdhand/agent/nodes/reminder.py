"""Reminder flow nodes - validate datetime and save reminder."""

from datetime import datetime, timedelta

import dateparser
import pytz
import structlog

from src.thirdhand.agent.state import AgentState
from src.thirdhand.celery_app.tasks import schedule_reminder
from src.thirdhand.config import settings
from src.thirdhand.models import ReminderQueries

logger = structlog.get_logger(__name__)

RUSSIAN_WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "сред": 2,
    "четверг": 3,
    "пятниц": 4,
    "суббот": 5,
    "воскрес": 6,
}


def _weekday_hint(text: str) -> int | None:
    """Extract weekday index from Russian natural language text, if present."""
    lowered = text.lower()
    for token, weekday_index in RUSSIAN_WEEKDAYS.items():
        if token in lowered:
            return weekday_index
    return None


def _shift_to_next_weekday_if_needed(raw_text: str, parsed: datetime, now: datetime) -> datetime:
    """Force weekday-only phrases like 'во вторник' into the next future occurrence."""
    weekday_index = _weekday_hint(raw_text)
    if weekday_index is None:
        return parsed

    # If the parser already chose the intended future weekday, keep it.
    if parsed > now and parsed.weekday() == weekday_index:
        return parsed

    days_ahead = (weekday_index - now.weekday()) % 7
    candidate = parsed

    if days_ahead == 0:
        candidate = parsed + timedelta(days=7)
    else:
        candidate = parsed + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)

    return candidate


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
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    parsed = dateparser.parse(
        raw_datetime,
        settings={
            "TIMEZONE": tz_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now,
        },
    )

    if parsed is None:
        return {
            "response_text": f"⚠️ Не удалось распознать дату/время: '{raw_datetime}'. Попробуй ещё раз.",
            "response_type": "error",
        }

    parsed = _shift_to_next_weekday_if_needed(raw_datetime, parsed, now)

    if parsed <= now:
        return {
            "response_text": "⚠️ Похоже, это время уже прошло. Укажи, пожалуйста, будущую дату или время.",
            "response_type": "error",
        }

    return {
        "reminder_datetime": parsed.isoformat(),
    }


async def save_reminder_node(state: AgentState) -> dict:
    """Save the reminder to the database.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with confirmation message and persisted reminder metadata.
    """
    title = state.reminder_title or state.entities.get("title", "Напоминание")
    description = state.reminder_description or state.entities.get("description", "")
    remind_at = state.reminder_datetime
    remind_at_dt = datetime.fromisoformat(remind_at)
    session = state.db_session

    if session is None:
        logger.error("reminder_save_missing_session", user_id=state.user_id)
        return {
            "response_text": "⚠️ Не удалось сохранить напоминание: нет доступа к базе данных.",
            "response_type": "error",
        }

    try:
        reminder = await ReminderQueries.create_reminder(
            session=session,
            user_id=state.user_id,
            title=title,
            remind_at=remind_at_dt,
            description=description or None,
        )
        task_id = schedule_reminder(reminder.id, remind_at)
        reminder.celery_task_id = task_id
        await session.flush()
        logger.info(
            "reminder_saved_and_scheduled",
            user_id=state.user_id,
            reminder_id=reminder.id,
            celery_task_id=task_id,
            remind_at=remind_at,
        )
    except Exception as exc:
        logger.exception(
            "reminder_save_failed",
            user_id=state.user_id,
            error=str(exc),
        )
        return {
            "response_text": "⚠️ Не удалось сохранить напоминание. Попробуй ещё раз.",
            "response_type": "error",
        }

    return {
        "response_text": f"✅ Напоминание '{title}' создано на {remind_at}",
        "response_type": "reminder_confirm",
        "reminder_id": reminder.id,
        "reminder_title": title,
        "reminder_description": description,
        "reminder_datetime": remind_at,
        "entities": {
            **state.entities,
            "celery_task_id": task_id,
        },
    }
