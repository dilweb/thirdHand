"""Tests for LocalWorkflowPolicy."""

from src.thirdhand.browser_core.page_classifier import PageType
from src.thirdhand.browser_core.policy import (
    LocalWorkflowPolicy,
    WorkflowState,
    WorkflowTransition,
)


class TestWorkflowState:
    def test_has_all_expected_values(self) -> None:
        values = {s.value for s in WorkflowState}
        expected = {"start", "discover", "alternate_search", "select", "apply",
                     "monitor", "await_user", "complete"}
        assert values == expected


class TestInitialState:
    def test_defaults_to_start(self) -> None:
        p = LocalWorkflowPolicy()
        assert p.state == WorkflowState.START

    def test_can_set_initial_state(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        assert p.state == WorkflowState.DISCOVER


class TestTransitions:
    def test_valid_transition(self) -> None:
        p = LocalWorkflowPolicy()
        assert p.transition_to(WorkflowState.DISCOVER)
        assert p.state == WorkflowState.DISCOVER

    def test_invalid_transition_returns_false(self) -> None:
        p = LocalWorkflowPolicy()
        # START → COMPLETE is not a valid transition
        assert not p.transition_to(WorkflowState.COMPLETE)
        assert p.state == WorkflowState.START

    def test_full_chain(self) -> None:
        p = LocalWorkflowPolicy()
        assert p.transition_to(WorkflowState.DISCOVER)
        assert p.transition_to(WorkflowState.SELECT)
        assert p.transition_to(WorkflowState.APPLY)
        assert p.transition_to(WorkflowState.COMPLETE)
        assert p.state == WorkflowState.COMPLETE

    def test_apply_to_await_user_and_back(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.APPLY)
        assert p.transition_to(WorkflowState.AWAIT_USER)
        assert p.transition_to(WorkflowState.APPLY)

    def test_start_to_monitor(self) -> None:
        p = LocalWorkflowPolicy()
        assert p.transition_to(WorkflowState.MONITOR)
        assert p.state == WorkflowState.MONITOR

    def test_monitor_to_complete(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.MONITOR)
        assert p.transition_to(WorkflowState.COMPLETE)
        assert p.state == WorkflowState.COMPLETE

    def test_monitor_to_await_user(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.MONITOR)
        assert p.transition_to(WorkflowState.AWAIT_USER)
        assert p.state == WorkflowState.AWAIT_USER

    def test_apply_to_discover(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.APPLY)
        assert p.transition_to(WorkflowState.DISCOVER)
        assert p.state == WorkflowState.DISCOVER

    def test_multi_item_chain(self) -> None:
        """Verify the full cycle for multi-item workflows (e.g. apply to 3 vacancies)."""
        p = LocalWorkflowPolicy()
        assert p.transition_to(WorkflowState.DISCOVER)
        assert p.transition_to(WorkflowState.SELECT)
        assert p.transition_to(WorkflowState.APPLY)
        assert p.transition_to(WorkflowState.DISCOVER)  # next item
        assert p.transition_to(WorkflowState.SELECT)
        assert p.transition_to(WorkflowState.APPLY)
        assert p.transition_to(WorkflowState.COMPLETE)
        assert p.state == WorkflowState.COMPLETE


class TestSuggestTransition:
    def test_cycle_on_discover_suggests_alternate(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        result = p.suggest_transition(PageType.GENERIC_PAGE, cycle_detected=True)
        assert result == WorkflowState.ALTERNATE_SEARCH

    def test_no_suggestion_when_stuck(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        result = p.suggest_transition(PageType.GENERIC_PAGE, no_progress_streak=2)
        assert result is None

    def test_search_results_suggests_discover(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.START)
        result = p.suggest_transition(PageType.SEARCH_RESULTS)
        assert result == WorkflowState.DISCOVER

    def test_detail_page_suggests_apply(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.START)
        result = p.suggest_transition(PageType.DETAIL_PAGE)
        assert result == WorkflowState.APPLY

    def test_natural_progression_from_start(self) -> None:
        p = LocalWorkflowPolicy()
        result = p.suggest_transition(PageType.GENERIC_PAGE)
        # START → DISCOVER is the first natural progression
        assert result == WorkflowState.DISCOVER


class TestBuildPromptBlock:
    def test_start_state_has_no_guidance(self) -> None:
        p = LocalWorkflowPolicy()
        block = p.build_prompt_block()
        # START state has no prompt block defined
        assert block == ""

    def test_discover_state_has_guidance(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        block = p.build_prompt_block()
        assert "DISCOVERY" in block

    def test_apply_state_has_guidance(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.APPLY)
        block = p.build_prompt_block()
        assert "APPLY" in block or "ACT" in block

    def test_cycle_warning_included(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        block = p.build_prompt_block(cycle_detected=True)
        assert "CYCLE DETECTED" in block

    def test_stuck_tip_included(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        block = p.build_prompt_block(no_progress_streak=1)
        assert "TIP" in block

    def test_login_page_guidance(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.START)
        block = p.build_prompt_block(page_type=PageType.LOGIN_PAGE)
        assert "LOGIN" in block

    def test_form_page_guidance(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.DISCOVER)
        block = p.build_prompt_block(page_type=PageType.FORM_PAGE)
        assert "FORM" in block

    def test_monitor_state_has_guidance(self) -> None:
        p = LocalWorkflowPolicy(initial_state=WorkflowState.MONITOR)
        block = p.build_prompt_block()
        assert "MONITOR" in block
        assert "observe" in block.lower()


class TestWorkflowTransition:
    def test_dataclass_fields(self) -> None:
        t = WorkflowTransition(
            from_state=WorkflowState.START,
            to_state=WorkflowState.DISCOVER,
            condition="initial",
        )
        assert t.from_state == WorkflowState.START
        assert t.to_state == WorkflowState.DISCOVER
        assert t.condition == "initial"