"""Thin public façade: browser session lock, `run_browser_task`, and re-exports.

Orchestration and state machine live in `browser_flow.py`. Tool schemas and `build_browser_tools`
live in `browser_tools.py`.

When the model calls ``ask_user`` and awaits a reply (``resume_strategy=await_user_message``),
Playwright/Chromium stays open for that user until the next resumed run consumes the parked
session. This avoids ``goto(resume_url)`` reloading captcha / OTP flows.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog

from src.thirdhand.services.browser_flow import (
    BrowserFlowPhase,
    BrowserFlowStateMachine,
    BrowserRunResult,
    run_browser_task_orchestration,
)
from src.thirdhand.services.browser_runtime import BrowserSession as _BrowserRuntimeSession
from src.thirdhand.services.browser_tools import (
    AskUserArgs,
    FinishTaskArgs,
    build_browser_tools,
)

logger = structlog.get_logger(__name__)

_BROWSER_RUN_LOCK = asyncio.Lock()

# Drop parked sessions older than this to avoid orphaned Chromium stacks (seconds).
_BROWSER_PARK_MAX_AGE_SECONDS = 30 * 60

_BROWSER_PARKED: dict[int, "_ParkedBrowser"] = {}

ProgressCallback = Callable[[str], Awaitable[None]]

__all__ = [
    "AskUserArgs",
    "BrowserFlowPhase",
    "BrowserFlowStateMachine",
    "BrowserRunResult",
    "BrowserSession",
    "FinishTaskArgs",
    "build_browser_tools",
    "discard_parked_browser_session_for_user",
    "run_browser_task",
]


class BrowserSession(_BrowserRuntimeSession):
    """Compatibility alias: all Playwright primitives live in `browser_runtime.BrowserSession`."""


@dataclass
class _ParkedBrowser:
    session: BrowserSession
    parked_at: float


async def _close_parked(user_id: int, entry: _ParkedBrowser) -> None:
    try:
        await entry.session.close()
    except Exception as exc:
        logger.warning(
            "browser_parked_session_close_failed",
            user_id=user_id,
            error=str(exc),
        )


async def discard_parked_browser_session_for_user(user_id: int) -> bool:
    """Close and drop a parked Playwright session awaiting a user reply, if any."""
    async with _BROWSER_RUN_LOCK:
        entry = _BROWSER_PARKED.pop(user_id, None)
    if entry is None:
        return False
    await _close_parked(user_id, entry)
    logger.info("browser_parked_session_discarded_by_user_request", user_id=user_id)
    return True


async def run_browser_task(
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    sub_intent: str | None = None,
    goal_display: str = "",
    page_context_hint: str = "",
) -> BrowserRunResult:
    """Run a browser task through a generic tool-calling loop."""
    return await _run_browser_task(
        goal,
        user_id,
        context_text=context_text,
        progress_callback=progress_callback,
        resume_url=resume_url,
        sub_intent=sub_intent,
        goal_display=goal_display,
        page_context_hint=page_context_hint,
    )


async def _run_browser_task(
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    sub_intent: str | None = None,
    goal_display: str = "",
    page_context_hint: str = "",
) -> BrowserRunResult:
    async with _BROWSER_RUN_LOCK:
        parked_slot = _BROWSER_PARKED.pop(user_id, None)
        if parked_slot is not None:
            age = time.monotonic() - parked_slot.parked_at
            if age > _BROWSER_PARK_MAX_AGE_SECONDS:
                logger.info(
                    "browser_parked_session_expired",
                    user_id=user_id,
                    age_seconds=round(age, 1),
                )
                await _close_parked(user_id, parked_slot)
                parked_slot = None

        reuse_parked = bool(parked_slot and resume_url.strip())
        if reuse_parked:
            session = parked_slot.session
            logger.info(
                "browser_reusing_parked_session",
                user_id=user_id,
                resume_url=resume_url,
            )
        else:
            if parked_slot is not None:
                await _close_parked(user_id, parked_slot)
            session = BrowserSession()

        close_when_done = True
        try:
            result = await run_browser_task_orchestration(
                session=session,
                goal=goal,
                user_id=user_id,
                context_text=context_text,
                progress_callback=progress_callback,
                resume_url=resume_url,
                reuse_parked_live_tab=reuse_parked,
                build_tools=build_browser_tools,
                initial_sub_intent=sub_intent,
                goal_display=goal_display,
                page_context_hint=page_context_hint,
            )
            if result.needs_user_input and result.resume_strategy == "await_user_message":
                close_when_done = False
                _BROWSER_PARKED[user_id] = _ParkedBrowser(
                    session=session,
                    parked_at=time.monotonic(),
                )
                logger.info(
                    "browser_session_parked_for_user_reply",
                    user_id=user_id,
                    blocker_type=result.blocker_type,
                    url=result.final_url,
                )
            return result
        finally:
            if close_when_done:
                try:
                    await session.close()
                except Exception as exc:
                    logger.warning(
                        "playwright_session_close_failed",
                        user_id=user_id,
                        error=str(exc),
                    )
