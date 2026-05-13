"""Workflow type definitions and PageType-based selector.

The initial workflow type is determined by the ``HighLevelPlanner``
(see ``planner.py``).  ``WorkflowSelector`` is used only for
structural PageType → WorkflowType mapping when the page changes.

No keyword matching, no goal text analysis — only structural signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.thirdhand.browser_core.page_classifier import PageType


class WorkflowType(str, Enum):
    """High-level execution mode for the browser agent."""

    DISCOVER = "discover"
    SELECT = "select"
    APPLY = "apply"
    MONITOR = "monitor"
    FILL = "fill"


@dataclass
class WorkflowSpec:
    """Full specification for one workflow type."""

    workflow_type: WorkflowType
    completion_criteria: str
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    prohibited_patterns: list[str] = field(default_factory=list)
    prompt_block: str = ""


# ---------------------------------------------------------------------------
# Page-type-based workflow inference
# ---------------------------------------------------------------------------

_PAGE_TYPE_TO_WORKFLOW: dict[PageType, WorkflowType] = {
    PageType.SEARCH_RESULTS: WorkflowType.DISCOVER,
    PageType.DETAIL_PAGE: WorkflowType.APPLY,
    PageType.FORM_PAGE: WorkflowType.FILL,
    PageType.LOGIN_PAGE: WorkflowType.APPLY,
    PageType.GENERIC_PAGE: WorkflowType.DISCOVER,
}


# ---------------------------------------------------------------------------
# Workflow specifications
# ---------------------------------------------------------------------------

_SAFE_TOOLS = frozenset({
    "inspect_page", "use_visual_assist", "wait", "scroll",
    "finish_task", "ask_user",
})

_OBSERVATION_TOOLS = frozenset({
    "open_browser", "goto_url", "click", "type_text",
    "press_key", "scroll", "wait",
})


def _build_spec(workflow: WorkflowType) -> WorkflowSpec:
    """Build the full specification for a workflow type."""
    if workflow == WorkflowType.DISCOVER:
        return WorkflowSpec(
            workflow_type=workflow,
            completion_criteria="A useful set of candidates has been collected and summarized.",
            allowed_tools=_SAFE_TOOLS | {"extract_page_items", "goto_url", "click"},
            prohibited_patterns=[
                "Do NOT submit applications, send résumés, or finalize purchases.",
                "Do NOT fill forms with user data unless explicitly asked.",
            ],
            prompt_block=(
                "\n---\n"
                "📋 WORKFLOW: DISCOVERY\n"
                "You are collecting candidates from a listing or search page.\n"
                "1. Use extract_page_items to get structured item data.\n"
                "2. Click on items to view details.\n"
                "3. Do NOT submit forms or apply — just gather information.\n"
                "4. Use goto_url(href) for navigation when href is available."
            ),
        )
    if workflow == WorkflowType.SELECT:
        return WorkflowSpec(
            workflow_type=workflow,
            completion_criteria="The best option(s) have been identified and reported.",
            allowed_tools=_SAFE_TOOLS | {"extract_page_items", "goto_url", "click"},
            prohibited_patterns=[
                "Do NOT submit applications or finalize purchases.",
                "Do NOT fill forms unless comparing options requires it.",
            ],
            prompt_block=(
                "\n---\n"
                "📋 WORKFLOW: SELECTION\n"
                "You are comparing visible options to choose the best one.\n"
                "1. Compare options carefully before navigating away.\n"
                "2. Finish with the selected option(s), not with a fake action.\n"
                "3. Do NOT submit forms or apply."
            ),
        )
    if workflow == WorkflowType.APPLY:
        return WorkflowSpec(
            workflow_type=workflow,
            completion_criteria="The primary action has been completed or user help is required.",
            allowed_tools=_SAFE_TOOLS | _OBSERVATION_TOOLS | {"extract_page_items"},
            prohibited_patterns=[],
            prompt_block=(
                "\n---\n"
                "📋 WORKFLOW: APPLY / ACT\n"
                "You are executing the user's requested action end-to-end.\n"
                "1. Follow through with the live UI until done.\n"
                "2. If blocked by missing data (password, OTP, captcha), call ask_user.\n"
                "3. Do NOT guess or invent data."
            ),
        )
    if workflow == WorkflowType.MONITOR:
        return WorkflowSpec(
            workflow_type=workflow,
            completion_criteria="The expected change has been detected or a timeout reached.",
            allowed_tools=_SAFE_TOOLS | {"inspect_page"},
            prohibited_patterns=[
                "Do NOT click, type, or submit anything.",
                "Only observe and report changes.",
            ],
            prompt_block=(
                "\n---\n"
                "📋 WORKFLOW: MONITOR\n"
                "You are watching for changes on the page.\n"
                "1. Call inspect_page periodically to check for updates.\n"
                "2. Report when the expected change occurs.\n"
                "3. Do NOT interact with the page — only observe."
            ),
        )
    return WorkflowSpec(
        workflow_type=workflow,
        completion_criteria="All required fields have been filled.",
        allowed_tools=_SAFE_TOOLS | {"type_text", "click", "press_key"},
        prohibited_patterns=[
            "Do NOT submit the form unless explicitly asked.",
            "Do NOT navigate away from the form.",
        ],
        prompt_block=(
            "\n---\n"
            "📋 WORKFLOW: FILL FORM\n"
            "You are filling a form with provided data.\n"
            "1. Use type_text with element_id from inspect_page.\n"
            "2. Fill required fields first.\n"
            "3. Do NOT submit until all fields are filled."
        ),
    )


class WorkflowSelector:
    """Rule-based workflow selector — PageType only, no keyword matching.

    The initial workflow type comes from ``HighLevelPlanner``.
    This selector is used only for structural PageType → WorkflowType
    mapping when the page context changes.
    """

    @staticmethod
    def from_page_type(page_type: PageType) -> WorkflowType:
        """Infer the appropriate workflow from the current page structure."""
        return _PAGE_TYPE_TO_WORKFLOW.get(page_type, WorkflowType.DISCOVER)

    @staticmethod
    def build_spec(workflow: WorkflowType) -> WorkflowSpec:
        """Return the full specification for a workflow type."""
        return _build_spec(workflow)
