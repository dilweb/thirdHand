"""Celery tasks for reminders and periodic searches."""

from datetime import datetime

import structlog

from src.thirdhand.celery_app import celery_app
from src.thirdhand.models import (
    InterestQueries,
    ReminderQueries,
    ReminderStatus,
    UserProfileQueries,
    get_session,
)
from src.thirdhand.services import redis_history
from src.thirdhand.services.llm import create_llm, safe_invoke

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_reminder_notification(self, reminder_id: int) -> None:
    """Send a reminder notification to the user.

    This task is scheduled by Celery Beat when a reminder is due.

    Args:
        reminder_id: ID of the reminder to send.
    """
    # TODO: Import aiogram bot here to avoid circular imports
    # from src.thirdhand.bot.app import create_bot
    # bot = create_bot()
    # asyncio.run(bot.send_message(chat_id=user_id, text=text))

    logger.info("Sending reminder notification for reminder %d", reminder_id)

    # For now, just log. Will be implemented when bot integration is ready.
    # In production, this would:
    # 1. Fetch reminder from DB
    # 2. Send message via bot
    # 3. Update reminder status to SENT


@celery_app.task
def periodic_interest_search() -> None:
    """Periodically search for content based on user interests.

    This task runs daily via Celery Beat.
    It fetches all user interests, searches for new content,
    and sends digests to users.
    """
    logger.info("Running periodic interest search...")

    # TODO: Implement search logic
    # 1. Fetch all interests from DB
    # 2. For each interest, run web search
    # 3. Filter results by relevance
    # 4. Send digest to user
    # 5. Update last_searched timestamp

    logger.info("Periodic interest search completed.")


@celery_app.task
def cleanup_old_reminders() -> None:
    """Clean up old reminders that have been sent.

    This task runs daily to keep the database clean.
    """
    logger.info("Cleaning up old reminders...")

    # TODO: Implement cleanup logic
    # Delete reminders older than 30 days with status = 'sent'

    logger.info("Old reminders cleaned up.")


@celery_app.task
def schedule_reminder(reminder_id: int, remind_at_iso: str) -> str:
    """Schedule a reminder notification via Celery Beat.

    Args:
        reminder_id: ID of the reminder.
        remind_at_iso: When to send the reminder (ISO format).

    Returns:
        Celery task ID.
    """
    from datetime import datetime

    remind_at = datetime.fromisoformat(remind_at_iso)

    # Schedule the task to run at the reminder time
    task = send_reminder_notification.apply_async(
        args=[reminder_id],
        eta=remind_at,
    )

    logger.info(
        "Scheduled reminder %d for %s (task: %s)",
        reminder_id,
        remind_at_iso,
        task.id,
    )

    return task.id


SUMMARIZE_PROMPT = """
Summarize this conversation session into key facts about the user.
Extract:
- Topics discussed
- Actions taken (reminders created, searches performed)
- New facts about the user (interests, preferences, occupation)
- Any open tasks or follow-ups

Return a JSON object with: date, topics (list), actions_taken (list), key_facts (list).
"""


@celery_app.task
def summarize_session_history(user_id: int) -> None:
    """Summarize expired Redis history and save to user profile.

    This task is triggered when Redis history TTL expires.
    It gets the history (if available), summarizes it, and merges
    into the user's session_summaries and context_summary.
    """
    import asyncio

    async def _run() -> None:
        # Get and clear history from Redis
        history = await redis_history.get_history(user_id)
        if not history:
            logger.info("no_history_to_summarize", user_id=user_id)
            return

        # Build summary via LLM
        llm = create_llm(temperature=0.0)
        from langchain_core.prompts import ChatPromptTemplate
        from pydantic import BaseModel, Field

        class SessionSummary(BaseModel):
            date: str = Field(default="")
            topics: list[str] = Field(default_factory=list)
            actions_taken: list[str] = Field(default_factory=list)
            key_facts: list[str] = Field(default_factory=list)

        prompt = ChatPromptTemplate.from_messages([
            ("system", SUMMARIZE_PROMPT),
            ("human", "Conversation:\n{history}"),
        ])

        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in history[:50]  # Limit for LLM
        )

        chain = prompt | llm.with_structured_output(SessionSummary)
        summary = safe_invoke(chain, {"history": history_text})

        if summary is None:
            logger.warning("summarization_failed", user_id=user_id)
            return

        # Save to DB
        async with get_session() as session:
            profile = await UserProfileQueries.get_or_create(session, user_id)

            # Add to session_summaries
            summaries = profile.session_summaries or []
            summary_dict = {
                "date": summary.date or datetime.utcnow().strftime("%Y-%m-%d"),
                "duration_minutes": len(history) // 4,  # Rough estimate
                "topics": summary.topics,
                "actions_taken": summary.actions_taken,
                "key_facts": summary.key_facts,
            }
            summaries.append(summary_dict)
            profile.session_summaries = summaries

            # Merge key facts into context_summary
            ctx = profile.context_summary or {}
            for fact in summary.key_facts:
                if "interests" not in ctx:
                    ctx["interests"] = []
                if fact not in ctx["interests"]:
                    ctx["interests"].append(fact)
            profile.context_summary = ctx

            await session.commit()

        # Clear history from Redis
        await redis_history.clear_history(user_id)

        logger.info(
            "session_summarized",
            user_id=user_id,
            topics=summary.topics,
            messages_processed=len(history),
        )

    asyncio.run(_run())
