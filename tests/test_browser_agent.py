"""Tests for browser_agent public contract and browser node wiring."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.thirdhand.agent.nodes.browser import run_browser_task_node
from src.thirdhand.agent.state import AgentState
from src.thirdhand.browser_core import api as browser_api


class TestBrowserTaskResultMapping:
    def test_public_result_maps_user_pause_fields(self) -> None:
        result = browser_api._browser_core_result_to_public(
            goal="g",
            goal_display="goal",
            result=SimpleNamespace(
                trace=["inspect_page: {}"],
                final_url="https://a.test",
                final_message="Код из SMS?",
                needs_user_input=True,
                request_type="otp",
                screenshot_png_base64="QUJD",
                stop_reason="ask_user",
                metadata={"step_count": 2},
            ),
        )
        assert result.blocker_type == "2fa"
        assert result.resume_strategy == "await_user_message"
        assert result.next_user_action == "Код из SMS?"
        assert result.screenshot_png_base64 == "QUJD"
        assert result.stop_reason == "ask_user"

    def test_public_result_maps_completed_run(self) -> None:
        result = browser_api._browser_core_result_to_public(
            goal="g",
            goal_display="goal",
            result=SimpleNamespace(
                trace=["click: {}"],
                final_url="https://done.test",
                final_message="Готово",
                needs_user_input=False,
                request_type="other",
                screenshot_png_base64="",
                stop_reason="",
                metadata={"step_count": 1},
            ),
        )
        assert result.needs_user_input is False
        assert result.resume_strategy == "none"
        assert result.next_user_action == ""
        assert result.final_url == "https://done.test"


@pytest.mark.asyncio
async def test_run_browser_task_node_maps_public_browser_fields() -> None:
    fake = browser_api.BrowserTaskResult(
        telegram_report="report",
        trace=["x"],
        final_url="https://example.com/p",
        needs_user_input=True,
        blocker_type="login",
        next_user_action="sign in",
        resume_strategy="await_user_message",
        screenshot_png_base64="dGVzdA==",
        stop_reason="ask_user",
        metadata={"source": "browser_core"},
    )
    with patch(
        "src.thirdhand.agent.nodes.browser.run_browser_task", new=AsyncMock(return_value=fake)
    ):
        out = await run_browser_task_node(
            AgentState(user_id=1, browser_goal="do thing", user_profile={}),
        )
    assert out["browser_next_user_action"] == "sign in"
    assert out["browser_resume_strategy"] == "await_user_message"
    assert out["browser_screenshot_png_base64"] == "dGVzdA=="
    assert out["browser_stop_reason"] == "ask_user"
