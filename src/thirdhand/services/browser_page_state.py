"""Structured page-state derivation for generic browser autonomy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.thirdhand.services.browser_recovery import dom_evidence_suggests_captcha


@dataclass(frozen=True)
class BrowserPageState:
    """Compact structured understanding of the current browser page."""

    screen_kind: str
    candidate_actions: tuple[str, ...]
    required_inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    can_proceed_without_user: bool
    confidence: float
    dominant_heading: str = ""
    primary_action_label: str = ""
    action_surface_kind: str = ""
    action_surface_present: bool = False
    fillable_count: int = 0
    interactive_count: int = 0


@dataclass(frozen=True)
class TerminalOutcomeInference:
    """Structured runtime guess about whether the browser task already reached a terminal outcome."""

    completed: bool
    confidence: float
    reason_code: str
    explanation: str


def _load_snapshot(snapshot_json: str) -> dict[str, Any]:
    try:
        snapshot = json.loads(snapshot_json) if snapshot_json else {}
    except Exception:
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


def _input_kind(item: dict[str, Any]) -> str:
    tag = str(item.get("tag", "") or "").lower()
    if tag not in {"input", "textarea", "select"} and not item.get("fillable"):
        return ""
    ty = str(item.get("type", "") or "text").lower()
    hints = " ".join(
        str(item.get(key, "") or "")
        for key in ("text", "name", "placeholder", "role", "autocomplete", "html_id")
    ).lower()

    if ty == "password" or "password" in hints or "парол" in hints:
        return "password"
    if any(tok in hints for tok in ("otp", "one-time", "verification", "sms", "смс", "код", "code", "pin")):
        return "verification_code"
    if ty == "email" or any(
        tok in hints for tok in ("email", "e-mail", "login", "username", "user", "логин", "почт")
    ):
        return "login_identity"
    if ty == "tel" or any(tok in hints for tok in ("phone", "mobile", "телеф", "номер")):
        return "phone"
    if any(tok in hints for tok in ("address", "street", "city", "zip", "адрес", "город", "улиц", "дом")):
        return "address"
    return ""


def _value_looks_missing(item: dict[str, Any], kind: str) -> bool:
    if not kind or kind == "password":
        return False
    preview = str(item.get("value_preview", "") or "").strip()
    return preview == ""


def _candidate_actions(snapshot: dict[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in snapshot.get("interactive", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("fillable") is True:
            continue
        text = " ".join(str(item.get("text", "") or "").split()).strip()
        if not text:
            continue
        low = text.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(text)
        if len(out) >= 5:
            break
    return tuple(out)


def _dominant_heading(snapshot: dict[str, Any]) -> str:
    headings = snapshot.get("headings", []) or []
    for item in headings:
        text = " ".join(str(item or "").split()).strip()
        if text:
            return text
    text = " ".join(str(snapshot.get("text", "") or "").split()).strip()
    if not text:
        return ""
    for chunk in text.split(". "):
        candidate = chunk.strip()
        if candidate:
            return candidate[:120]
    return text[:120]


def _action_surface_kind(
    *,
    screen_kind: str,
    candidate_actions: tuple[str, ...],
    fillable_count: int,
) -> str:
    if screen_kind == "challenge":
        return "challenge_gate"
    if screen_kind == "oauth_selection":
        return "oauth_gate"
    if screen_kind == "code_verification":
        return "verification_gate"
    if screen_kind == "login":
        return "login_form"
    if screen_kind == "form":
        return "editable_form"
    if screen_kind == "selection_list":
        return "selection_list"
    if screen_kind == "actionable_page":
        return "single_action_surface" if len(candidate_actions) <= 1 else "action_menu"
    if fillable_count == 0 and not candidate_actions:
        return "passive_content"
    return "unknown"


def _duplicate_action_labels_present(snapshot: dict[str, Any]) -> bool:
    labels: list[str] = []
    for item in snapshot.get("interactive", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("fillable") is True:
            continue
        text = " ".join(str(item.get("text", "") or "").split()).strip().lower()
        if text:
            labels.append(text)
    return len(labels) != len(set(labels)) and len(labels) >= 2


def derive_browser_page_state(
    *,
    snapshot_json: str,
    probe: dict[str, Any] | None = None,
    site_key: str = "",
) -> BrowserPageState:
    """Derive a generic page state from DOM snapshot + optional session probe."""
    from src.thirdhand.services.browser_auth import (
        classify_browser_barrier,
        snapshot_allows_ask_user_2fa,
    )

    snapshot = _load_snapshot(snapshot_json)
    probe_data = probe if isinstance(probe, dict) else {}
    candidate_actions = _candidate_actions(snapshot)
    barrier = classify_browser_barrier(site_key, probe_data, snapshot)
    interactive = snapshot.get("interactive", []) or []
    has_duplicate_actions = _duplicate_action_labels_present(snapshot)
    dominant_heading = _dominant_heading(snapshot)

    required_inputs_seen: list[str] = []
    missing_inputs_seen: list[str] = []
    fillable_count = 0
    for item in interactive:
        if not isinstance(item, dict):
            continue
        if item.get("fillable") is True:
            fillable_count += 1
        kind = _input_kind(item)
        if not kind:
            continue
        if kind not in required_inputs_seen:
            required_inputs_seen.append(kind)
        if _value_looks_missing(item, kind) and kind not in missing_inputs_seen:
            missing_inputs_seen.append(kind)

    if dom_evidence_suggests_captcha(snapshot_json):
        screen_kind = "challenge"
        confidence = 0.98
    elif barrier.oauth_service_selection_barrier:
        screen_kind = "oauth_selection"
        confidence = 0.95
    elif snapshot_allows_ask_user_2fa(snapshot):
        if "verification_code" not in required_inputs_seen:
            required_inputs_seen.append("verification_code")
        if "verification_code" not in missing_inputs_seen:
            missing_inputs_seen.append("verification_code")
        screen_kind = "code_verification"
        confidence = 0.94
    elif barrier.looks_like_login_surface:
        screen_kind = "login"
        confidence = 0.88
    elif fillable_count >= 2:
        screen_kind = "form"
        confidence = 0.72
    elif len(candidate_actions) >= 3:
        screen_kind = "selection_list"
        confidence = 0.64
    elif candidate_actions:
        screen_kind = "actionable_page"
        confidence = 0.58
    else:
        screen_kind = "unknown"
        confidence = 0.35

    if has_duplicate_actions and screen_kind in {"actionable_page", "selection_list"}:
        confidence = min(confidence, 0.48)

    can_proceed_without_user = True
    if screen_kind in {"challenge", "code_verification"}:
        can_proceed_without_user = False
    elif not candidate_actions and fillable_count == 0:
        can_proceed_without_user = False

    action_surface_kind = _action_surface_kind(
        screen_kind=screen_kind,
        candidate_actions=candidate_actions,
        fillable_count=fillable_count,
    )
    action_surface_present = action_surface_kind not in {
        "passive_content",
        "unknown",
    }

    return BrowserPageState(
        screen_kind=screen_kind,
        candidate_actions=candidate_actions,
        required_inputs=tuple(required_inputs_seen),
        missing_inputs=tuple(missing_inputs_seen),
        can_proceed_without_user=can_proceed_without_user,
        confidence=round(confidence, 2),
        dominant_heading=dominant_heading,
        primary_action_label=candidate_actions[0] if candidate_actions else "",
        action_surface_kind=action_surface_kind,
        action_surface_present=action_surface_present,
        fillable_count=fillable_count,
        interactive_count=len([item for item in interactive if isinstance(item, dict)]),
    )


def summarize_browser_page_state(state: BrowserPageState) -> str:
    """Compact runtime guidance snippet derived from page state."""
    actions = ", ".join(state.candidate_actions[:3]) or "(none)"
    required = ", ".join(state.required_inputs) or "(none)"
    missing = ", ".join(state.missing_inputs) or "(none)"
    proceed = "yes" if state.can_proceed_without_user else "no"
    heading = state.dominant_heading or "(none)"
    primary_action = state.primary_action_label or "(none)"
    action_surface = "yes" if state.action_surface_present else "no"
    return (
        "Derived page state:\n"
        f"- screen_kind: {state.screen_kind}\n"
        f"- dominant_heading: {heading}\n"
        f"- candidate_actions: {actions}\n"
        f"- primary_action_label: {primary_action}\n"
        f"- required_inputs: {required}\n"
        f"- missing_inputs: {missing}\n"
        f"- action_surface_kind: {state.action_surface_kind or '(none)'}\n"
        f"- action_surface_present: {action_surface}\n"
        f"- fillable_count: {state.fillable_count}\n"
        f"- interactive_count: {state.interactive_count}\n"
        f"- can_proceed_without_user: {proceed}\n"
        f"- confidence: {state.confidence:.2f}"
    )


def _normalized_sub_intent_mode(sub_intent: str) -> str:
    raw = (sub_intent or "").strip().lower()
    if raw.endswith("discover_candidates"):
        return "discover"
    if raw.endswith("select_targets"):
        return "select"
    return "apply"


def _snapshot_corpus(snapshot: dict[str, Any], heading: str = "") -> str:
    return " ".join(
        [
            " ".join(str(x or "") for x in (snapshot.get("headings") or [])),
            str(snapshot.get("text", "") or ""),
            heading,
        ]
    ).lower()


def _looks_like_success_marker(snapshot: dict[str, Any], heading: str) -> bool:
    corpus = _snapshot_corpus(snapshot, heading)
    return any(
        token in corpus
        for token in (
            "success",
            "successful",
            "completed",
            "confirmed",
            "thank you",
            "conversation",
            "receipt",
            "order #",
            "sent",
            "submitted",
            "done",
            "успеш",
            "готово",
            "отправ",
            "подтверж",
            "заказ",
            "чек",
            "диалог",
        )
    )


def _url_path_changed(before_url: str, after_url: str) -> bool:
    if not before_url or not after_url or before_url == after_url:
        return False
    before_path = before_url.split("://", 1)[-1].split("/", 1)[-1]
    after_path = after_url.split("://", 1)[-1].split("/", 1)[-1]
    return before_path != after_path


def _looks_like_selected_target_opened(
    *,
    before: BrowserPageState,
    after: BrowserPageState,
    url_changed: bool,
    heading_changed: bool,
    previous_action_no_longer_offered: bool,
) -> bool:
    return (
        before.screen_kind == "selection_list"
        and after.screen_kind in {"actionable_page", "form", "unknown"}
        and after.can_proceed_without_user
        and (url_changed or heading_changed)
        and previous_action_no_longer_offered
    )


def _looks_like_ambiguous_partial_state(
    *,
    after: BrowserPageState,
    after_snapshot: dict[str, Any],
) -> bool:
    corpus = _snapshot_corpus(after_snapshot, after.dominant_heading)
    partial_markers = (
        "draft",
        "saved draft",
        "autosaved",
        "save draft",
        "continue editing",
        "edit application",
        "update application",
        "review your order",
        "review order",
        "processing",
        "pending",
        "in progress",
        "resume later",
        "черновик",
        "сохранено",
        "сохранен",
        "продолжить редактирование",
        "редактировать",
        "на проверке",
        "обработка",
        "в процессе",
        "продолжить позже",
    )
    if not any(token in corpus for token in partial_markers):
        return False
    return after.action_surface_present or after.screen_kind in {"form", "actionable_page"}


def infer_terminal_outcome(
    *,
    sub_intent: str,
    tool_name: str,
    before_snapshot: str,
    after_snapshot: str,
    before_page_state: BrowserPageState | None,
    after_page_state: BrowserPageState | None,
    before_url: str,
    after_url: str,
) -> TerminalOutcomeInference:
    """Infer whether a page transition likely means the task is already complete.

    This detector is runtime-owned and state-based: it compares before/after page structure and
    only treats textual success wording as supporting evidence once the page transition itself
    already suggests completion.
    """

    mode = _normalized_sub_intent_mode(sub_intent)
    action_tool = (tool_name or "").strip().lower()
    before = before_page_state
    after = after_page_state
    before_snap = _load_snapshot(before_snapshot)
    after_snap = _load_snapshot(after_snapshot)
    before_url_norm = (before_url or "").strip()
    after_url_norm = (after_url or "").strip()

    if mode == "discover":
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.18,
            reason_code="discover_requires_explicit_finish",
            explanation=(
                "Discovery completion is semantic, so runtime does not auto-complete it from page"
                " transition alone."
            ),
        )

    if after is None:
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.12,
            reason_code="missing_after_state",
            explanation="No after-page state is available, so terminal outcome cannot be inferred safely.",
        )

    if after.screen_kind in {"challenge", "code_verification", "login", "oauth_selection"}:
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.05,
            reason_code="still_blocked_by_action_gate",
            explanation=(
                f"After the {action_tool or 'action'}, the page still shows a blocking gate"
                f" ({after.screen_kind})."
            ),
        )

    if _looks_like_ambiguous_partial_state(after=after, after_snapshot=after_snap):
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.09,
            reason_code="ambiguous_partial_or_draft_state",
            explanation=(
                "The after-page state still looks like a draft, partial save, review, or transient"
                " processing state, so runtime must not auto-complete it."
            ),
        )

    if after.missing_inputs:
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.08,
            reason_code="required_inputs_still_missing",
            explanation=(
                "The after-page state still requires missing inputs: "
                + ", ".join(after.missing_inputs)
                + "."
            ),
        )

    if before is None:
        return TerminalOutcomeInference(
            completed=False,
            confidence=0.2,
            reason_code="missing_before_state",
            explanation="No before-page state is available, so there is no transition evidence to compare.",
        )

    if after.action_surface_present and after.primary_action_label:
        before_action = (before.primary_action_label or "").strip().lower()
        after_action = (after.primary_action_label or "").strip().lower()
        if before_action and before_action == after_action:
            return TerminalOutcomeInference(
                completed=False,
                confidence=0.1,
                reason_code="same_primary_action_still_present",
                explanation=(
                    "The primary action surface is still present with the same main action, so the"
                    " task does not look terminal yet."
                ),
            )
    score = 0.0
    reasons: list[str] = []
    positive_reasons: list[str] = []

    action_surface_disappeared = before.action_surface_present and not after.action_surface_present
    if action_surface_disappeared:
        score += 0.38
        reasons.append("primary action surface disappeared")
        positive_reasons.append("action_surface_disappeared")

    screen_kind_became_passive = (
        before.screen_kind in {"login", "form", "selection_list", "actionable_page"}
        and after.screen_kind == "unknown"
        and not after.action_surface_present
    )
    if screen_kind_became_passive:
        score += 0.18
        reasons.append("screen kind changed from actionable to passive")
        positive_reasons.append("moved_to_passive_result_surface")

    required_inputs_reduced = len(after.required_inputs) < len(before.required_inputs)
    missing_inputs_reduced = len(after.missing_inputs) < len(before.missing_inputs)
    if required_inputs_reduced:
        score += 0.14
        reasons.append("required inputs decreased")
        positive_reasons.append("required_inputs_decreased")
    if missing_inputs_reduced:
        score += 0.18
        reasons.append("missing inputs decreased")
        positive_reasons.append("missing_inputs_decreased")

    authenticated_capabilities_increased = (
        before.screen_kind == "login"
        and after.screen_kind in {"selection_list", "actionable_page", "form", "unknown"}
        and after.can_proceed_without_user
    )
    if authenticated_capabilities_increased:
        score += 0.24
        reasons.append("authenticated capabilities increased after login")
        positive_reasons.append("authenticated_capabilities_increased")

    heading_changed = (
        bool(before.dominant_heading)
        and bool(after.dominant_heading)
        and before.dominant_heading != after.dominant_heading
    )
    url_changed = _url_path_changed(before_url_norm, after_url_norm)
    if url_changed and heading_changed:
        score += 0.12
        reasons.append("URL and dominant heading changed together")
        positive_reasons.append("url_and_heading_changed")
    elif url_changed:
        score += 0.07
        reasons.append("URL changed")
        positive_reasons.append("url_changed")
    elif heading_changed:
        score += 0.05
        reasons.append("dominant heading changed")
        positive_reasons.append("heading_changed")

    previous_action_no_longer_offered = (
        bool(before.primary_action_label)
        and before.primary_action_label.lower() not in {a.lower() for a in after.candidate_actions}
    )
    if previous_action_no_longer_offered:
        score += 0.14
        reasons.append("previous primary action is no longer offered in the same way")
        positive_reasons.append("previous_action_no_longer_offered")

    selected_target_opened = _looks_like_selected_target_opened(
        before=before,
        after=after,
        url_changed=url_changed,
        heading_changed=heading_changed,
        previous_action_no_longer_offered=previous_action_no_longer_offered,
    )
    if selected_target_opened:
        score += 0.32
        reasons.append("selection list transitioned into a stable opened target state")
        positive_reasons.append("selected_target_opened")

    success_marker_present = _looks_like_success_marker(after_snap, after.dominant_heading)
    if score >= 0.45 and success_marker_present:
        score += 0.06
        reasons.append("supporting success marker text is present")
        positive_reasons.append("supporting_success_text")

    score = min(score, 0.96)
    explanation = (
        "Detected terminal-state signals: " + "; ".join(reasons) + "."
        if reasons
        else "The before/after page states look too similar to infer terminal success."
    )

    completion_threshold = 0.72
    if mode == "select":
        completion_threshold = 0.58 if selected_target_opened else 0.72

    if score >= completion_threshold:
        primary_reason = positive_reasons[0] if positive_reasons else "state_transition_success"
        if "action_surface_disappeared" in positive_reasons and url_changed:
            primary_reason = "action_surface_replaced_after_navigation"
        elif "selected_target_opened" in positive_reasons and mode == "select":
            primary_reason = "selection_became_opened_target"
        elif "authenticated_capabilities_increased" in positive_reasons and mode == "select":
            primary_reason = "selection_became_opened_target"
        return TerminalOutcomeInference(
            completed=True,
            confidence=round(score, 2),
            reason_code=primary_reason,
            explanation=explanation,
        )

    if score >= 0.4:
        return TerminalOutcomeInference(
            completed=False,
            confidence=round(score, 2),
            reason_code="insufficient_transition_evidence",
            explanation=explanation,
        )

    return TerminalOutcomeInference(
        completed=False,
        confidence=max(round(score, 2), 0.16),
        reason_code="insufficient_transition_evidence",
        explanation=explanation,
    )
