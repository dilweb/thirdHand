"""Tests for structured browser page-state derivation."""

from src.thirdhand.services.browser_page_state import (
    BrowserPageState,
    derive_browser_page_state,
    infer_terminal_outcome,
    summarize_browser_page_state,
)


def test_derive_browser_page_state_login_form() -> None:
    state = derive_browser_page_state(
        snapshot_json="""
        {
          "url": "https://example.com/login",
          "text": "Sign in to continue",
          "interactive": [
            {"id": "u1", "tag": "input", "type": "email", "name": "email", "fillable": true, "value_preview": ""},
            {"id": "p1", "tag": "input", "type": "password", "name": "password", "fillable": true, "value_preview": ""},
            {"id": "b1", "tag": "button", "text": "Continue", "fillable": false}
          ]
        }
        """,
        probe={"url": "https://example.com/login", "auth_signals": {"login_form_present": True}},
    )
    assert isinstance(state, BrowserPageState)
    assert state.screen_kind == "login"
    assert "login_identity" in state.required_inputs
    assert "password" in state.required_inputs
    assert "login_identity" in state.missing_inputs
    assert "Continue" in state.candidate_actions
    assert state.primary_action_label == "Continue"
    assert state.action_surface_kind == "login_form"
    assert state.action_surface_present is True


