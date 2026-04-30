"""Bot entry point - starts the Aiogram bot with all components."""

import asyncio

import structlog

from src.thirdhand.bot.app import create_bot, create_dispatcher
from src.thirdhand.bot.handlers import main_router
from src.thirdhand.bot.middlewares import (
    DbSessionMiddleware,
    HistoryMiddleware,
    UserSyncMiddleware,
)
from src.thirdhand.config import settings
from src.thirdhand.services.logging_config import setup_logging

logger = structlog.get_logger(__name__)


def setup_dispatcher(dp) -> None:
    """Register middlewares and routers on the dispatcher."""
    # All middlewares must be outer so session is available to all of them.
    # Order: first registered = first called
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.update.outer_middleware(UserSyncMiddleware())
    dp.update.outer_middleware(HistoryMiddleware())

    # Routers
    dp.include_router(main_router)


async def main() -> None:
    """Start the bot."""
    # Configure structlog
    setup_logging(level="INFO")

    if not settings.BOT_TOKEN:
        logger.error("bot_token_missing", hint="Set BOT_TOKEN in .env file.")
        raise

    bot = create_bot()
    dp = create_dispatcher()

    setup_dispatcher(dp)

    logger.info("bot_starting")
    me = await bot.me()
    logger.info("bot_started", username=me.username, bot_id=me.id)

    # Start polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("bot_stopped", reason="user_interrupt")
