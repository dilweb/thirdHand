"""Main message handler with LangGraph integration."""

import asyncio
import base64
from datetime import datetime, UTC
from itertools import count
from uuid import uuid4

import structlog
from aiogram import F, Router
from aiogram.types import BufferedInputFile
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.thirdhand.agent.graph import graph
from src.thirdhand.agent.schemas import PendingTask
from src.thirdhand.agent.state import AgentState
from src.thirdhand.browser_core.api import discard_parked_browser_session_for_user
from src.thirdhand.models import UserProfileQueries
from src.thirdhand.services import redis_history
from src.thirdhand.services.bio_extractor import extract_bio_facts, merge_facts
from src.thirdhand.services.context_builder import (
    build_context_prompt,
    compress_if_needed,
)
from src.thirdhand.services.telegram_format import format_agent_reply_for_telegram

logger = structlog.get_logger(__name__)
router = Router()
_active_user_runs: dict[int, tuple[int, asyncio.Task]] = {}
_active_user_runs_lock = asyncio.Lock()
_active_user_run_ids = count(1)

_TELEGRAM_MESSAGE_MAX = 4096


async def _answer_text_in_chunks(message: Message, text: str) -> None:
    """Telegram message body limit is 4096 chars; split instead of truncating."""
    chunk = (text or "").strip()
    while chunk:
        piece = chunk[:_TELEGRAM_MESSAGE_MAX]
        chunk = chunk[_TELEGRAM_MESSAGE_MAX:]
        await message.answer(piece)


async def _register_active_run(user_id: int) -> int:
    """Register the current handler task as the latest active run for a user.

    If there is an older unfinished run for the same user, cancel it so stale
    browser/status responses do not leak into a newer conversation.
    """
    current_task = asyncio.current_task()
    if current_task is None:
        return 0

    run_id = next(_active_user_run_ids)
    previous_task: asyncio.Task | None = None
    previous_run_id = 0
    async with _active_user_runs_lock:
        previous = _active_user_runs.get(user_id)
        if previous:
            previous_run_id, previous_task = previous
        _active_user_runs[user_id] = (run_id, current_task)

    if previous_task is not None and previous_task is not current_task and not previous_task.done():
        logger.info(
            "user_run_cancel_requested",
            user_id=user_id,
            previous_run_id=previous_run_id,
            new_run_id=run_id,
        )
        previous_task.cancel()

    return run_id


def _is_latest_run(user_id: int, run_id: int) -> bool:
    """Return whether a run is still the latest active run for the user."""
    current = _active_user_runs.get(user_id)
    return bool(current and current[0] == run_id)


async def _clear_active_run(user_id: int, run_id: int) -> None:
    """Clear the active run marker if this run is still current."""
    async with _active_user_runs_lock:
        current = _active_user_runs.get(user_id)
        if current and current[0] == run_id:
            _active_user_runs.pop(user_id, None)


