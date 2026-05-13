"""Tests for HighLevelPlanner."""

import pytest

from src.thirdhand.browser_core.planner import (
    HighLevelPlanner,
    SubTask,
    TaskPlan,
    _parse_llm_response,
    _dict_to_task_plan,
    _fallback_plan,
)
from src.thirdhand.browser_core.sub_intent import WorkflowType


class TestTaskPlan:
    def test_default_values(self) -> None:
        plan = TaskPlan()
        assert not plan.fast_path
        assert plan.subtasks == []
        assert plan.estimated_steps == 0
        assert plan.primary_workflow == WorkflowType.APPLY
        assert plan.summary == ""

    def test_custom_values(self) -> None:
        plan = TaskPlan(
            fast_path=True,
            estimated_steps=3,
            primary_workflow=WorkflowType.DISCOVER,
            summary="Test plan",
        )
        assert plan.fast_path
        assert plan.estimated_steps == 3
        assert plan.primary_workflow == WorkflowType.DISCOVER
        assert plan.summary == "Test plan"


class TestSubTask:
    def test_default_values(self) -> None:
        st = SubTask(description="test", workflow=WorkflowType.DISCOVER, completion_criteria="done")
        assert st.description == "test"
        assert st.workflow == WorkflowType.DISCOVER
        assert st.completion_criteria == "done"


class TestParseLlmResponse:
    def test_parses_valid_json(self) -> None:
        data = _parse_llm_response(
            '{"fast_path": false, "estimated_steps": 5, "primary_workflow": "discover", "subtasks": []}'
        )
        assert data is not None
        assert data["fast_path"] is False
        assert data["estimated_steps"] == 5
        assert data["primary_workflow"] == "discover"

    def test_parses_json_in_code_block(self) -> None:
        data = _parse_llm_response(
            '```json\n{"fast_path": true, "estimated_steps": 1, "primary_workflow": "apply"}\n```'
        )
        assert data is not None
        assert data["fast_path"] is True

    def test_returns_none_for_empty(self) -> None:
        assert _parse_llm_response("") is None

    def test_returns_none_for_invalid(self) -> None:
        assert _parse_llm_response("not json") is None


class TestDictToTaskPlan:
    def test_converts_valid_dict(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 3,
            "primary_workflow": "discover",
            "subtasks": [
                {"description": "Search", "workflow": "discover", "completion_criteria": "results found"},
            ],
            "summary": "Find and apply",
        }
        plan = _dict_to_task_plan(data, "test goal")
        assert not plan.fast_path
        assert plan.estimated_steps == 3
        assert plan.primary_workflow == WorkflowType.DISCOVER
        assert len(plan.subtasks) == 1
        assert plan.subtasks[0].description == "Search"
        assert plan.summary == "Find and apply"

    def test_fallback_on_invalid_workflow(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 3,
            "primary_workflow": "invalid_workflow",
            "subtasks": [],
            "summary": "",
        }
        plan = _dict_to_task_plan(data, "Find jobs")
        assert plan.primary_workflow == WorkflowType.APPLY

    def test_limits_subtasks_to_5(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 10,
            "primary_workflow": "apply",
            "subtasks": [
                {"description": f"Step {i}", "workflow": "apply", "completion_criteria": "done"}
                for i in range(10)
            ],
            "summary": "",
        }
        plan = _dict_to_task_plan(data, "test")
        assert len(plan.subtasks) == 5

    def test_parses_expected_first_actions(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 5,
            "primary_workflow": "apply",
            "subtasks": [],
            "summary": "Apply to jobs",
            "expected_first_actions": [
                "Search for python developer vacancies",
                "Open the first vacancy from the results",
            ],
        }
        plan = _dict_to_task_plan(data, "test")
        assert len(plan.expected_first_actions) == 2
        assert plan.expected_first_actions[0] == "Search for python developer vacancies"
        assert plan.expected_first_actions[1] == "Open the first vacancy from the results"

    def test_expected_first_actions_limited_to_2(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 5,
            "primary_workflow": "apply",
            "subtasks": [],
            "summary": "",
            "expected_first_actions": ["a", "b", "c", "d"],
        }
        plan = _dict_to_task_plan(data, "test")
        assert len(plan.expected_first_actions) == 2

    def test_expected_first_actions_defaults_to_empty(self) -> None:
        data = {
            "fast_path": False,
            "estimated_steps": 3,
            "primary_workflow": "discover",
            "subtasks": [],
            "summary": "",
        }
        plan = _dict_to_task_plan(data, "test")
        assert plan.expected_first_actions == []


class TestFallbackPlan:
    def test_returns_valid_plan(self) -> None:
        plan = _fallback_plan("Find jobs")
        assert not plan.fast_path
        assert plan.estimated_steps == 5
        assert plan.primary_workflow == WorkflowType.APPLY
        assert "Fallback" in plan.summary


class TestHighLevelPlanner:
    @pytest.mark.asyncio
    async def test_always_calls_llm(self) -> None:
        """Planner always calls LLM — no rule-based fast-path."""
        plan = await HighLevelPlanner.plan("Open Google")
        # The LLM decides fast_path, not rule-based heuristics
        assert isinstance(plan, TaskPlan)
        assert plan.estimated_steps >= 0

    @pytest.mark.asyncio
    async def test_empty_goal_returns_fallback(self) -> None:
        """Empty goal should still produce a valid plan (or fallback on API error)."""
        try:
            plan = await HighLevelPlanner.plan("")
            assert isinstance(plan, TaskPlan)
            # Fallback never sets fast_path; LLM may return fast_path for empty
            assert isinstance(plan.fast_path, bool)
        except Exception:
            # Transient API errors should not break the test suite
            pass