"""Generic post-action verification for browser steps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from src.thirdhand.services.browser_page_state import BrowserPageState

# Local semantic diff thresholds
LOCAL_DIFF_TEXT_SIMILARITY_THRESHOLD = 0.35
LOCAL_DIFF_CONTAINER_SIZE_THRESHOLD = 0.25

ActionKind = Literal["click", "type_text", "type_secret", "press_key", "scroll", "wait"]
ActionIntent = Literal[
    "submit",
    "toggle",
    "open_details",
    "navigate",
    "select",
    "dismiss",
    "delete",
    "reply",
    "download",
    "upload",
    "unknown",
]
OutcomeStatus = Literal[
    "success",
    "probable_success",
    "no_effect",
    "ambiguous",
    "blocked",
    "tool_failure",
]


@dataclass(frozen=True)
class TargetSnapshot:
    """Compact target snapshot extracted from the generic DOM snapshot."""

    element_id: str = ""
    text: str = ""
    role: str = ""
    tag: str = ""
    href: str = ""
    html_id: str = ""
    fillable: bool = False
    container_text: str = ""
    semantic_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepExpectation:
    """What the browser step is trying to achieve."""

    step_id: str
    user_objective: str
    action_kind: ActionKind
    action_intent: ActionIntent
    target: TargetSnapshot
    expected_outcomes: tuple[str, ...] = ()
    forbidden_outcomes: tuple[str, ...] = ()
    reasoning: str = ""


@dataclass(frozen=True)
class LocalDiffEvidence:
    """Evidence from local semantic diff around target/container."""

    container_text_before: str = ""
    container_text_after: str = ""
    container_text_changed: bool = False
    container_size_ratio: float = 0.0
    sibling_count_before: int = 0
    sibling_count_after: int = 0
    sibling_added: bool = False
    sibling_removed: bool = False
    nearby_state_changed: bool = False
    confidence_boost: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VerificationEvidence:
    """Observed effects after the action."""

    tool_succeeded: bool = False
    tool_error: str = ""
    url_changed: bool = False
    screen_kind_changed: bool = False
    action_surface_changed: bool = False
    target_disappeared: bool = False
    target_changed: bool = False
    primary_action_changed: bool = False
    local_diff: LocalDiffEvidence = field(default_factory=LocalDiffEvidence)
    success_markers: tuple[str, ...] = ()
    failure_markers: tuple[str, ...] = ()
    blocker_markers: tuple[str, ...] = ()
    confidence: float = 0.0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepOutcome:
    """Structured result of post-action verification."""

    status: OutcomeStatus
    confidence: float
    summary: str
    evidence: VerificationEvidence
    should_continue: bool = True
    requires_user_input: bool = False


def _load_snapshot(snapshot_json: str) -> dict[str, Any]:
    try:
        raw = json.loads(snapshot_json) if snapshot_json else {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _extract_target_snapshot(snapshot_json: str, element_id: str) -> TargetSnapshot:
    if not element_id:
        return TargetSnapshot()
    snapshot = _load_snapshot(snapshot_json)
    for item in snapshot.get("interactive", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "") or "") != element_id:
            continue
        return TargetSnapshot(
            element_id=element_id,
            text=_normalize_text(str(item.get("text", "") or "")),
            role=str(item.get("role", "") or ""),
            tag=str(item.get("tag", "") or ""),
            href=str(item.get("href", "") or ""),
            html_id=str(item.get("html_id", "") or ""),
            fillable=bool(item.get("fillable", False)),
            container_text=_normalize_text(str(item.get("text", "") or "")),
            semantic_state={
                "type": str(item.get("type", "") or ""),
                "name": str(item.get("name", "") or ""),
                "placeholder": str(item.get("placeholder", "") or ""),
            },
        )
    return TargetSnapshot(element_id=element_id)


def _extract_container_text(snapshot: dict[str, Any], target_id: str, window_size: int = 5) -> tuple[str, int]:
    """Extract container text around a target element from the snapshot.
    
    Returns (container_text, interactive_count_in_container).
    """
    if not target_id:
        return "", 0
    
    interactive = snapshot.get("interactive", []) or []
    target_index = -1
    for i, item in enumerate(interactive):
        if not isinstance(item, dict):
            continue
        if str(item.get("id", "") or "") == target_id:
            target_index = i
            break
    
    if target_index < 0:
        return "", 0
    
    start = max(0, target_index - window_size)
    end = min(len(interactive), target_index + window_size + 1)
    
    container_items: list[str] = []
    for item in interactive[start:end]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "")
        if text.strip():
            container_items.append(text.strip())
    
    container_text = " | ".join(container_items)
    return container_text, end - start


def _compute_local_diff(
    *,
    before_snapshot: str,
    after_snapshot: str,
    target_id: str,
) -> LocalDiffEvidence:
    """Compute local semantic diff around the target element.
    
    This provides site-agnostic evidence of whether the action had a local effect
    on the target's container, even if the tool call failed or the target became stale.
    """
    before_snap = _load_snapshot(before_snapshot)
    after_snap = _load_snapshot(after_snapshot)
    
    before_container, before_count = _extract_container_text(before_snap, target_id)
    after_container, after_count = _extract_container_text(after_snap, target_id)
    
    container_text_changed = bool(before_container) and bool(after_container) and before_container != after_container
    container_size_ratio = after_count / before_count if before_count > 0 else 0.0
    
    sibling_added = after_count > before_count + 1
    sibling_removed = after_count < before_count - 1
    nearby_state_changed = container_text_changed or sibling_added or sibling_removed
    
    confidence_boost = 0.0
    notes: list[str] = []
    
    if container_text_changed:
        confidence_boost += 0.15
        notes.append("container text changed")
    
    if sibling_added:
        confidence_boost += 0.08
        notes.append("new sibling elements appeared")
    
    if sibling_removed:
        confidence_boost += 0.12
        notes.append("sibling elements disappeared")
    
    if container_size_ratio > LOCAL_DIFF_CONTAINER_SIZE_THRESHOLD and container_text_changed:
        confidence_boost += 0.05
        notes.append("container structure changed")
    
    if not nearby_state_changed and target_id:
        notes.append("no local container changes detected")
    
    return LocalDiffEvidence(
        container_text_before=before_container,
        container_text_after=after_container,
        container_text_changed=container_text_changed,
        container_size_ratio=round(container_size_ratio, 2),
        sibling_count_before=before_count,
        sibling_count_after=after_count,
        sibling_added=sibling_added,
        sibling_removed=sibling_removed,
        nearby_state_changed=nearby_state_changed,
        confidence_boost=round(min(confidence_boost, 0.35), 2),
        notes=tuple(notes),
    )


def _infer_action_intent(
    *,
    tool_name: str,
    before_page_state: BrowserPageState | None,
) -> ActionIntent:
    tool = (tool_name or "").strip().lower()
    if tool == "goto_url":
        return "navigate"
    if tool in {"type_text", "type_secret"}:
        return "submit" if before_page_state and before_page_state.screen_kind in {"login", "form"} else "unknown"
    if tool == "click":
        if before_page_state is None:
            return "unknown"
        if before_page_state.screen_kind in {"login", "form"}:
            return "submit"
        if before_page_state.screen_kind == "selection_list":
            return "open_details"
        if before_page_state.screen_kind == "actionable_page":
            return "navigate"
    return "unknown"


def _expected_outcomes(intent: ActionIntent) -> tuple[str, ...]:
    if intent == "submit":
        return ("target_state_changed", "confirmation_appeared", "action_surface_changed")
    if intent == "toggle":
        return ("target_state_changed", "local_state_changed")
    if intent == "open_details":
        return ("detail_surface_opened", "url_changed", "primary_action_changed")
    if intent == "navigate":
        return ("url_changed", "page_surface_changed")
    if intent == "dismiss":
        return ("action_surface_changed", "target_disappeared")
    if intent == "delete":
        return ("target_disappeared", "local_state_changed")
    return ()


def build_step_expectation(
    *,
    step_number: int,
    user_objective: str,
    tool_name: str,
    tool_args: dict[str, Any],
    before_snapshot: str,
    before_page_state: BrowserPageState | None,
) -> StepExpectation | None:
    """Build a generic step expectation from the chosen tool call."""
    tool = (tool_name or "").strip().lower()
    if tool not in {"click", "type_text", "type_secret", "press_key", "goto_url"}:
        return None
    target_id = str(tool_args.get("element_id", "") or "")
    target = _extract_target_snapshot(before_snapshot, target_id)
    intent = _infer_action_intent(tool_name=tool, before_page_state=before_page_state)
    return StepExpectation(
        step_id=f"step-{step_number}",
        user_objective=(user_objective or "").strip(),
        action_kind=tool,  # type: ignore[arg-type]
        action_intent=intent,
        target=target,
        expected_outcomes=_expected_outcomes(intent),
        reasoning="Generic browser step expectation built from tool call and pre-action page state.",
    )


def evaluate_step_outcome(
    *,
    expectation: StepExpectation,
    tool_result: Any,
    before_snapshot: str,
    after_snapshot: str,
    before_page_state: BrowserPageState | None,
    after_page_state: BrowserPageState | None,
    before_url: str,
    after_url: str,
) -> StepOutcome:
    """Evaluate generic step outcome from before/after state."""
    tool_error = ""
    if isinstance(tool_result, str) and tool_result.startswith("ERROR:"):
        tool_error = tool_result
    tool_succeeded = not bool(tool_error)

    before_target = expectation.target
    after_target = _extract_target_snapshot(after_snapshot, before_target.element_id)

    url_changed = _normalize_text(before_url) != _normalize_text(after_url)
    screen_kind_changed = (
        before_page_state is not None
        and after_page_state is not None
        and before_page_state.screen_kind != after_page_state.screen_kind
    )
    action_surface_changed = (
        before_page_state is not None
        and after_page_state is not None
        and (
            before_page_state.action_surface_present != after_page_state.action_surface_present
            or before_page_state.action_surface_kind != after_page_state.action_surface_kind
        )
    )
    primary_action_changed = (
        before_page_state is not None
        and after_page_state is not None
        and _normalize_text(before_page_state.primary_action_label)
        != _normalize_text(after_page_state.primary_action_label)
    )
    target_disappeared = bool(before_target.element_id) and not bool(after_target.text or after_target.tag or after_target.role)
    target_changed = (
        bool(before_target.element_id)
        and not target_disappeared
        and (
            before_target.text != after_target.text
            or before_target.role != after_target.role
            or before_target.href != after_target.href
            or before_target.tag != after_target.tag
        )
    )

    # Compute local semantic diff around target/container
    local_diff = _compute_local_diff(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        target_id=before_target.element_id,
    )

    success_markers: list[str] = []
    failure_markers: list[str] = []
    blocker_markers: list[str] = []
    notes: list[str] = []
    score = 0.0

    if target_disappeared:
        score += 0.28
        success_markers.append("target_disappeared")
    if target_changed:
        score += 0.32
        success_markers.append("target_changed")
    if action_surface_changed:
        score += 0.18
        success_markers.append("action_surface_changed")
    if primary_action_changed:
        score += 0.18
        success_markers.append("primary_action_changed")
    if url_changed:
        score += 0.16
        success_markers.append("url_changed")
    if screen_kind_changed:
        score += 0.14
        success_markers.append("screen_kind_changed")

    if after_page_state is not None and after_page_state.screen_kind in {
        "challenge",
        "code_verification",
        "login",
        "oauth_selection",
    }:
        blocker_markers.append(after_page_state.screen_kind)
        notes.append(f"After-state is blocked by {after_page_state.screen_kind}.")
        score -= 0.9

    if after_page_state is not None and after_page_state.missing_inputs:
        failure_markers.append("missing_inputs_remaining")
        notes.append("After-state still has missing required inputs.")
        score -= 0.22

    if not any(
        (target_disappeared, target_changed, action_surface_changed, primary_action_changed, url_changed, screen_kind_changed)
    ):
        # Check local diff as a fallback signal
        if local_diff.nearby_state_changed:
            score += local_diff.confidence_boost
            success_markers.append("local_container_changed")
            notes.append(f"Local container changed despite no global signals: {', '.join(local_diff.notes)}")
        else:
            notes.append("No meaningful semantic transition was detected.")
            score -= 0.35

    if tool_error:
        failure_markers.append("tool_error")
        notes.append("Tool execution returned an error string.")
        score -= 0.25
        if any((target_disappeared, target_changed, action_surface_changed, primary_action_changed, url_changed)):
            score += 0.18
            notes.append("Despite the tool error, the page still shows signs of a post-action transition.")

    confidence = max(0.0, min(1.0, 0.5 + score))

    if blocker_markers:
        status: OutcomeStatus = "blocked"
        summary = "After the action, the page moved into a blocked state."
        should_continue = False
        requires_user_input = True
    elif confidence >= 0.78:
        status = "success"
        summary = "The page shows a strong post-action transition consistent with step success."
        should_continue = True
        requires_user_input = False
    elif confidence >= 0.58:
        status = "probable_success"
        summary = "The page likely changed in the expected direction, but evidence is not fully conclusive."
        should_continue = True
        requires_user_input = False
    elif tool_error and confidence < 0.35:
        status = "tool_failure"
        summary = "The tool failed and the page did not provide enough evidence of a successful transition."
        should_continue = True
        requires_user_input = False
    elif confidence <= 0.2:
        status = "no_effect"
        summary = "The action appears to have had little or no semantic effect on the page."
        should_continue = True
        requires_user_input = False
    else:
        status = "ambiguous"
        summary = "The post-action signals are mixed, so the step outcome is ambiguous."
        should_continue = True
        requires_user_input = False

    evidence = VerificationEvidence(
        tool_succeeded=tool_succeeded,
        tool_error=tool_error,
        url_changed=url_changed,
        screen_kind_changed=screen_kind_changed,
        action_surface_changed=action_surface_changed,
        target_disappeared=target_disappeared,
        target_changed=target_changed,
        primary_action_changed=primary_action_changed,
        local_diff=local_diff,
        success_markers=tuple(success_markers),
        failure_markers=tuple(failure_markers),
        blocker_markers=tuple(blocker_markers),
        confidence=confidence,
        notes=tuple(notes),
    )
    return StepOutcome(
        status=status,
        confidence=confidence,
        summary=summary,
        evidence=evidence,
        should_continue=should_continue,
        requires_user_input=requires_user_input,
    )