async def _cancel_active_run_if_any(user_id: int) -> None:
    """Stop the current message/graph run for this user if one is in flight."""
    async with _active_user_runs_lock:
        previous = _active_user_runs.pop(user_id, None)
    if previous is None:
        return
    prev_run_id, prev_task = previous
    if prev_task is not None and not prev_task.done():
        prev_task.cancel()
        logger.info(
            "active_run_cancelled_for_reset_session",
            user_id=user_id,
            cancelled_run_id=prev_run_id,
        )


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Handle /start command with welcome and bio request."""
    user_id = message.from_user.id

    # Check if this is a first-time user
    profile = await UserProfileQueries.get_or_create(session, user_id)
    is_new = not profile.context_summary

    welcome = (
        "👋 Привет! Я thirdHand — твоя третья рука и персональный AI-ассистент.\n"
        "Я могу:\n"
        "• Напоминать о событиях\n"
        "• Искать информацию\n"
        "• Подстраиваться под твои интересы\n"
    )

    if is_new:
        welcome += (
            "\n\n🧠 Давай познакомимся!\n"
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
        "📖 Команды:\n"
        "/start — начать\n"
        "/help — помощь\n"
        "/reset_session — сбросить историю и незавершённые задачи в Redis для этого чата\n"
        "\n💬 Примеры:\n"
        "• 'напомни завтра в 10:00 о созвоне'\n"
        "• 'найди новые модели OpenAI'\n"
        "• 'я работаю с Python и LangChain'\n"
        "• 'зайди на hh.ru, найди 3 вакансии AI-инженера и подготовь отклики'"
    )


@router.message(Command("reset_session"))
async def cmd_reset_session(message: Message) -> None:
    """Clear this user's Redis session state (history + pending); cancel active run; drop parked browser."""
    user_id = message.from_user.id
    await _cancel_active_run_if_any(user_id)
    had_browser = await discard_parked_browser_session_for_user(user_id)
    had_history, had_pending = await redis_history.clear_session_redis(user_id)
    logger.info(
        "session_reset_by_command",
        user_id=user_id,
        had_history=had_history,
        had_pending=had_pending,
        had_parked_browser=had_browser,
    )
    lines = [
        "Сессия сброшена для твоего аккаунта в этом боте.",
        "",
        "Redis:",
        f"• История сообщений: {'удалена' if had_history else 'уже была пуста'}.",
        f"• Незавершённая задача (pending): {'удалена' if had_pending else 'не было'}.",
        f"• Ожидающий ответа браузер (Chromium): {'закрыт' if had_browser else 'не было'}.",
        "",
        "Профиль в базе данных не менялся.",
    ]
    await message.answer("\n".join(lines))


