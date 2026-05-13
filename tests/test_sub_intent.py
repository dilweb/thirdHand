"""Tests for WorkflowSelector (PageType-based only)."""

from src.thirdhand.browser_core.page_classifier import PageType
from src.thirdhand.browser_core.sub_intent import (
    WorkflowSelector,
    WorkflowSpec,
    WorkflowType,
)


class TestWorkflowType:
    def test_has_all_expected_values(self) -> None:
        values = {w.value for w in WorkflowType}
        expected = {"discover", "select", "apply", "monitor", "fill"}
        assert values == expected


class TestWorkflowSelectorFromPageType:
    def test_search_results_to_discover(self) -> None:
        assert WorkflowSelector.from_page_type(PageType.SEARCH_RESULTS) == WorkflowType.DISCOVER

    def test_detail_page_to_apply(self) -> None:
        assert WorkflowSelector.from_page_type(PageType.DETAIL_PAGE) == WorkflowType.APPLY

    def test_form_page_to_fill(self) -> None:
        assert WorkflowSelector.from_page_type(PageType.FORM_PAGE) == WorkflowType.FILL

    def test_login_page_to_apply(self) -> None:
        assert WorkflowSelector.from_page_type(PageType.LOGIN_PAGE) == WorkflowType.APPLY

    def test_generic_page_to_discover(self) -> None:
        assert WorkflowSelector.from_page_type(PageType.GENERIC_PAGE) == WorkflowType.DISCOVER


class TestWorkflowSpec:
    def test_discover_spec_has_prompt(self) -> None:
        spec = WorkflowSelector.build_spec(WorkflowType.DISCOVER)
        assert isinstance(spec, WorkflowSpec)
        assert "DISCOVERY" in spec.prompt_block
        assert spec.completion_criteria

    def test_apply_spec_has_no_prohibitions(self) -> None:
        spec = WorkflowSelector.build_spec(WorkflowType.APPLY)
        assert spec.prohibited_patterns == []

    def test_monitor_spec_restricts_tools(self) -> None:
        spec = WorkflowSelector.build_spec(WorkflowType.MONITOR)
        assert "click" not in spec.allowed_tools

    def test_fill_spec_allows_type_text(self) -> None:
        spec = WorkflowSelector.build_spec(WorkflowType.FILL)
        assert "type_text" in spec.allowed_tools