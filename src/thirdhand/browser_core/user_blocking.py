"""User-blocking and parked-session continuation for the new browser core."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog

from src.thirdhand.browser_core.agent_loop import BrowserCoreRunResult, run_browser_core_loop
from src.thirdhand.browser_core.session import BrowserSession

logger = structlog.get_logger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

_BROWSER_CORE_RUN_LOCK = asyncio.Lock()
_BROWSER_CORE_PARK_MAX_AGE_SECONDS = 30 * 60
_BROWSER_CORE_PARKED: dict[int, "_ParkedBrowserCore"] = {}


@dataclass
class _ParkedBrowserCore:
    session: BrowserSession
    parked_at: float


async def _close_parked(user_id: int, entry: _ParkedBrowserCore) -> None:
    try:
        await entry.session.close()
    except Exception as exc:
        logger.warning(
            "browser_core_parked_session_close_failed",
            user_id=user_id,
            error=str(exc),
        )


async def discard_parked_browser_core_session_for_user(user_id: int) -> bool:
    """Close and drop a parked browser-core session awaiting user input."""
    async with _BROWSER_CORE_RUN_LOCK:
        entry = _BROWSER_CORE_PARKED.pop(user_id, None)
    if entry is None:
        return False
    await _close_parked(user_id, entry)
    logger.info("browser_core_parked_session_discarded_by_user_request", user_id=user_id)
    return True


async def run_browser_core_task(
    *,
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    latest_user_message: str = "",
) -> BrowserCoreRunResult:
    """Run the new browser core with parked-session continuation."""
    async with _BROWSER_CORE_RUN_LOCK:
        parked_slot = _BROWSER_CORE_PARKED.pop(user_id, None)
        logger.info(
            "browser_core_parked_slot_check",
            user_id=user_id,
            had_parked_slot=parked_slot is not None,
            parked_count=len(_BROWSER_CORE_PARKED),
        )
        if parked_slot is not None:
            age = time.monotonic() - parked_slot.parked_at
            if age > _BROWSER_CORE_PARK_MAX_AGE_SECONDS:
                logger.info(
                    "browser_core_parked_session_expired",
                    user_id=user_id,
                    age_seconds=round(age, 1),
                )
                await _close_parked(user_id, parked_slot)
                parked_slot = None

        reuse_parked = bool(parked_slot is not None)
        if reuse_parked:
            session = parked_slot.session
            logger.info(
                "browser_core_reusing_parked_session",
                user_id=user_id,
                resume_url=resume_url,
                parked_session_url=parked_slot.session.page.url if parked_slot.session.page else "",
            )
        else:
            session = BrowserSession()

        close_when_done = True
        try:
            result = await run_browser_core_loop(
                session=session,
                goal=goal,
                user_id=user_id,
                context_text=context_text,
                progress_callback=progress_callback,
                resume_url=resume_url,
                reuse_parked_live_tab=reuse_parked,
                latest_user_message=latest_user_message,
            )
            # Park session when browser needs user input, regardless of stop reason.
            # This covers ask_user, no_tool_calls, captcha, and other blocking scenarios.
            _stop_reasons_for_parking = {"ask_user", "no_tool_calls", "step_limit"}
            if result.needs_user_input and result.stop_reason in _stop_reasons_for_parking:
                close_when_done = False
                _BROWSER_CORE_PARKED[user_id] = _ParkedBrowserCore(
                    session=session,
                    parked_at=time.monotonic(),
                )
                logger.info(
                    "browser_core_session_parked_for_user_reply",
                    user_id=user_id,
                    request_type=result.request_type,
                    url=result.final_url,
                    stop_reason=result.stop_reason,
                )
            return result
        finally:
            if close_when_done:
                try:
                    await session.close()
                except Exception as exc:
                    logger.warning(
                        "browser_core_session_close_failed",
                        user_id=user_id,
                        error=str(exc),
                    )
