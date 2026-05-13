"""High-level planner for browser tasks.

Performs one LLM call at the start of a task to decompose the user's
goal into a structured plan.  Returns a ``TaskPlan`` that the
orchestrator uses to configure the workflow.

The LLM decides ``fast_path: true`` for trivial 1-click tasks,
allowing the orchestrator to skip the full hierarchical pipeline.
No rule-based heuristics — the model validates and decides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.thirdhand.browser_core.sub_intent import WorkflowType
from src.thirdhand.config import settings
from src.thirdhand.services.llm import ainvoke_with_retry, create_llm

logger = structlog.get_logger(__name__)


@dataclass
class SubTask:
    """One step in a decomposed task plan."""

    description: str
    workflow: WorkflowType
    completion_criteria: str


@dataclass
class TaskPlan:
    """Structured plan returned by the HighLevelPlanner.

    The orchestrator switches on ``fast_path`` to decide whether to
    use the full hierarchical pipeline or skip directly to execution.
    """

    # When True, skip WorkflowSelector/Policy (Planner already called).
    fast_path: bool = False
    # Decomposed subtasks (empty for fast_path).
    subtasks: list[SubTask] = field(default_factory=list)
    # Estimated number of browser steps.
    estimated_steps: int = 0
    # Primary workflow type for the task.
    primary_workflow: WorkflowType = WorkflowType.APPLY
    # Human-readable summary of the plan.
    summary: str = ""
    # Expected flow — sequence of semantic steps for the LLM context.
    expected_flow: list[dict] = field(default_factory=list)
    # Concrete first 1-2 actions the agent should take (e.g. "search for X", "open Y").
    expected_first_actions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM-based planner
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """You are a browser task planner. Given a user's goal, produce a structured plan.

Return ONLY valid JSON with this exact structure:
{
  "fast_path": false,
  "estimated_steps": <integer>,
  "primary_workflow": "<discover|select|apply|monitor|fill>",
  "subtasks": [
    {
      "description": "<short description of this subtask>",
      "workflow": "<discover|select|apply|monitor|fill>",
      "completion_criteria": "<when this subtask is done>"
    }
  ],
  "summary": "<one-line summary of the plan>",
  "expected_first_actions": [
    "<concrete first action the agent should take>",
    "<concrete second action, if applicable>"
  ]
}

Rules:
- fast_path: set to true ONLY for trivial tasks that are 1 click or 1 read
  (e.g. "open google.com", "what is the page title", "check my balance").
  For everything else set fast_path to false.
- estimated_steps: realistic number of browser actions (clicks, types, navigations).
- primary_workflow: the main workflow type for this task.
- subtasks: break the task into logical phases (max 5 subtasks).
- Each subtask has a workflow type matching its phase.
- Use "discover" for searching/browsing, "select" for choosing, "apply" for acting.
- Use "monitor" for watching, "fill" for form filling.
- expected_first_actions: list 1-2 concrete actions the agent should do first.
  Be specific but do NOT include element selectors or URLs.
  Example for "apply to 3 python jobs on hh.ru":
  ["Search for python developer vacancies", "Open the first vacancy from the results"]
- Do NOT include site-specific instructions.
- Do NOT include element selectors or URLs."""


async def plan_task(goal: str, context_text: str = "") -> TaskPlan:
    """Plan a browser task.

    Always makes one LLM call.  The model returns a JSON plan with
    ``fast_path`` set to ``true`` for trivial tasks and ``false``
    for complex multi-step tasks.

    Falls back to a sensible default when the LLM call fails.
    """
    llm = create_llm(model=settings.BROWSER_MODEL or None, temperature=0.0)

    user_message = f"User goal: {goal.strip()}"
    if context_text.strip():
        user_message += f"\nUser context: {context_text.strip()}"

    try:
        response = await ainvoke_with_retry(
            llm,
            [
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        content = getattr(response, "content", "") or ""
        plan_dict = _parse_llm_response(content)

        if plan_dict is None:
            logger.warning("browser_core_planner_parse_failed, using fallback")
            return _fallback_plan(goal)

        return _dict_to_task_plan(plan_dict, goal)

    except Exception as exc:
        logger.warning("browser_core_planner_llm_failed", error=str(exc))
        return _fallback_plan(goal)


def _parse_llm_response(content: str) -> dict[str, Any] | None:
    """Extract JSON from the LLM response."""
    text = content.strip()
    if not text:
        return None

    # Strip markdown code fence if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _dict_to_task_plan(data: dict[str, Any], goal: str) -> TaskPlan:
    """Convert a parsed dict to a TaskPlan."""
    workflow_str = str(data.get("primary_workflow", "") or "").strip().lower()
    try:
        primary_workflow = WorkflowType(workflow_str)
    except ValueError:
        primary_workflow = WorkflowType.APPLY

    subtasks_raw = data.get("subtasks") or []
    subtasks: list[SubTask] = []
    for item in subtasks_raw[:5]:
        if isinstance(item, dict):
            wf_str = str(item.get("workflow", "") or "").strip().lower()
            try:
                wf = WorkflowType(wf_str)
            except ValueError:
                wf = primary_workflow
            subtasks.append(SubTask(
                description=str(item.get("description", "") or ""),
                workflow=wf,
                completion_criteria=str(item.get("completion_criteria", "") or ""),
            ))

    expected_first_actions_raw = data.get("expected_first_actions") or []
    expected_first_actions = [
        str(a).strip() for a in expected_first_actions_raw[:2] if a
    ]

    return TaskPlan(
        fast_path=bool(data.get("fast_path", False)),
        subtasks=subtasks,
        estimated_steps=int(data.get("estimated_steps", 0)),
        primary_workflow=primary_workflow,
        summary=str(data.get("summary", "") or ""),
        expected_flow=data.get("expected_flow") or [],
        expected_first_actions=expected_first_actions,
    )


def _fallback_plan(goal: str) -> TaskPlan:
    """Fallback plan when LLM planning fails.

    Uses APPLY as default — the least restrictive workflow.
    The LLM can use all tools and is not prohibited from any action.
    For maximum autonomy, no workflow-specific prompt is injected
    unless ALTERNATIVE_POLICY later detects a specific situation.
    """
    return TaskPlan(
        fast_path=False,
        estimated_steps=5,
        primary_workflow=WorkflowType.APPLY,
        summary=f"Fallback plan for: {goal[:80]}",
    )


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------

class HighLevelPlanner:
    """High-level planner for browser tasks.

    Usage::

        plan = await HighLevelPlanner.plan(goal, context)
        if plan.fast_path:
            # Skip to executor
        else:
            # Use full pipeline
    """

    @staticmethod
    async def plan(goal: str, context: str = "") -> TaskPlan:
        """Plan a browser task. Returns a ``TaskPlan``."""
        return await plan_task(goal, context)