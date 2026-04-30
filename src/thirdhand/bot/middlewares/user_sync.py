"""User sync middleware - creates/updates user in DB on every event."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineQuery, Message, PreCheckoutQuery, TelegramObject

from src.thirdhand.models import UserQueries

logger = structlog.get_logger(__name__)

# Types that can have `from_user` attribute
EVENTS_WITH_USER = (
    Message,
    CallbackQuery,
    InlineQuery,
    PreCheckoutQuery,
    ChatMemberUpdated,
)


def extract_user_from_event(event: TelegramObject):
    """Extract user object from various event types.

    Returns None if the event type doesn't have a user.
    """
    if isinstance(event, Message):
        return event.from_user
    if isinstance(event, CallbackQuery):
        return event.from_user
    if isinstance(event, InlineQuery):
        return event.from_user
    if isinstance(event, PreCheckoutQuery):
        return event.from_user
    if isinstance(event, ChatMemberUpdated):
        return event.from_user
    return None


def get_event_description(event: TelegramObject) -> str:
    """Get a human-readable description of the event for logging."""
    try:
        if isinstance(event, Message):
            return f"Message(id={event.message_id}, chat_id={event.chat.id})"
        if isinstance(event, CallbackQuery):
            return f"CallbackQuery(id={event.id}, data={event.data!r})"
        if isinstance(event, InlineQuery):
            return f"InlineQuery(id={event.id}, query={event.query!r})"
        if isinstance(event, PreCheckoutQuery):
            return f"PreCheckoutQuery(id={event.id})"
        if isinstance(event, ChatMemberUpdated):
            return f"ChatMemberUpdated(chat_id={event.chat.id})"
    except AttributeError:
        pass  # Mock objects may not have all attributes
    return f"{event.__class__.__name__}"


class UserSyncMiddleware(BaseMiddleware):
    """Middleware that syncs user data from Telegram to DB.

    This middleware runs before every handler and ensures the user exists
    in the database with up-to-date information from Telegram.

    Supported event types:
    - Message (text, photo, document, etc.)
    - CallbackQuery (inline button presses)
    - InlineQuery (inline mode)
    - PreCheckoutQuery (payments)
    - ChatMemberUpdated (member joined/left)

    If session is not available, the middleware logs a warning and continues.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Sync user data before handling the event."""
        # Extract user from the event
        user = extract_user_from_event(event)

        if user is None:
            # Event type doesn't have a user (e.g., MyChatMember, ErrorEvent)
            logger.debug("Event %s has no user, skipping sync", event.__class__.__name__)
            return await handler(event, data)

        # Get session from data
        session = data.get("session")
        if session is None:
            # No DB session available — this means DbSessionMiddleware wasn't added
            # or was added after this middleware
            logger.warning(
                "No DB session available for %s — is DbSessionMiddleware registered?",
                get_event_description(event),
            )
            return await handler(event, data)

        # Sync user to database
        try:
            await UserQueries.update_from_telegram(
                session,
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                language_code=user.language_code,
            )
            logger.debug("Synced user %d (@%s)", user.id, user.username or "no_username")
        except Exception:
            logger.exception("Failed to sync user %d", user.id)
            # Don't fail the event — continue anyway

        return await handler(event, data)
