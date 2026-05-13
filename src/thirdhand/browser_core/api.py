"""Public browser-task entrypoint backed only by browser_core."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from src.thirdhand.browser_core.reporting import format_run_summary_telegram
from src.thirdhand.browser_core.session import BrowserSession
from src.thirdhand.browser_core.tools import AskUserArgs, FinishTaskArgs
from src.thirdhand.browser_core.user_blocking import (
    discard_parked_browser_core_session_for_user,
    run_browser_core_task,
)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class BrowserTaskResult:
    """Minimal public browser-task contract for the new browser core."""

    telegram_report: str
    trace: list[str]
    final_url: str
    needs_user_input: bool = False
    blocker_type: str = "other"
    next_user_action: str = ""
    resume_strategy: str = "none"
    screenshot_png_base64: str = ""
    stop_reason: str = ""
    metadata: dict = field(default_factory=dict)


__all__ = [
    "AskUserArgs",
    "BrowserSession",
    "BrowserTaskResult",
    "FinishTaskArgs",
    "discard_parked_browser_session_for_user",
    "run_browser_task",
]


async def discard_parked_browser_session_for_user(user_id: int) -> bool:
    """Close and drop a parked browser session awaiting a user reply."""
    return await discard_parked_browser_core_session_for_user(user_id)


async def run_browser_task(
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    goal_display: str = "",
    page_context_hint: str = "",
    latest_user_message: str = "",
) -> BrowserTaskResult:
    """Run a browser task using browser_core as the only execution path."""
    del page_context_hint
    core_result = await run_browser_core_task(
        goal=goal,
        user_id=user_id,
        context_text=context_text,
        progress_callback=progress_callback,
        resume_url=resume_url,
        latest_user_message=latest_user_message,
    )
    return _browser_core_result_to_public(
        goal=goal,
        goal_display=goal_display,
        result=core_result,
    )


def _browser_core_request_type_to_blocker_type(request_type: str, needs_user_input: bool) -> str:
    raw = (request_type or "").strip().lower()
    if raw == "credential":
        return "login"
    if raw == "otp":
        return "2fa"
    if raw == "captcha":
        return "captcha"
    if raw == "confirmation":
        return "confirmation"
    if raw in {"choice", "file"}:
        return "missing_info"
    return "other" if needs_user_input else "other"


def _browser_core_result_to_public(
    *,
    goal: str,
    goal_display: str,
    result,
) -> BrowserTaskResult:
    blocker_type = _browser_core_request_type_to_blocker_type(
        str(getattr(result, "request_type", "") or ""),
        bool(getattr(result, "needs_user_input", False)),
    )
    needs_user_input = bool(getattr(result, "needs_user_input", False))
    final_message = str(getattr(result, "final_message", "") or "")
    final_url = str(getattr(result, "final_url", "") or "")
    trace = list(getattr(result, "trace", []) or [])
    stop_reason = str(getattr(result, "stop_reason", "") or "")
    metadata = dict(getattr(result, "metadata", {}) or {})
    public_metadata = {
        "facts_version": 1,
        "source": "browser_core",
        "outcome": "agent_requested_user_input" if needs_user_input else "completed",
        "blocker_type": blocker_type,
    }
    if stop_reason:
        public_metadata["stop_reason"] = stop_reason
    if metadata:
        public_metadata["metadata"] = metadata
    resume_strategy = "await_user_message" if needs_user_input and stop_reason == "ask_user" else (
        "continue_after_checkpoint" if needs_user_input else "none"
    )
    next_user_action = final_message if needs_user_input else ""
    return BrowserTaskResult(
        telegram_report=format_run_summary_telegram(
            goal_display=goal_display,
            goal_internal=goal,
            trace=trace,
            final_message=final_message,
            final_url=final_url,
            needs_user_input=needs_user_input,
            blocker_type=blocker_type,
        ),
        trace=trace,
        final_url=final_url,
        needs_user_input=needs_user_input,
        blocker_type=blocker_type,
        next_user_action=next_user_action,
        resume_strategy=resume_strategy,
        screenshot_png_base64=str(getattr(result, "screenshot_png_base64", "") or ""),
        stop_reason=stop_reason,
        metadata=public_metadata,
    )
