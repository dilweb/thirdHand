"""Tests for the stuck-tool interceptor (_is_stuck_tool)."""

from src.thirdhand.browser_core.agent_loop import _is_stuck_tool
from src.thirdhand.browser_core.tracking import BrowserTrackingState


class TestIsStuckTool:
    def test_not_stuck_when_streak_below_2(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 1
        tracking.last_stuck_tool_name = "type_text"
        assert not _is_stuck_tool("type_text", tracking)

    def test_blocks_same_tool_when_stuck(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        assert _is_stuck_tool("type_text", tracking)

    def test_allows_different_tool_when_stuck(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        assert not _is_stuck_tool("click", tracking)

    def test_always_allows_visual_assist(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        assert not _is_stuck_tool("use_visual_assist", tracking)

    def test_blocks_ask_user_before_visual_assist(self) -> None:
        """ask_user blocked when stuck and visual assist not called yet."""
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        tracking.visual_assist_called_during_stuck = False
        assert _is_stuck_tool("ask_user", tracking)

    def test_allows_ask_user_after_visual_assist(self) -> None:
        """ask_user allowed after visual assist was called during stuck."""
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        tracking.visual_assist_called_during_stuck = True
        assert not _is_stuck_tool("ask_user", tracking)

    def test_always_allows_finish_task(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        assert not _is_stuck_tool("finish_task", tracking)

    def test_always_allows_inspect_page(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "type_text"
        assert not _is_stuck_tool("inspect_page", tracking)

    def test_not_stuck_with_empty_last_tool(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = ""
        assert not _is_stuck_tool("type_text", tracking)

    def test_blocks_scroll_when_stuck_on_scroll(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "scroll"
        assert _is_stuck_tool("scroll", tracking)

    def test_blocks_click_when_stuck_on_click(self) -> None:
        tracking = BrowserTrackingState()
        tracking.no_progress_streak = 2
        tracking.last_stuck_tool_name = "click"
        assert _is_stuck_tool("click", tracking)