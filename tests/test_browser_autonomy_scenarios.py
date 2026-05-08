"""High-level product scenarios for generic browser autonomy behavior."""

from src.thirdhand.services.browser_flow import _guard_ask_user_request, _infer_blocker_class
from src.thirdhand.services.browser_page_state import derive_browser_page_state
from src.thirdhand.services.browser_recovery import explain_visual_assist_decision


def test_scenario_login_password_screen_requests_user_data_not_ui_interpretation() -> None:
    snapshot = """
    {
      "url": "https://vendor.example/login",
      "text": "Sign in to continue",
      "interactive": [
        {"id": "u1", "tag": "input", "type": "email", "name": "email", "fillable": true, "value_preview": "user@example.com"},
        {"id": "p1", "tag": "input", "type": "password", "name": "password", "fillable": true, "value_preview": ""},
        {"id": "b1", "tag": "button", "text": "Log in", "fillable": false}
      ]
    }
    """
    state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe={"url": "https://vendor.example/login", "auth_signals": {"login_form_present": True}},
    )
    decision = _guard_ask_user_request(
        question="Нужен пароль для входа.",
        blocker_type="login",
        step_number=3,
        tool_actions_taken=2,
        page_reads_taken=2,
        snapshot_json=snapshot,
        page_state=state,
    )
    assert state.screen_kind == "login"
    assert "password" in state.required_inputs
    assert decision.allowed is True
    assert _infer_blocker_class(blocker_type="login") == "user_data_needed"


def test_scenario_otp_screen_allows_code_request_only_when_field_is_visible() -> None:
    snapshot = """
    {
      "text": "Введите код из SMS",
      "interactive": [
        {"id": "c1", "tag": "input", "type": "text", "placeholder": "Код из SMS", "fillable": true, "value_preview": ""},
        {"id": "b1", "tag": "button", "text": "Подтвердить", "fillable": false}
      ]
    }
    """
    state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe={"auth_signals": {"login_form_present": False}},
    )
    decision = _guard_ask_user_request(
        question="Пришли одноразовый код из SMS.",
        blocker_type="2fa",
        step_number=3,
        tool_actions_taken=1,
        page_reads_taken=2,
        snapshot_json=snapshot,
        page_state=state,
    )
    assert state.screen_kind == "code_verification"
    assert decision.allowed is True


def test_scenario_checkout_with_missing_address_is_generic_form_not_site_specific() -> None:
    snapshot = """
    {
      "url": "https://pizza.example/checkout",
      "text": "Checkout Delivery details",
      "interactive": [
        {"id": "a1", "tag": "input", "type": "text", "name": "street", "placeholder": "Street address", "fillable": true, "value_preview": ""},
        {"id": "a2", "tag": "input", "type": "text", "name": "apartment", "placeholder": "Apartment", "fillable": true, "value_preview": ""},
        {"id": "b1", "tag": "button", "text": "Place order", "fillable": false}
      ]
    }
    """
    state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe={"url": "https://pizza.example/checkout", "auth_signals": {"login_form_present": False}},
    )
    assert state.screen_kind == "form"
    assert "address" in state.required_inputs
    assert "address" in state.missing_inputs
    assert "Place order" in state.candidate_actions


def test_scenario_ambiguous_cta_prefers_vision_before_user_escalation() -> None:
    snapshot = """
    {
      "text": "Choose your plan",
      "interactive": [
        {"id": "a1", "tag": "button", "text": "Continue", "fillable": false},
        {"id": "a2", "tag": "button", "text": "Continue", "fillable": false}
      ]
    }
    """
    state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe={"auth_signals": {"login_form_present": False}},
    )
    use_visual, code = explain_visual_assist_decision(
        site_key="",
        snapshot_json=snapshot,
        auth_guidance="",
        recovery_attempt=0,
        page_state=state,
    )
    rejected = _guard_ask_user_request(
        question="Какую кнопку Continue нажать?",
        blocker_type="other",
        step_number=2,
        tool_actions_taken=0,
        page_reads_taken=2,
        snapshot_json=snapshot,
        page_state=state,
    )
    assert state.screen_kind in {"actionable_page", "selection_list"}
    assert use_visual is True
    assert code in {"low_page_state_confidence", "unknown_screen_kind"}
    assert rejected.allowed is False


def test_scenario_visual_challenge_is_classified_as_manual_policy_barrier() -> None:
    snapshot = """
    {
      "text": "Please verify you are human",
      "headings": ["Security check"],
      "interactive": [{"id": "b1", "tag": "button", "text": "I am human", "fillable": false}]
    }
    """
    state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe={"auth_signals": {"login_form_present": False}},
    )
    use_visual, code = explain_visual_assist_decision(
        site_key="",
        snapshot_json=snapshot,
        auth_guidance="",
        recovery_attempt=0,
        page_state=state,
    )
    assert state.screen_kind == "challenge"
    assert use_visual is True
    assert code == "captcha_barrier"
    assert (
        _infer_blocker_class(blocker_type="captcha", stop_reason="user_must_complete_captcha")
        == "policy_forbidden_or_impossible"
    )