@router.message(F.text)
async def handle_message(
    message: Message, session: AsyncSession, history: list | None = None
) -> None:
    """Handle text messages through LangGraph agent.

    Loads user profile, builds context, invokes graph, saves history.
    """
    user_id = message.from_user.id
    text = message.text
    run_id = await _register_active_run(user_id)

    logger.info("message_received", user_id=user_id, run_id=run_id, text_preview=text[:100])

    try:
        if history is None:
            history = await redis_history.get_history(user_id, limit=20)
            logger.info(
                "history_loaded_in_handler",
                user_id=user_id,
                run_id=run_id,
                message_count=len(history),
            )

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
        pending_task = await redis_history.get_pending_task(user_id)
        status_message = None
        last_status_text = ""

        async def update_status(text: str) -> None:
            nonlocal status_message, last_status_text
            if not _is_latest_run(user_id, run_id):
                logger.info(
                    "stale_status_update_suppressed",
                    user_id=user_id,
                    run_id=run_id,
                    text_preview=text[:120] if text else "",
                )
                return
            if not text or text == last_status_text:
                return
            last_status_text = text
            if status_message is None:
                status_message = await message.answer(text)
                return
            try:
                await status_message.edit_text(text)
            except Exception as exc:
                logger.warning("status_edit_failed", user_id=user_id, run_id=run_id, error=str(exc))

        # Create agent state with context
        state = AgentState(
            user_id=user_id,
            message_text=text,
            user_profile={
                "context_summary": context_summary,
                "session_summaries": session_summaries,
            },
            conversation_history=history[-10:] if history else [],
            pending_task=pending_task or {},
            db_session=session,
            status_callback=update_status,
        )

        # Inject context into the state for response node
        state.user_profile["context_text"] = context_text

        # Persist the incoming user message here so history ordering stays deterministic:
        # user message first, then assistant reply after successful handling.
        await redis_history.push_message(user_id, "user", text)

        # Invoke the graph
        try:
            result = await graph.ainvoke(state)
        except asyncio.CancelledError:
            logger.info("message_run_cancelled", user_id=user_id, run_id=run_id)
            return
        except Exception as e:
            logger.exception(
                "graph_invocation_failed", user_id=user_id, run_id=run_id, error=str(e)
            )
            if _is_latest_run(user_id, run_id):
                # Check if this is a browser task with partial results
                # Use getattr since state is an AgentState object, not a dict
                browser_screenshot = (getattr(state, "browser_screenshot_png_base64", "") or "").strip()
                browser_response = (getattr(state, "response_text", "") or "").strip()
                browser_needs_input = getattr(state, "browser_needs_user_input", False)
                
                # If browser already produced output, show it instead of error
                if browser_response and browser_needs_input:
                    response_text = format_agent_reply_for_telegram(browser_response)
                    if browser_screenshot:
                        try:
                            raw_png = base64.b64decode(browser_screenshot, validate=True)
                            await message.answer_photo(photo=BufferedInputFile(raw_png, filename="browser.png"), caption=response_text)
                        except Exception:
                            await message.answer(response_text)
                    else:
                        await message.answer(response_text)
                else:
                    await message.answer("⚠️ Произошла ошибка. Попробуй ещё раз позже.")
            return

        if not _is_latest_run(user_id, run_id):
            logger.info("stale_run_result_suppressed", user_id=user_id, run_id=run_id)
            return

        # Extract response
        response_text = result.get("response_text", "")
        response_type = result.get("response_type", "text")

        if not response_text:
            response_text = "🤔 Хм, не знаю что ответить. Попробуй переформулировать."
        response_text = format_agent_reply_for_telegram(response_text)

        screenshot_b64 = (result.get("browser_screenshot_png_base64") or "").strip()
        sent_photo = False
        if screenshot_b64:
            try:
                raw_png = base64.b64decode(screenshot_b64, validate=True)
            except Exception as exc:
                logger.warning(
                    "browser_screenshot_decode_failed",
                    user_id=user_id,
                    run_id=run_id,
                    error=str(exc),
                )
                raw_png = b""
            if raw_png:
                short_caption = "Снимок страницы браузера (отладка)."
                photo_file = BufferedInputFile(raw_png, filename="browser.png")
                if status_message is not None:
                    try:
                        await status_message.delete()
                    except Exception as exc:
                        logger.warning(
                            "status_delete_before_photo_failed",
                            user_id=user_id,
                            run_id=run_id,
                            error=str(exc),
                        )
                await message.answer_photo(photo_file, caption=short_caption)
                sent_photo = True
                await _answer_text_in_chunks(message, response_text)
                logger.info(
                    "response_sent_photo",
                    user_id=user_id,
                    run_id=run_id,
                    caption_preview=short_caption[:120],
                    png_bytes=len(raw_png),
                    text_chunks=max(1, (len(response_text) + _TELEGRAM_MESSAGE_MAX - 1) // _TELEGRAM_MESSAGE_MAX),
                )

        if not sent_photo:
            # Send response or finalize the existing status message
            if status_message is not None:
                try:
                    await status_message.edit_text(response_text)
                except Exception as exc:
                    logger.warning(
                        "final_status_edit_failed",
                        user_id=user_id,
                        run_id=run_id,
                        error=str(exc),
                    )
                    await message.answer(response_text)
            else:
                await message.answer(response_text)

        # Persist assistant reply so future turns see a correctly ordered dialogue.
        await redis_history.push_message(user_id, "assistant", response_text)
        await _sync_pending_task(user_id, text, result)

        logger.info(
            "response_sent",
            user_id=user_id,
            run_id=run_id,
            response_type=response_type,
            response_preview=response_text[:100],
        )

        # Background: extract bio facts only for ordinary conversational turns.
        if not (result.get("browser_needs_user_input") or len(response_text) > 1200):
            asyncio.create_task(
                _background_bio_extract(
                    user_id,
                    text,
                    response_text,
                    context_summary,
                )
            )
    finally:
        await _clear_active_run(user_id, run_id)


async def _sync_pending_task(user_id: int, user_message: str, result: dict) -> None:
    """Persist or clear pending task state after a graph run."""
    missing_context = result.get("missing_context") or []
    clarification_question = (result.get("clarification_question") or "").strip()
    browser_needs_user_input = bool(result.get("browser_needs_user_input", False))

    if missing_context and clarification_question:
        is_browser_waiting = (
            str(result.get("intent", "") or "").strip() == "browser_task"
            and bool(result.get("requires_browser", False))
        )
        pending = PendingTask(
            task_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            intent=result.get("intent", "chat"),
            user_goal=result.get("user_goal", "") or user_message,
            original_user_request=user_message,
            search_query=result.get("search_query", ""),
            browser_goal=result.get("browser_goal", ""),
            requires_web_search=bool(result.get("requires_web_search", False)),
            requires_browser=bool(result.get("requires_browser", False)),
            routing_reason=result.get("routing_reason", ""),
            required_context=result.get("required_context", []) or [],
            missing_context=missing_context,
            clarification_question=clarification_question,
            ambiguous_request=bool(result.get("ambiguous_request", False)),
            awaiting_user_step=is_browser_waiting,
            blocker_type="missing_info" if is_browser_waiting else "",
            browser_stop_reason=str(result.get("browser_stop_reason", "") or ""),
        )
        await redis_history.set_pending_task(user_id, pending.model_dump())
        return

    if bool(result.get("ambiguous_request", False)) and clarification_question:
        pending = PendingTask(
            task_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            intent=result.get("intent", "chat"),
            user_goal=result.get("user_goal", "") or user_message,
            original_user_request=user_message,
            search_query=result.get("search_query", ""),
            browser_goal=result.get("browser_goal", ""),
            requires_web_search=bool(result.get("requires_web_search", False)),
            requires_browser=bool(result.get("requires_browser", False)),
            routing_reason=result.get("routing_reason", ""),
            required_context=result.get("required_context", []) or [],
            missing_context=result.get("missing_context", []) or [],
            clarification_question=clarification_question,
            ambiguous_request=True,
        )
        await redis_history.set_pending_task(user_id, pending.model_dump())
        return

    if browser_needs_user_input:
        canon = (
            (result.get("canonical_user_objective") or "").strip()
            or (result.get("user_goal") or "").strip()
        )
        pending = PendingTask(
            task_id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(),
            intent="browser_task",
            user_goal=canon or user_message,
            original_user_request=user_message,
            browser_goal=result.get("browser_goal", "") or user_message,
            canonical_user_objective=canon,
            requires_browser=True,
            clarification_question=result.get("response_text", ""),
            blocker_type=result.get("browser_blocker_type", "") or "",
            browser_final_url=result.get("browser_final_url", "") or "",
            browser_next_user_action=str(result.get("browser_next_user_action", "") or ""),
            browser_resume_strategy=str(result.get("browser_resume_strategy", "") or ""),
            browser_stop_reason=str(result.get("browser_stop_reason", "") or ""),
            awaiting_user_step=True,
        )
        await redis_history.set_pending_task(user_id, pending.model_dump())
        return

    if bool(result.get("preserve_pending_task", False)):
        active_task_context = result.get("active_task_context") or {}
        if isinstance(active_task_context, dict) and active_task_context:
            await redis_history.set_pending_task(user_id, active_task_context)
            return

    await redis_history.clear_pending_task(user_id)


async def _background_bio_extract(
    user_id: int,
    user_message: str,
    assistant_message: str,
    existing_profile: dict,
) -> None:
    """Extract and save bio facts in background."""
    from src.thirdhand.models import get_session

    try:
        facts = await extract_bio_facts(user_message, assistant_message, existing_profile)
        if facts:
            async with get_session() as session:
                profile = await UserProfileQueries.get_or_create(session, user_id)
                profile.context_summary = merge_facts(profile.context_summary, facts)
                await session.commit()
                logger.info("bio_saved", user_id=user_id)
    except Exception as e:
        logger.exception("background_bio_extract_failed", user_id=user_id, error=str(e))
