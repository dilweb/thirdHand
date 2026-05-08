"""Tests for canonical browser flow phases (Stage 17 shell)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.thirdhand.services.browser_flow import (
    BrowserFlowPhase,
    BrowserFlowPhaseTracker,
    BrowserSubIntent,
    CanonicalBrowserPhase,
    canonical_browser_phase,
    infer_browser_sub_intent,
    sub_intent_execution_brief,
)
from src.thirdhand.services.browser_step_verification import (
    StepOutcome,
    VerificationEvidence,
)


def test_canonical_maps_legacy_runtime_labels() -> None:
    assert canonical_browser_phase("init") is CanonicalBrowserPhase.INIT
    assert canonical_browser_phase("waiting_for_model") is CanonicalBrowserPhase.PAGE_ACTION_FLOW
    assert canonical_browser_phase("recovering_empty_step") is CanonicalBrowserPhase.RECOVERY_FLOW
    assert canonical_browser_phase("blocked") is CanonicalBrowserPhase.BLOCKED_WAITING_USER
    assert canonical_browser_phase("finished") is CanonicalBrowserPhase.FINISHED


def test_canonical_maps_every_browser_flow_phase_enum_member() -> None:
    for member in BrowserFlowPhase:
        c = canonical_browser_phase(member.value)
        assert isinstance(c, CanonicalBrowserPhase)
        assert c is not CanonicalBrowserPhase.INIT or member.value == "init"


def test_phase_tracker_sync_updates_canonical() -> None:
    t = BrowserFlowPhaseTracker()
    assert t.canonical is CanonicalBrowserPhase.INIT
    assert t.sync_from_runtime_label("recovering_empty_step") is CanonicalBrowserPhase.RECOVERY_FLOW
    assert t.canonical is CanonicalBrowserPhase.RECOVERY_FLOW


def test_infer_browser_sub_intent_always_defaults_to_apply_until_graph_sets_one() -> None:
    phrases = (
        "найди на hh вакансии python и откликнись",
        "найди вакансии python разработчик на hh",
        "выбери лучшую вакансию из списка",
        "browse the catalog without buying",
    )
    for text in phrases:
        assert infer_browser_sub_intent(text) is BrowserSubIntent.APPLY_TO_TARGETS


def test_resolve_browser_sub_intent_prefers_explicit_persisted_value() -> None:
    from src.thirdhand.services.browser_flow import _resolve_browser_sub_intent

    assert (
        _resolve_browser_sub_intent("игнорируется", "browser_discover_candidates")
        is BrowserSubIntent.DISCOVER_CANDIDATES
    )
    assert infer_browser_sub_intent("anything") is BrowserSubIntent.APPLY_TO_TARGETS


def test_infer_browser_sub_intent_explicit_default_fixture() -> None:
    """Keep a single anchored example for regressions."""
    assert infer_browser_sub_intent("открой страницу настроек") is BrowserSubIntent.APPLY_TO_TARGETS


def test_sub_intent_execution_briefs_are_distinct_stage21() -> None:
    """Discovery vs apply must not read as the same mode (Stage 21)."""
    discover = sub_intent_execution_brief(BrowserSubIntent.DISCOVER_CANDIDATES)
    apply_ = sub_intent_execution_brief(BrowserSubIntent.APPLY_TO_TARGETS)
    select = sub_intent_execution_brief(BrowserSubIntent.SELECT_TARGETS)
    assert discover != apply_
    assert select != apply_
    assert "Do NOT start application" in discover
    assert "APPLY / ACT" in apply_
    assert "SELECTION" in select


def test_system_prompt_includes_sub_intent_policy() -> None:
    from src.thirdhand.services.browser_flow import _system_prompt

    discover_prompt = _system_prompt("", BrowserSubIntent.DISCOVER_CANDIDATES)
    apply_prompt = _system_prompt("", BrowserSubIntent.APPLY_TO_TARGETS)
    assert "Sub-intent policy" in discover_prompt
    assert "Mode: DISCOVERY" in discover_prompt
    assert "Sub-intent policy" in apply_prompt
    assert "Mode: APPLY" in apply_prompt
    assert "Primary operating loop" in apply_prompt
    assert "Ask the user only as a last resort" in apply_prompt
    assert "Do not ask the user to identify buttons" in apply_prompt


def test_guard_ask_user_rejects_vague_question() -> None:
    from src.thirdhand.services.browser_flow import _guard_ask_user_request

    decision = _guard_ask_user_request(
        question="Что нажать?",
        blocker_type="other",
        step_number=1,
        tool_actions_taken=0,
        page_reads_taken=1,
        snapshot_json='{"text":"Каталог","interactive":[{"id":"a","text":"Купить"}]}',
    )
    assert decision.allowed is False
    assert decision.reason_code == "vague_question"


def test_guard_ask_user_rejects_premature_specific_request() -> None:
    from src.thirdhand.services.browser_flow import _guard_ask_user_request

    decision = _guard_ask_user_request(
        question="Какой пароль использовать для входа?",
        blocker_type="login",
        step_number=1,
        tool_actions_taken=0,
        page_reads_taken=1,
        snapshot_json='{"text":"Вход","interactive":[{"id":"p1","tag":"input","type":"password"}]}',
    )
    assert decision.allowed is False
    assert decision.reason_code == "premature_escalation"


def test_guard_ask_user_allows_specific_request_after_runtime_attempts() -> None:
    from src.thirdhand.services.browser_flow import _guard_ask_user_request

    decision = _guard_ask_user_request(
        question="Нужен пароль для входа. Пришли пароль или одноразовый код, если вход по SMS.",
        blocker_type="login",
        step_number=3,
        tool_actions_taken=2,
        page_reads_taken=2,
        snapshot_json='{"text":"Вход","interactive":[{"id":"p1","tag":"input","type":"password"}]}',
    )
    assert decision.allowed is True


def test_guard_ask_user_rejects_2fa_when_code_input_not_visible() -> None:
    from src.thirdhand.services.browser_flow import _guard_ask_user_request

    decision = _guard_ask_user_request(
        question="Пришли код из SMS",
        blocker_type="2fa",
        step_number=3,
        tool_actions_taken=1,
        page_reads_taken=2,
        snapshot_json='{"text":"Вход Я ищу работу","interactive":[{"id":"b1","tag":"button","text":"Войти"}]}',
    )
    assert decision.allowed is False
    assert decision.reason_code == "2fa_not_visible"


def test_guard_ask_user_rejects_when_page_state_has_actions() -> None:
    from src.thirdhand.services.browser_flow import _guard_ask_user_request
    from src.thirdhand.services.browser_page_state import BrowserPageState

    decision = _guard_ask_user_request(
        question="Нужна помощь",
        blocker_type="other",
        step_number=3,
        tool_actions_taken=1,
        page_reads_taken=2,
        snapshot_json='{"text":"Каталог"}',
        page_state=BrowserPageState(
            screen_kind="selection_list",
            candidate_actions=("Купить", "Открыть"),
            required_inputs=(),
            missing_inputs=(),
            can_proceed_without_user=True,
            confidence=0.7,
        ),
    )
    assert decision.allowed is False
    assert decision.reason_code == "vague_question"


def test_infer_blocker_class_distinguishes_core_categories() -> None:
    from src.thirdhand.services.browser_flow import _infer_blocker_class

    assert _infer_blocker_class(blocker_type="captcha", stop_reason="") == "policy_forbidden_or_impossible"
    assert _infer_blocker_class(blocker_type="confirmation", stop_reason="") == "manual_confirmation_needed"
    assert _infer_blocker_class(blocker_type="login", stop_reason="") == "user_data_needed"
    assert (
        _infer_blocker_class(
            blocker_type="other",
            stop_reason="user_must_assist_after_model_stall",
            outcome="model_stalled_no_tools",
        )
        == "machine_resolvable"
    )


def test_tool_progress_message_is_human_friendly() -> None:
    from src.thirdhand.services.browser_flow import _tool_progress_message

    assert _tool_progress_message("inspect_page") == "Изучаю текущую страницу и доступные элементы."
    assert _tool_progress_message("scroll", {"direction": "down"}) == "Просматриваю страницу ниже, ищу нужные элементы."


def test_model_progress_message_avoids_raw_reasoning_dump() -> None:
    from src.thirdhand.services.browser_flow import _model_progress_message
    from src.thirdhand.services.browser_page_state import BrowserPageState

    msg = _model_progress_message(
        tool_calls=[],
        page_state=BrowserPageState(
            screen_kind="selection_list",
            candidate_actions=("Откликнуться", "Открыть"),
            required_inputs=(),
            missing_inputs=(),
            can_proceed_without_user=True,
            confidence=0.48,
        ),
    )
    assert "Ищу другой способ открыть нужный вариант" in msg


def test_infer_start_url_hh_keeps_explicit_domain_not_keywords() -> None:
    from src.thirdhand.services.browser_flow import _infer_start_url_from_goal

    assert _infer_start_url_from_goal("Go to hh.ru, log in with SMS code") == "https://hh.ru"


def test_infer_start_url_hh_starts_at_home_when_goal_is_browse_only() -> None:
    from src.thirdhand.services.browser_flow import _infer_start_url_from_goal

    u = _infer_start_url_from_goal("найди на hh вакансии python разработчик")
    assert u == "https://hh.ru"


def test_infer_start_url_russian_vkhod_keeps_site_home_until_user_gives_deep_link() -> None:
    from src.thirdhand.services.browser_flow import _infer_start_url_from_goal

    u = _infer_start_url_from_goal("зайди на hh вот номер для входа")
    assert u == "https://hh.ru"


def test_infer_start_url_respects_explicit_account_path_in_goal() -> None:
    from src.thirdhand.services.browser_flow import _infer_start_url_from_goal

    u = _infer_start_url_from_goal(
        "https://hh.ru/account/login?role=applicant вход по смс номер дам дальше"
    )
    assert "account/login" in u


def test_should_run_runtime_detector_only_for_successful_page_changing_tools() -> None:
    from src.thirdhand.services.browser_flow import _should_run_runtime_detector

    assert _should_run_runtime_detector("click", "ok") is True
    assert _should_run_runtime_detector("press_key", {"ok": True}) is True
    assert _should_run_runtime_detector("inspect_page", "ok") is False
    assert _should_run_runtime_detector("click", "ERROR: boom") is False


def test_runtime_detector_followup_message_requests_one_more_inspection() -> None:
    from src.thirdhand.services.browser_flow import _runtime_detector_followup_message
    from src.thirdhand.services.browser_page_state import derive_browser_page_state

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
    flow = SimpleNamespace(
        sub_intent=BrowserSubIntent.SELECT_TARGETS,
        last_snapshot='{"headings":["Account"]}',
        page_state=after,
        current_url="https://jobs.example/account",
    )
    msg = _runtime_detector_followup_message(
        flow=flow,
        tool_name="click",
        before_snapshot='{"headings":["Sign in"]}',
        before_page_state=before,
        before_url="https://jobs.example/login",
        verification_already_requested=False,
    )
    assert "Inspect the live page once more" in msg
    assert _runtime_detector_followup_message(
        flow=flow,
        tool_name="click",
        before_snapshot='{"headings":["Sign in"]}',
        before_page_state=before,
        before_url="https://jobs.example/login",
        verification_already_requested=True,
    ) == ""


def test_runtime_detector_followup_message_uses_step_outcome_when_available() -> None:
    from src.thirdhand.services.browser_flow import _runtime_detector_followup_message

    flow = SimpleNamespace(
        sub_intent=BrowserSubIntent.APPLY_TO_TARGETS,
        last_snapshot='{"headings":["Jobs"]}',
        page_state=None,
        current_url="https://jobs.example/list",
    )
    msg = _runtime_detector_followup_message(
        flow=flow,
        tool_name="click",
        before_snapshot='{"headings":["Jobs"]}',
        before_page_state=None,
        before_url="https://jobs.example/list",
        verification_already_requested=False,
        step_outcome=StepOutcome(
            status="probable_success",
            confidence=0.63,
            summary="Local surface changed after the click.",
            evidence=VerificationEvidence(
                tool_succeeded=False,
                tool_error="ERROR: TimeoutError",
                target_changed=True,
                primary_action_changed=True,
                confidence=0.63,
            ),
        ),
    )
    assert "Inspect the live page once more" in msg


@pytest.mark.asyncio
async def test_runtime_detector_can_auto_finish_run() -> None:
    from src.thirdhand.services.browser_flow import _maybe_complete_via_runtime_detector
    from src.thirdhand.services.browser_page_state import derive_browser_page_state

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
    flow = SimpleNamespace(
        user_id=1,
        sub_intent=BrowserSubIntent.APPLY_TO_TARGETS,
        last_snapshot='{"headings":["Conversation started"]}',
        page_state=after,
        current_url="https://jobs.example/messages/42",
        transition=AsyncMock(),
    )
    session = SimpleNamespace(current_url=AsyncMock(return_value="https://jobs.example/messages/42"))
    trace: list[str] = []
    result = await _maybe_complete_via_runtime_detector(
        flow=flow,
        session=session,
        goal="apply to vacancy",
        goal_display="apply",
        trace=trace,
        step_number=3,
        tool_name="click",
        before_snapshot='{"headings":["Apply now"]}',
        before_page_state=before,
        before_url="https://jobs.example/apply",
    )
    assert result is not None
    assert result.needs_user_input is False
    assert result.auth_facts["success_detected_by_runtime"] is True
    assert result.auth_facts["outcome"] == "runtime_success_detected"
    flow.transition.assert_awaited()


@pytest.mark.asyncio
async def test_runtime_detector_marks_login_success_after_auth_wall_disappears() -> None:
    from src.thirdhand.services.browser_flow import _maybe_complete_via_runtime_detector
    from src.thirdhand.services.browser_page_state import derive_browser_page_state

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
          "text": "Welcome back",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    flow = SimpleNamespace(
        user_id=1,
        sub_intent=BrowserSubIntent.APPLY_TO_TARGETS,
        last_snapshot='{"headings":["Account"],"text":"Welcome back"}',
        page_state=after,
        current_url="https://jobs.example/account",
        transition=AsyncMock(),
    )
    session = SimpleNamespace(current_url=AsyncMock(return_value="https://jobs.example/account"))
    result = await _maybe_complete_via_runtime_detector(
        flow=flow,
        session=session,
        goal="log in",
        goal_display="login",
        trace=[],
        step_number=2,
        tool_name="click",
        before_snapshot='{"headings":["Sign in"]}',
        before_page_state=before,
        before_url="https://jobs.example/login",
    )
    assert result is not None
    assert result.needs_user_input is False
    assert result.auth_facts["outcome"] == "runtime_success_detected"
    assert result.auth_facts["success_detected_by_runtime"] is True


@pytest.mark.asyncio
async def test_runtime_detector_can_convert_step_limit_exit_into_completed() -> None:
    from src.thirdhand.services.browser_flow import _maybe_complete_via_runtime_detector
    from src.thirdhand.services.browser_page_state import derive_browser_page_state

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
          "text": "Thank you for your order",
          "interactive": []
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    flow = SimpleNamespace(
        user_id=1,
        sub_intent=BrowserSubIntent.APPLY_TO_TARGETS,
        last_snapshot='{"headings":["Order #1048"],"text":"Thank you for your order"}',
        page_state=after,
        current_url="https://pizza.example/order/1048",
        transition=AsyncMock(),
    )
    session = SimpleNamespace(current_url=AsyncMock(return_value="https://pizza.example/order/1048"))
    trace: list[str] = ["Шаг 8: лимит шагов почти достигнут."]
    result = await _maybe_complete_via_runtime_detector(
        flow=flow,
        session=session,
        goal="complete checkout",
        goal_display="checkout",
        trace=trace,
        step_number=10,
        tool_name="press_key",
        before_snapshot='{"headings":["Checkout"]}',
        before_page_state=before,
        before_url="https://pizza.example/checkout",
    )
    assert result is not None
    assert result.needs_user_input is False
    assert result.auth_facts["outcome"] == "runtime_success_detected"
    assert result.auth_facts["success_detected_by_runtime"] is True
    assert any("runtime_success_detected" in line for line in result.trace)


@pytest.mark.asyncio
async def test_runtime_detector_can_complete_from_step_verifier_probable_success() -> None:
    from src.thirdhand.services.browser_flow import _maybe_complete_via_runtime_detector
    from src.thirdhand.services.browser_page_state import derive_browser_page_state

    before = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Vacancies"],
          "interactive": [
            {"id": "b1", "tag": "button", "text": "Apply", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    after = derive_browser_page_state(
        snapshot_json="""
        {
          "headings": ["Vacancies"],
          "interactive": [
            {"id": "b2", "tag": "button", "text": "Chat", "fillable": false}
          ]
        }
        """,
        probe={"auth_signals": {"login_form_present": False}},
    )
    flow = SimpleNamespace(
        user_id=1,
        sub_intent=BrowserSubIntent.APPLY_TO_TARGETS,
        last_snapshot='{"headings":["Vacancies"]}',
        page_state=after,
        current_url="https://jobs.example/list",
        transition=AsyncMock(),
    )
    session = SimpleNamespace(current_url=AsyncMock(return_value="https://jobs.example/list"))
    result = await _maybe_complete_via_runtime_detector(
        flow=flow,
        session=session,
        goal="apply to vacancy",
        goal_display="apply",
        trace=[],
        step_number=3,
        tool_name="click",
        before_snapshot='{"headings":["Vacancies"]}',
        before_page_state=before,
        before_url="https://jobs.example/list",
        step_outcome=StepOutcome(
            status="probable_success",
            confidence=0.81,
            summary="Target state changed from Apply to Chat after the click.",
            evidence=VerificationEvidence(
                tool_succeeded=False,
                tool_error="ERROR: TimeoutError",
                target_changed=True,
                primary_action_changed=True,
                confidence=0.81,
            ),
        ),
    )
    assert result is not None
    assert result.needs_user_input is False
    assert result.auth_facts["outcome"] == "runtime_success_detected"
    assert result.auth_facts["reason_code"] == "step_verifier_probable_success"
