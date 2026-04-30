"""History middleware - loads conversation history from Redis and saves after response."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from src.thirdhand.services import redis_history

logger = structlog.get_logger(__name__)


class HistoryMiddleware(BaseMiddleware):
    """Middleware that loads and saves conversation history.

    Before handler: loads history from Redis into data['history']
    After handler: saves user message and bot response to Redis
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
        user_text = event.text

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

        # Call handler
        result = await handler(event, data)

        # Save user message to history
        await redis_history.push_message(user_id, "user", user_text)

        # If handler sent a response, save it too
        # The response is stored in data by the handler
        bot_response = data.get("bot_response")
        if bot_response:
            await redis_history.push_message(user_id, "assistant", bot_response)
            logger.debug(
                "history_saved",
                user_id=user_id,
                messages=2,
            )

        return result
