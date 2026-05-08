"""Tests for browser_recovery helpers."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.thirdhand.services.browser_page_state import BrowserPageState
from src.thirdhand.services.browser_recovery import (
    MAX_EMPTY_TOOL_RECOVERY_ATTEMPTS,
    NoToolsOutcome,
    NoToolsPolicyResult,
    auth_facts_model_stalled_no_tools,
    dom_evidence_suggests_captcha,
    empty_step_recovery_limit_reached,
    explain_visual_assist_decision,
    human_message_after_no_tools_recovery,
    resolve_no_tools_after_llm_step,
    should_request_visual_assist,
    snapshot_dom_evidence_looks_weak,
    user_message_for_stalled_no_tools,
)


def test_empty_step_recovery_limit_respects_max_attempts() -> None:
    assert (
        empty_step_recovery_limit_reached(page_missing=False, attempt_count_before_retry=0) is False
    )
    assert (
        empty_step_recovery_limit_reached(page_missing=False, attempt_count_before_retry=1) is False
    )
    assert (
        empty_step_recovery_limit_reached(page_missing=False, attempt_count_before_retry=2) is True
    )
    assert (
        empty_step_recovery_limit_reached(page_missing=True, attempt_count_before_retry=0) is True
    )


def test_max_attempts_constant_matches_behavior() -> None:
    assert MAX_EMPTY_TOOL_RECOVERY_ATTEMPTS == 2


def test_auth_facts_model_stalled_shape() -> None:
    facts = auth_facts_model_stalled_no_tools("login")
    assert facts["outcome"] == "model_stalled_no_tools"
    assert facts["blocker_type"] == "login"
    assert "stall_reason_code" not in facts


def test_auth_facts_model_stalled_includes_reason() -> None:
    facts = auth_facts_model_stalled_no_tools("other", stall_reason_code="recovery_exhausted")
    assert facts["stall_reason_code"] == "recovery_exhausted"


def test_human_message_after_recovery_prefix_order() -> None:
    msg = human_message_after_no_tools_recovery('{"x":1}', runtime_guidance_prefix="Guidance line.")
    assert msg.startswith("Guidance line.")
    assert "recovered the live page state" in msg
    assert '{"x":1}' in msg


def test_human_message_without_prefix() -> None:
    msg = human_message_after_no_tools_recovery("snap", runtime_guidance_prefix="")
    assert msg.startswith("The model returned no tool call")


def test_weak_snapshot_detects_empty_and_short_text() -> None:
    assert snapshot_dom_evidence_looks_weak("") is True
    assert snapshot_dom_evidence_looks_weak("{}") is True
    assert snapshot_dom_evidence_looks_weak("not json") is True
    assert snapshot_dom_evidence_looks_weak('{"interactive":[],"text":"hi"}') is True
    long_text = "x" * 100
    assert (
        snapshot_dom_evidence_looks_weak('{"interactive":[{"id":"a"}],"text":"' + long_text + '"}')
        is False
    )


def test_policy_result_trace_line_for_stall() -> None:
    r = NoToolsPolicyResult(
        outcome=NoToolsOutcome.EXIT_STALLED,
        stall_reason_code="recovery_exhausted",
    )
    assert "recovery_exhausted" in r.recommended_trace_line(step=3)


def test_weak_dom_triggers_vision_when_recovery_attempt_positive() -> None:
    with patch("src.thirdhand.services.browser_recovery.settings") as s:
        s.PICTURE_RECOGNITION_MODEL = ""
        s.BROWSER_MODEL = "dummy-vision-model"
        s.DEFAULT_MODEL = ""
        assert (
            should_request_visual_assist(
                site_key="",
                snapshot_json='{"text":"","interactive":[]}',
                auth_guidance="",
                recovery_attempt=1,
                dom_evidence_weak=True,
            )
            is True
        )


def test_low_confidence_page_state_triggers_vision_without_recovery() -> None:
    with patch("src.thirdhand.services.browser_recovery.settings") as s:
        s.PICTURE_RECOGNITION_MODEL = ""
        s.BROWSER_MODEL = "dummy-vision-model"
        s.DEFAULT_MODEL = ""
        use, code = explain_visual_assist_decision(
            site_key="",
            snapshot_json='{"text":"", "interactive":[{"id":"a","text":"Open"}]}',
            auth_guidance="",
            recovery_attempt=0,
            page_state=BrowserPageState(
                screen_kind="actionable_page",
                candidate_actions=("Open",),
                required_inputs=(),
                missing_inputs=(),
                can_proceed_without_user=True,
                confidence=0.42,
            ),
        )
    assert use is True
    assert code == "low_page_state_confidence"


def test_unknown_page_state_triggers_vision_first() -> None:
    with patch("src.thirdhand.services.browser_recovery.settings") as s:
        s.PICTURE_RECOGNITION_MODEL = ""
        s.BROWSER_MODEL = "dummy-vision-model"
        s.DEFAULT_MODEL = ""
        use, code = explain_visual_assist_decision(
            site_key="",
            snapshot_json='{"text":"mystery", "interactive":[]}',
            auth_guidance="",
            recovery_attempt=0,
            page_state=BrowserPageState(
                screen_kind="unknown",
                candidate_actions=(),
                required_inputs=(),
                missing_inputs=(),
                can_proceed_without_user=False,
                confidence=0.35,
            ),
        )
    assert use is True
    assert code == "unknown_screen_kind"


@pytest.mark.asyncio
async def test_resolve_no_tools_continues_when_snapshot_returned() -> None:
    flow = MagicMock()
    flow.user_id = 1
    flow.blocking_message = ""

    with patch(
        "src.thirdhand.services.browser_recovery.execute_empty_tool_step_recovery",
        new_callable=AsyncMock,
        return_value='{"url":"x"}',
    ):
        result = await resolve_no_tools_after_llm_step(
            flow,
            step=2,
            compose_runtime_guidance=lambda: "Runtime hint.",
        )
    assert result.outcome is NoToolsOutcome.CONTINUE_LOOP
    assert result.human_followup_message
    assert "Runtime hint." in result.human_followup_message


@pytest.mark.asyncio
async def test_resolve_no_tools_user_blocking_after_recovery() -> None:
    flow = MagicMock()
    flow.user_id = 1

    async def _fake_execute(f: MagicMock, *, step: int) -> None:
        f.blocking_message = "Enter code"
        f.blocking_type = "2fa"
        return None

    with patch(
        "src.thirdhand.services.browser_recovery.execute_empty_tool_step_recovery",
        side_effect=_fake_execute,
    ):
        result = await resolve_no_tools_after_llm_step(
            flow,
            step=1,
            compose_runtime_guidance=lambda: "",
        )
    assert result.outcome is NoToolsOutcome.EXIT_USER_BLOCKING
    assert "user input" in result.policy_debug_note.lower()


@pytest.mark.asyncio
async def test_resolve_no_tools_stalled_on_recovery_exception() -> None:
    flow = MagicMock()
    flow.user_id = 1

    with patch(
        "src.thirdhand.services.browser_recovery.execute_empty_tool_step_recovery",
        new_callable=AsyncMock,
        side_effect=RuntimeError("probe failed"),
    ):
        result = await resolve_no_tools_after_llm_step(
            flow,
            step=1,
            compose_runtime_guidance=lambda: "",
        )
    assert result.outcome is NoToolsOutcome.EXIT_STALLED
    assert result.stall_reason_code == "recovery_exception"


@pytest.mark.asyncio
async def test_resolve_no_tools_stalled_when_page_missing() -> None:
    flow = MagicMock()
    flow.user_id = 1
    flow.blocking_message = ""
    flow.session.page = None

    with patch(
        "src.thirdhand.services.browser_recovery.execute_empty_tool_step_recovery",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await resolve_no_tools_after_llm_step(
            flow,
            step=1,
            compose_runtime_guidance=lambda: "",
        )
    assert result.outcome is NoToolsOutcome.EXIT_STALLED
    assert result.stall_reason_code == "page_missing"


def test_user_message_for_stalled_is_non_empty() -> None:
    assert len(user_message_for_stalled_no_tools(stall_reason_code="recovery_exhausted")) > 10


def test_dom_evidence_suggests_captcha_from_json_text() -> None:
    snap = json.dumps(
        {"text": "Пройдите капчу введите текст с картинки", "headings": []},
        ensure_ascii=False,
    )
    assert dom_evidence_suggests_captcha(snap) is True


def test_dom_evidence_suggests_captcha_from_heading() -> None:
    snap = json.dumps(
        {"text": "Вход", "headings": ["Пройдите капчу"]},
        ensure_ascii=False,
    )
    assert dom_evidence_suggests_captcha(snap) is True


def test_explain_visual_assist_decision_prefers_captcha_barrier() -> None:
    use, code = explain_visual_assist_decision(
        site_key="hh",
        snapshot_json=json.dumps(
            {"text": "подтвердите что вы не робот", "interactive": []},
            ensure_ascii=False,
        ),
        auth_guidance="",
        recovery_attempt=0,
    )
    assert use is True
    assert code == "captcha_barrier"


def test_should_request_visual_assist_on_captcha_without_auth_guidance() -> None:
    assert should_request_visual_assist(
        site_key="hh",
        snapshot_json=json.dumps({"text": "пройдите капчу"}, ensure_ascii=False),
        auth_guidance="",
        recovery_attempt=0,
    )
