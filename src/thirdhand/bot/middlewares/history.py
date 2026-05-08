"""History middleware - loads conversation history from Redis."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.thirdhand.services import redis_history

logger = structlog.get_logger(__name__)


class HistoryMiddleware(BaseMiddleware):
    """Middleware that loads conversation history.

    Before handler: loads history from Redis into data['history']
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Load history before, save after."""
        if not isinstance(event, Message) or not event.text:
            return await handler(event, data)

        user_id = event.from_user.id
        # Load history from Redis
        history = await redis_history.get_history(
            user_id,
            limit=20,  # Load last 20 messages for context
        )
        data["history"] = history

        logger.debug(
            "history_loaded",
            user_id=user_id,
            message_count=len(history),
        )

        return await handler(event, data)