def test_derive_browser_page_state_code_verification() -> None:
    state = derive_browser_page_state(
        snapshot_json="""
        {
          "text": "Введите код из SMS",
          "interactive": [
            {"id": "c1", "tag": "input", "type": "text", "placeholder": "Код из смс", "fillable": true, "value_preview": ""},
            {"id": "b1", "tag": "button", "text": "Подтвердить", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    assert state.screen_kind == "code_verification"
    assert "verification_code" in state.required_inputs
    assert state.can_proceed_without_user is False


def test_derive_browser_page_state_selection_list() -> None:
    state = derive_browser_page_state(
        snapshot_json="""
        {
          "text": "Results page",
          "interactive": [
            {"id": "a1", "tag": "a", "text": "Open item 1", "fillable": false},
            {"id": "a2", "tag": "a", "text": "Open item 2", "fillable": false},
            {"id": "a3", "tag": "button", "text": "Next page", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    assert state.screen_kind in {"selection_list", "actionable_page"}
    assert len(state.candidate_actions) >= 2
    assert state.can_proceed_without_user is True
    assert state.action_surface_present is True


def test_derive_browser_page_state_keeps_action_surface_even_if_text_sounds_successful() -> None:
    state = derive_browser_page_state(
        snapshot_json="""
        {
          "text": "Application sent. You can still edit and submit again.",
          "interactive": [
            {"id": "f1", "tag": "textarea", "name": "cover_letter", "fillable": true, "value_preview": "draft"},
            {"id": "b1", "tag": "button", "text": "Submit application", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    assert state.screen_kind in {"form", "actionable_page"}
    assert state.action_surface_present is True
    assert state.primary_action_label == "Submit application"


def test_derive_browser_page_state_passive_page_does_not_need_success_keywords() -> None:
    state = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Order #1048"],
          "text": "Delivery details and receipt",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    assert state.screen_kind == "unknown"
    assert state.dominant_heading == "Order #1048"
    assert state.action_surface_kind == "passive_content"
    assert state.action_surface_present is False


def test_summarize_browser_page_state_contains_core_fields() -> None:
    summary = summarize_browser_page_state(
        BrowserPageState(
            screen_kind="login",
            candidate_actions=("Continue",),
            required_inputs=("login_identity", "password"),
            missing_inputs=("login_identity",),
            can_proceed_without_user=True,
            confidence=0.88,
            dominant_heading="Sign in",
            primary_action_label="Continue",
            action_surface_kind="login_form",
            action_surface_present=True,
            fillable_count=2,
            interactive_count=3,
        )
    )
    assert "screen_kind: login" in summary
    assert "dominant_heading: Sign in" in summary
    assert "candidate_actions: Continue" in summary
    assert "primary_action_label: Continue" in summary
    assert "required_inputs: login_identity, password" in summary
    assert "action_surface_kind: login_form" in summary


def test_infer_terminal_outcome_returns_structured_completion_for_state_transition() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Apply now"],
          "interactive": [
            {"id": "c1", "tag": "textarea", "name": "cover_letter", "fillable": true, "value_preview": "hello"},
            {"id": "b1", "tag": "button", "text": "Submit application", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Conversation started"],
          "text": "Employer thread opened",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="click",
        before_snapshot='{"headings":["Apply now"]}',
        after_snapshot='{"headings":["Conversation started"]}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://jobs.example/apply",
        after_url="https://jobs.example/messages/42",
    )
    assert outcome.completed is True
    assert outcome.confidence >= 0.78
    assert outcome.reason_code in {
        "action_surface_disappeared",
        "action_surface_replaced_after_navigation",
        "required_form_surface_disappeared",
        "moved_to_passive_result_surface",
    }
    assert outcome.explanation


def test_infer_terminal_outcome_rejects_success_keywords_without_state_change() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "text": "Apply now",
          "interactive": [
            {"id": "f1", "tag": "textarea", "name": "cover_letter", "fillable": true, "value_preview": "draft"},
            {"id": "b1", "tag": "button", "text": "Submit application", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "text": "Application sent successfully. You may still update and submit application.",
          "interactive": [
            {"id": "f1", "tag": "textarea", "name": "cover_letter", "fillable": true, "value_preview": "draft"},
            {"id": "b1", "tag": "button", "text": "Submit application", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="click",
        before_snapshot='{"text":"Apply now"}',
        after_snapshot='{"text":"Application sent successfully"}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://jobs.example/apply",
        after_url="https://jobs.example/apply",
    )
    assert outcome.completed is False
    assert outcome.reason_code == "same_primary_action_still_present"


def test_infer_terminal_outcome_keeps_discovery_non_terminal() -> None:
    before = derive_browser_page_state(
        snapshot_json='{"interactive":[{"id":"a1","tag":"a","text":"Open item","fillable":false}]}',
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json='{"headings":["Results"],"interactive":[]}',
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_discover_candidates",
        tool_name="click",
        before_snapshot='{"text":"list"}',
        after_snapshot='{"text":"results"}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://jobs.example/search",
        after_url="https://jobs.example/search?page=2",
    )
    assert outcome.completed is False
    assert outcome.reason_code == "discover_requires_explicit_finish"


def test_infer_terminal_outcome_scores_missing_inputs_and_form_disappearance() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Checkout"],
          "interactive": [
            {"id": "a1", "tag": "input", "type": "text", "name": "street", "fillable": true, "value_preview": ""},
            {"id": "a2", "tag": "input", "type": "text", "name": "city", "fillable": true, "value_preview": ""},
            {"id": "b1", "tag": "button", "text": "Place order", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Order #1048"],
          "text": "Thank you for your order",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="press_key",
        before_snapshot='{"text":"Checkout"}',
        after_snapshot='{"text":"Thank you for your order"}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://pizza.example/checkout",
        after_url="https://pizza.example/order/1048",
    )
    assert outcome.completed is True
    assert outcome.confidence >= 0.72
    assert "missing inputs decreased" in outcome.explanation


def test_infer_terminal_outcome_scores_authenticated_capabilities_after_login() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Sign in"],
          "interactive": [
            {"id": "u1", "tag": "input", "type": "email", "name": "email", "fillable": true, "value_preview": "user@example.com"},
            {"id": "p1", "tag": "input", "type": "password", "name": "password", "fillable": true, "value_preview": ""},
            {"id": "b1", "tag": "button", "text": "Log in", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": True}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Account"],
          "interactive": [
            {"id": "a1", "tag": "a", "text": "Open applications", "fillable": false},
            {"id": "a2", "tag": "button", "text": "Apply now", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_select_targets",
        tool_name="click",
        before_snapshot='{"text":"Sign in"}',
        after_snapshot='{"text":"Account"}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://jobs.example/login",
        after_url="https://jobs.example/account",
    )
    assert outcome.completed is False
    assert outcome.confidence >= 0.4
    assert "authenticated capabilities increased after login" in outcome.explanation


def test_infer_terminal_outcome_uses_text_only_as_secondary_support() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Checkout"],
          "interactive": [
            {"id": "a1", "tag": "input", "type": "text", "name": "street", "fillable": true, "value_preview": "Main st"},
            {"id": "b1", "tag": "button", "text": "Place order", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Order #1048"],
          "text": "Thank you. Order confirmed.",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="click",
        before_snapshot='{"text":"Checkout"}',
        after_snapshot='{"text":"Thank you. Order confirmed."}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://pizza.example/checkout",
        after_url="https://pizza.example/order/1048",
    )
    assert outcome.completed is True
    assert "supporting success marker text is present" in outcome.explanation


def test_infer_terminal_outcome_allows_select_mode_when_target_is_clearly_opened() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Results"],
          "interactive": [
            {"id": "a1", "tag": "a", "text": "Open item 1", "fillable": false},
            {"id": "a2", "tag": "a", "text": "Open item 2", "fillable": false},
            {"id": "a3", "tag": "button", "text": "Next page", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Item 1 details"],
          "interactive": [
            {"id": "b1", "tag": "button", "text": "Save selection", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_select_targets",
        tool_name="click",
        before_snapshot='{"headings":["Results"]}',
        after_snapshot='{"headings":["Item 1 details"]}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://shop.example/results",
        after_url="https://shop.example/items/1",
    )
    assert outcome.completed is True
    assert outcome.reason_code == "selection_became_opened_target"
    assert outcome.confidence >= 0.58


def test_infer_terminal_outcome_keeps_same_transition_non_terminal_for_apply_mode() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Results"],
          "interactive": [
            {"id": "a1", "tag": "a", "text": "Open item 1", "fillable": false},
            {"id": "a2", "tag": "a", "text": "Open item 2", "fillable": false},
            {"id": "a3", "tag": "button", "text": "Next page", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Item 1 details"],
          "interactive": [
            {"id": "b1", "tag": "button", "text": "Save selection", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="click",
        before_snapshot='{"headings":["Results"]}',
        after_snapshot='{"headings":["Item 1 details"]}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://shop.example/results",
        after_url="https://shop.example/items/1",
    )
    assert outcome.completed is False
    assert outcome.reason_code == "insufficient_transition_evidence"


def test_infer_terminal_outcome_blocks_ambiguous_draft_state() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Apply now"],
          "interactive": [
            {"id": "c1", "tag": "textarea", "name": "cover_letter", "fillable": true, "value_preview": "hello"},
            {"id": "b1", "tag": "button", "text": "Submit application", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Draft saved"],
          "text": "Your application draft was saved. Continue editing when ready.",
          "interactive": [
            {"id": "b1", "tag": "button", "text": "Continue editing", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="click",
        before_snapshot='{"headings":["Apply now"]}',
        after_snapshot='{"headings":["Draft saved"]}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://jobs.example/apply",
        after_url="https://jobs.example/drafts/42",
    )
    assert outcome.completed is False
    assert outcome.reason_code == "ambiguous_partial_or_draft_state"


def test_infer_terminal_outcome_blocks_review_or_processing_state() -> None:
    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Checkout"],
          "interactive": [
            {"id": "a1", "tag": "input", "type": "text", "name": "street", "fillable": true, "value_preview": "Main st"},
            {"id": "b1", "tag": "button", "text": "Place order", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Review your order"],
          "text": "Processing payment, review order details before final confirmation.",
          "interactive": [
            {"id": "b1", "tag": "button", "text": "Confirm order", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    outcome = infer_terminal_outcome(
        sub_intent="browser_apply_to_targets",
        tool_name="press_key",
        before_snapshot='{"headings":["Checkout"]}',
        after_snapshot='{"headings":["Review your order"]}',
        before_page_state=before,
        after_page_state=after,
        before_url="https://pizza.example/checkout",
        after_url="https://pizza.example/review",
    )
    assert outcome.completed is False
    assert outcome.reason_code == "ambiguous_partial_or_draft_state"
