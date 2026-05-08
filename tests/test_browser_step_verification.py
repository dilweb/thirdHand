"""Tests for generic post-action step verification."""

from src.thirdhand.services.browser_page_state import BrowserPageState
from src.thirdhand.services.browser_step_verification import (
    build_step_expectation,
    evaluate_step_outcome,
)


def _snapshot(interactive: list[dict], *, headings: list[str] | None = None) -> str:
    import json

    return json.dumps(
        {
            "title": "Page",
            "url": "https://example.com",
            "headings": headings or [],
            "interactive": interactive,
            "text": "Example page",
        },
        ensure_ascii=False,
    )


def test_build_step_expectation_for_click_on_selection_list() -> None:
    before_snapshot = _snapshot(
        [
            {
                "id": "el-1",
                "text": "Open item",
                "role": "button",
                "tag": "button",
                "fillable": False,
            }
        ]
    )
    before_state = BrowserPageState(
        screen_kind="selection_list",
        candidate_actions=("Open item",),
        required_inputs=(),
        missing_inputs=(),
        can_proceed_without_user=True,
        confidence=0.6,
        primary_action_label="Open item",
        action_surface_kind="selection_list",
        action_surface_present=True,
    )

    expectation = build_step_expectation(
        step_number=3,
        user_objective="Open the matching result",
        tool_name="click",
        tool_args={"element_id": "el-1"},
        before_snapshot=before_snapshot,
        before_page_state=before_state,
    )

    assert expectation is not None
    assert expectation.action_intent == "open_details"
    assert expectation.target.element_id == "el-1"


def test_evaluate_step_outcome_detects_probable_success_after_tool_error() -> None:
    before_snapshot = _snapshot(
        [
            {
                "id": "apply-1",
                "text": "Apply",
                "role": "button",
                "tag": "button",
                "fillable": False,
            }
        ]
    )
    after_snapshot = _snapshot(
        [
            {
                "id": "chat-1",
                "text": "Chat",
                "role": "button",
                "tag": "button",
                "fillable": False,
            }
        ]
    )
    before_state = BrowserPageState(
        screen_kind="selection_list",
        candidate_actions=("Apply",),
        required_inputs=(),
        missing_inputs=(),
        can_proceed_without_user=True,
        confidence=0.6,
        primary_action_label="Apply",
        action_surface_kind="selection_list",
        action_surface_present=True,
    )
    after_state = BrowserPageState(
        screen_kind="selection_list",
        candidate_actions=("Chat",),
        required_inputs=(),
        missing_inputs=(),
        can_proceed_without_user=True,
        confidence=0.7,
        primary_action_label="Chat",
        action_surface_kind="selection_list",
        action_surface_present=True,
    )
    expectation = build_step_expectation(
        step_number=4,
        user_objective="Apply to the vacancy",
        tool_name="click",
        tool_args={"element_id": "apply-1"},
        before_snapshot=before_snapshot,
        before_page_state=before_state,
    )
    assert expectation is not None

    outcome = evaluate_step_outcome(
        expectation=expectation,
        tool_result='ERROR: TimeoutError: Locator.click: Timeout 10000ms exceeded.',
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        before_page_state=before_state,
        after_page_state=after_state,
        before_url="https://example.com/jobs",
        after_url="https://example.com/jobs",
    )

    assert outcome.status in {"probable_success", "success"}
    assert outcome.evidence.primary_action_changed is True


def test_evaluate_step_outcome_detects_blocked_state() -> None:
    before_snapshot = _snapshot(
        [
            {
                "id": "login-1",
                "text": "Continue",
                "role": "button",
                "tag": "button",
                "fillable": False,
            }
        ]
    )
    after_snapshot = _snapshot([])
    before_state = BrowserPageState(
        screen_kind="form",
        candidate_actions=("Continue",),
        required_inputs=("login_identity",),
        missing_inputs=(),
        can_proceed_without_user=True,
        confidence=0.65,
        primary_action_label="Continue",
        action_surface_kind="editable_form",
        action_surface_present=True,
    )
    after_state = BrowserPageState(
        screen_kind="code_verification",
        candidate_actions=(),
        required_inputs=("verification_code",),
        missing_inputs=("verification_code",),
        can_proceed_without_user=False,
        confidence=0.92,
        primary_action_label="",
        action_surface_kind="verification_gate",
        action_surface_present=False,
    )
    expectation = build_step_expectation(
        step_number=5,
        user_objective="Login",
        tool_name="click",
        tool_args={"element_id": "login-1"},
        before_snapshot=before_snapshot,
        before_page_state=before_state,
    )
    assert expectation is not None

    outcome = evaluate_step_outcome(
        expectation=expectation,
        tool_result='{"title":"Verification","url":"https://example.com/verify"}',
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        before_page_state=before_state,
        after_page_state=after_state,
        before_url="https://example.com/login",
        after_url="https://example.com/verify",
    )

    assert outcome.status == "blocked"
    assert "code_verification" in outcome.evidence.blocker_markers
