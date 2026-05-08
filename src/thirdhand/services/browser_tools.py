"""LangChain tool definitions for the browser automation loop."""

from __future__ import annotations

import json
from typing import Literal

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.thirdhand.services.browser_flow import BrowserSubIntent
from src.thirdhand.services.browser_runtime import BrowserSession as BrowserRuntimeSession

logger = structlog.get_logger(__name__)


class AskUserArgs(BaseModel):
    """Structured arguments for the ask_user tool."""

    question: str = Field(
        description="Clear short question: what value is missing or what should happen next."
    )
    blocker_type: Literal["login", "captcha", "2fa", "confirmation", "missing_info", "other"] = (
        Field(
            default="other",
            description=(
                "Loose category for routing/logging — not enforced by runtime. Examples: login (sign-in wall), "
                "captcha (anti-bot puzzle), missing_info (need text from user), confirmation (risky commit), "
                "2fa (any second-step verification the page asks for — use only after reading the DOM), other."
            ),
        )
    )


class FinishTaskArgs(BaseModel):
    """Structured arguments for the finish_task tool."""

    summary: str = Field(description="Short final report about the browser task outcome.")
    status: Literal["completed", "stopped"] = Field(
        default="completed",
        description=(
            "Use 'completed' only when the requested task is actually done. "
            "Use 'stopped' when you intentionally stop at a safe checkpoint or when technical issues prevent completion."
        ),
    )


def _finish_task_tool_description(sub_intent: BrowserSubIntent) -> str:
    base = (
        "Call this only when the task is fully completed or you intentionally stopped. "
        "Use status='completed' only for a truly finished task. Use status='stopped' for technical blockers "
        "or safe checkpoints that still need the user's next action. Do not use this as a shortcut for uncertainty; "
        "keep exploring the live page until you either complete the task or identify a concrete blocking reason."
    )
    if sub_intent is BrowserSubIntent.DISCOVER_CANDIDATES:
        return (
            base
            + " In discovery mode, 'completed' means you summarized what was found (candidates/listings), "
            "not that you submitted an application or checkout."
        )
    if sub_intent is BrowserSubIntent.SELECT_TARGETS:
        return (
            base
            + " In selection mode, 'completed' means you identified the chosen option(s) from the current context, "
            "not necessarily that you applied unless the user asked to apply."
        )
    return base


def _click_tool_description(sub_intent: BrowserSubIntent) -> str:
    base = "Click a visible element using its dynamic id from inspect_page."
    if sub_intent is BrowserSubIntent.DISCOVER_CANDIDATES:
        return (
            base
            + " In discovery mode, avoid clicks that submit applications, send résumés, or finalize purchases; "
            "use click for navigation, filters, pagination, and opening readable detail views."
        )
    return base


def build_browser_tools(
    session: BrowserRuntimeSession,
    *,
    sub_intent: BrowserSubIntent = BrowserSubIntent.APPLY_TO_TARGETS,
) -> dict[str, StructuredTool]:
    """Create the browser toolset bound to a live Playwright session.

    Tool descriptions depend on ``sub_intent`` so discovery, selection, and apply runs are not one undifferentiated mode.
    """

    async def finish_task(
        summary: str, status: Literal["completed", "stopped"] = "completed"
    ) -> str:
        return json.dumps(
            {
                "summary": summary,
                "status": status,
            },
            ensure_ascii=False,
        )

    async def ask_user(
        question: str,
        blocker_type: Literal[
            "login", "captcha", "2fa", "confirmation", "missing_info", "other"
        ] = "other",
    ) -> str:
        return json.dumps(
            {
                "question": question,
                "blocker_type": blocker_type,
            },
            ensure_ascii=False,
        )

    tools = [
        StructuredTool.from_function(
            coroutine=session.open_browser,
            name="open_browser",
            description="Open or focus the persistent browser. Optionally pass a site URL.",
        ),
        StructuredTool.from_function(
            coroutine=session.goto_url,
            name="goto_url",
            description="Navigate to a URL or domain when you know where to go next.",
        ),
        StructuredTool.from_function(
            coroutine=session.inspect_page,
            name="inspect_page",
            description="Read the current page. Returns title, URL, visible text, headings, and interactive elements; "
            "each row includes fillable=true only for real text fields (input/textarea/select/contenteditable), not buttons.",
        ),
        StructuredTool.from_function(
            coroutine=session.read_page,
            name="read_page",
            description="Read only the visible page text when you need more semantic context.",
        ),
        StructuredTool.from_function(
            coroutine=session.click,
            name="click",
            description=_click_tool_description(sub_intent),
        ),
        StructuredTool.from_function(
            coroutine=session.type_text,
            name="type_text",
            description="Type text into an element id from inspect_page. Only use rows with fillable=true (or tag input/textarea); "
            "never pass a button/link id. Use submit=true to press Enter after typing.",
        ),
        StructuredTool.from_function(
            coroutine=session.press_key,
            name="press_key",
            description="Press a keyboard key such as Enter, Tab, Escape, ArrowDown, or Control+L.",
        ),
        StructuredTool.from_function(
            coroutine=session.scroll,
            name="scroll",
            description="Scroll the page up or down to reveal more content.",
        ),
        StructuredTool.from_function(
            coroutine=session.wait_for_page,
            name="wait_for_page",
            description="Wait a short time for navigation, animations, network requests, or lazy rendering.",
        ),
        StructuredTool.from_function(
            coroutine=finish_task,
            name="finish_task",
            args_schema=FinishTaskArgs,
            description=_finish_task_tool_description(sub_intent),
        ),
        StructuredTool.from_function(
            coroutine=ask_user,
            name="ask_user",
            args_schema=AskUserArgs,
            description=(
                "Last resort. Ask the user only after you already used inspect_page/read_page and, when useful, "
                "scroll, wait_for_page, or another inspect to understand the live screen. Do not ask what is visible "
                "on the page or which button probably matches the task. Ask only for a concrete missing value "
                "(password, OTP, address, confirmation) or when the next safe action is still ambiguous after "
                "exhausting the available page evidence."
            ),
        ),
    ]
    return {tool.name: tool for tool in tools}
