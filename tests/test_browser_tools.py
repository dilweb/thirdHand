"""Smoke tests for browser tool module."""

from src.thirdhand.services.browser_flow import BrowserSubIntent
from src.thirdhand.services.browser_tools import (
    AskUserArgs,
    _click_tool_description,
    _finish_task_tool_description,
    build_browser_tools,
)


class _DummySession:
    async def open_browser(self, start_url: str = "") -> str:
        return start_url

    async def goto_url(self, url: str) -> str:
        return url

    async def inspect_page(self) -> str:
        return "{}"

    async def read_page(self) -> str:
        return ""

    async def click(self, element_id: str) -> str:
        return element_id

    async def type_text(self, element_id: str, text: str, submit: bool = False) -> str:
        return text

    async def press_key(self, key: str) -> str:
        return key

    async def scroll(self, direction: str = "down", amount: int = 800) -> str:
        return direction

    async def wait_for_page(self, seconds: float = 2.0) -> str:
        return str(seconds)


def test_ask_user_args_has_blocker_type() -> None:
    assert "blocker_type" in AskUserArgs.model_fields


def test_build_browser_tools_callable() -> None:
    assert callable(build_browser_tools)


def test_build_browser_tools_descriptions_vary_by_sub_intent_stage21() -> None:
    """finish_task/click guidance must differ for discovery vs apply (Stage 21)."""
    apply_finish = _finish_task_tool_description(BrowserSubIntent.APPLY_TO_TARGETS)
    discover_finish = _finish_task_tool_description(BrowserSubIntent.DISCOVER_CANDIDATES)
    apply_click = _click_tool_description(BrowserSubIntent.APPLY_TO_TARGETS)
    discover_click = _click_tool_description(BrowserSubIntent.DISCOVER_CANDIDATES)
    assert apply_finish != discover_finish
    assert apply_click != discover_click
    assert "discovery mode" in discover_finish.lower()
    assert "discovery mode" in discover_click.lower()


def test_finish_task_description_discourages_premature_stop() -> None:
    desc = _finish_task_tool_description(BrowserSubIntent.APPLY_TO_TARGETS)
    assert "shortcut for uncertainty" in desc


def test_ask_user_tool_description_marks_last_resort() -> None:
    tools = build_browser_tools(session=_DummySession(), sub_intent=BrowserSubIntent.APPLY_TO_TARGETS)
    desc = tools["ask_user"].description or ""
    assert "Last resort" in desc
    assert "Do not ask what is visible on the page" in desc
