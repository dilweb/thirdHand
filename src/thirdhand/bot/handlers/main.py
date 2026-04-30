"""Main message handler with LangGraph integration."""

import asyncio

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.thirdhand.agent.graph import graph
from src.thirdhand.agent.state import AgentState
from src.thirdhand.models import UserProfileQueries
from src.thirdhand.services import redis_history
from src.thirdhand.services.bio_extractor import extract_bio_facts, merge_facts
from src.thirdhand.services.context_builder import (
    build_context_prompt,
    compress_if_needed,
)

logger = structlog.get_logger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Handle /start command with welcome and bio request."""
    user_id = message.from_user.id

    # Check if this is a first-time user
    profile = await UserProfileQueries.get_or_create(session, user_id)
    is_new = not profile.context_summary

    welcome = (
        "👋 Привет! Я твоя <b>третья рука</b> — твой персональный AI-ассистент.\n"
        "Я могу:\n"
        "•  <b>Напоминать</b> о событиях\n"
        "•  <b>Искать</b> информацию\n"
        "•  <b>Подстраиваться</b> под твои интересы\n"
    )

    if is_new:
        welcome += (
            "\n\n🧠 <b>Давай познакомимся!</b>\n"
            "Расскажи немного о себе, чтобы я лучше помогал:\n"
            "• Чем занимаешься?\n"
            "• Какие инструменты используешь?\n"
            "• Что тебя интересует?\n"
            "\nИли просто начни общаться — я запомню важное!"
        )
    else:
        welcome += "\n\nПопробуй написать что-нибудь!"

    await message.answer(welcome)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command."""
    await message.answer(
        "📖 <b>Команды:</b>\n"
        "/start — начать\n"
        "/help — помощь\n"
        "\n💬 <b>Примеры:</b>\n"
        "• 'напомни завтра в 10:00 о созвоне'\n"
        "• 'найди новые модели OpenAI'\n"
        "• 'я работаю с Python и LangChain'"
    )


@router.message(F.text)
async def handle_message(message: Message, session: AsyncSession, history: list | None = None) -> None:
    """Handle text messages through LangGraph agent.

    Loads user profile, builds context, invokes graph, saves history.
    """
    user_id = message.from_user.id
    text = message.text

    logger.info("message_received", user_id=user_id, text_preview=text[:100])

    # Load user profile from DB
    profile = await UserProfileQueries.get_or_create(session, user_id)
    context_summary = profile.context_summary or {}
    session_summaries = profile.session_summaries or []

    # Compress if needed
    context_summary, session_summaries, history = compress_if_needed(
        context_summary,
        session_summaries,
        history or [],
    )

    # Build context prompt
    context_text = build_context_prompt(context_summary, session_summaries, history)

    # Create agent state with context
    state = AgentState(
        user_id=user_id,
        message_text=text,
        user_profile={
            "context_summary": context_summary,
            "session_summaries": session_summaries,
        },
        conversation_history=history[-10:] if history else [],
    )

    # Inject context into the state for response node
    state.user_profile["context_text"] = context_text

    # Invoke the graph
    try:
        result = await graph.ainvoke(state)
    except Exception as e:
        logger.exception("graph_invocation_failed", user_id=user_id, error=str(e))
        await message.answer(
            "⚠️ Произошла ошибка. Попробуй ещё раз позже."
        )
        return

    # Extract response
    response_text = result.get("response_text", "")
    response_type = result.get("response_type", "text")

    if not response_text:
        response_text = "🤔 Хм, не знаю что ответить. Попробуй переформулировать."

    # Send response
    await message.answer(response_text)

    # Store response in data for history middleware
    # (middleware will pick it up and save to Redis)
    # Note: middleware uses data["bot_response"], so we set it here
    # Actually, we need to save history differently since handler doesn't control middleware data
    # Let's save directly here
    await redis_history.push_message(user_id, "assistant", response_text)

    logger.info(
        "response_sent",
        user_id=user_id,
        response_type=response_type,
        response_preview=response_text[:100],
    )

    # Background: extract bio facts (non-blocking)
    asyncio.create_task(
        _background_bio_extract(
            user_id,
            text,
            response_text,
            context_summary,
        )
    )


async def _background_bio_extract(
    user_id: int,
    user_message: str,
    assistant_message: str,
    existing_profile: dict,
) -> None:
    """Extract and save bio facts in background."""
    from src.thirdhand.models import get_session

    facts = await extract_bio_facts(user_message, assistant_message, existing_profile)
    if facts:
        async with get_session() as session:
            profile = await UserProfileQueries.get_or_create(session, user_id)
            profile.context_summary = merge_facts(profile.context_summary, facts)
            await session.commit()
            logger.info("bio_saved", user_id=user_id)
