"""Aiogram bot initialization."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from src.thirdhand.config import settings


def create_bot() -> Bot:
    """Create and configure the Telegram bot.

    Returns:
        Configured Bot instance.
    """
    return Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )


def create_dispatcher() -> Dispatcher:
    """Create and configure the Dispatcher.

    Returns:
        Configured Dispatcher instance.
    """
    dp = Dispatcher()
    return dp
