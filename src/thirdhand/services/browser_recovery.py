"""Recovery policy and mechanics when the browser model stalls (e.g. empty tool calls). No policy LLM."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

from src.thirdhand.config import settings

logger = structlog.get_logger(__name__)

MAX_EMPTY_TOOL_RECOVERY_ATTEMPTS = 2

# DOM / snapshot text tokens indicating a human-verification or CAPTCHA surface (any language).
_CAPTCHA_PAGE_TEXT_TOKENS: tuple[str, ...] = (
    "капч",
    "captcha",
    "рекаптч",
    "recaptcha",
    "hcaptcha",
    "hcap",
    "не робот",
    "not a robot",
    "я не робот",
    "подтвердите, что вы не робот",
    "подтвердите что вы не робот",
    "пройдите капчу",
    "введите текст с картинки",
    "введите символы",
    "verify you are human",
    "are you a robot",
    "пройдите проверку",
)


def dom_evidence_suggests_captcha(snapshot_json: str) -> bool:
    """True when inspect_page JSON or raw text hints at CAPTCHA / robot check."""
    raw = (snapshot_json or "").strip()
    if not raw:
        return False
    try:
        data = json.loads(snapshot_json)
    except Exception:
        blob = raw.lower()
        return any(tok in blob for tok in _CAPTCHA_PAGE_TEXT_TOKENS)

    if not isinstance(data, dict):
        return False
    page_text = str(data.get("text", "") or "").lower()
    headings = data.get("headings") or []
    head_blob = " ".join(str(h) for h in headings if isinstance(h, str)).lower()
    blob = f"{page_text} {head_blob}"
    return any(tok in blob for tok in _CAPTCHA_PAGE_TEXT_TOKENS)


class NoToolsOutcome(str, Enum):
    """High-level decision after the browser LLM returns no tool calls."""

    CONTINUE_LOOP = "continue_loop"
    EXIT_USER_BLOCKING = "exit_user_blocking"
    EXIT_STALLED = "exit_stalled"


@dataclass(frozen=True)
class NoToolsPolicyResult:
    """Structured outcome of empty-tool-call handling (vision / recovery / escalation)."""

    outcome: NoToolsOutcome
    human_followup_message: str | None = None
    stall_reason_code: str = ""
    policy_debug_note: str = ""

    def recommended_trace_line(self, step: int) -> str:
        if self.outcome != NoToolsOutcome.EXIT_STALLED:
            return ""
        return f"Шаг {step}: модель не вызвала инструмент; политика: {self.stall_reason_code or 'stalled'}."


def empty_step_recovery_limit_reached(
    *, page_missing: bool, attempt_count_before_retry: int
) -> bool:
    """Return True when we must not run another empty-step recovery attempt."""
    return page_missing or attempt_count_before_retry >= MAX_EMPTY_TOOL_RECOVERY_ATTEMPTS


def explain_visual_assist_decision(
    *,
    site_key: str,
    snapshot_json: str,
    auth_guidance: str,
    recovery_attempt: int,
    dom_evidence_weak: bool = False,
    page_state: "BrowserPageState" | None = None,
) -> tuple[bool, str]:
    """Return (whether to call vision, short skip/reason code for logging)."""
    if not (
        settings.PICTURE_RECOGNITION_MODEL
        or settings.BROWSER_MODEL
        or settings.DEFAULT_MODEL
    ):
        return False, "no_vision_model_configured"
    if dom_evidence_suggests_captcha(snapshot_json):
        return True, "captcha_barrier"
    if auth_guidance:
        return True, "auth_surface"
    if page_state is not None:
        if page_state.screen_kind == "unknown":
            return True, "unknown_screen_kind"
        if page_state.confidence < 0.55:
            return True, "low_page_state_confidence"
        if (
            dom_evidence_weak
            and page_state.confidence < 0.75
            and recovery_attempt >= 0
        ):
            return True, "weak_dom_with_uncertain_page_state"
        if (
            recovery_attempt > 0
            and not page_state.can_proceed_without_user
            and page_state.screen_kind not in {"challenge", "code_verification"}
        ):
            return True, "blocked_page_state_after_recovery"
    if dom_evidence_weak and recovery_attempt > 0:
        return True, "weak_dom_after_recovery"
    try:
        snapshot = json.loads(snapshot_json)
    except Exception:
        return (
            (True, "malformed_snapshot_recovery")
            if recovery_attempt > 0
            else (False, "malformed_snapshot_no_recovery")
        )

    page_text = str(snapshot.get("text", "") or "").lower()
    interactive = snapshot.get("interactive", []) or []
    visible_text = " ".join(
        str(item.get("text", "")) for item in interactive if isinstance(item, dict)
    ).lower()
    if recovery_attempt > 0 and any(
        token in f"{page_text} {visible_text}"
        for token in ("откликнуться", "apply", "submit", "войти", "login", "continue", "дальше")
    ):
        return True, "post_stall_action_keywords"
    return False, "no_visual_assist_match"


def should_request_visual_assist(
    *,
    site_key: str,
    snapshot_json: str,
    auth_guidance: str,
    recovery_attempt: int,
    dom_evidence_weak: bool = False,
    page_state: "BrowserPageState" | None = None,
) -> bool:
    """Decide when a screenshot-based vision assist is worth the extra model call.

    Central recovery policy: auth surfaces, post-stall pages, weak DOM after recovery, site hints.
    """
    return explain_visual_assist_decision(
        site_key=site_key,
        snapshot_json=snapshot_json,
        auth_guidance=auth_guidance,
        recovery_attempt=recovery_attempt,
        dom_evidence_weak=dom_evidence_weak,
        page_state=page_state,
    )[0]


def auth_facts_model_stalled_no_tools(
    blocker_type: str, *, stall_reason_code: str = ""
) -> dict[str, Any]:
    """Structured facts when the run ends after the model returned no tools and recovery failed."""
    facts: dict[str, Any] = {
        "facts_version": 1,
        "source": "browser_agent",
        "outcome": "model_stalled_no_tools",
        "blocker_type": blocker_type,
    }
    if stall_reason_code:
        facts["stall_reason_code"] = stall_reason_code
    return facts


def human_message_after_no_tools_recovery(
    snapshot: str, *, runtime_guidance_prefix: str = ""
) -> str:
    """English follow-up HumanMessage after a successful empty-step recovery probe."""
    body = (
        "The model returned no tool call, so the runtime recovered the live page state. "
        "Continue from this snapshot and use tools.\n"
        f"{snapshot}"
    )
    prefix = (runtime_guidance_prefix or "").strip()
    if prefix:
        return f"{prefix}\n{body}"
    return body


def user_message_for_stalled_no_tools(*, stall_reason_code: str) -> str:
    """Russian user-facing line when the model stalled and recovery is exhausted."""
    code = (stall_reason_code or "").strip()
    if code == "recovery_exhausted":
        return (
            "Модель перестала вызывать инструменты после нескольких попыток восстановления. "
            "Проверь страницу в браузере и опиши, что видишь, или дай следующий шаг."
        )
    return "Агент остановился без явного завершения. Проверь открытую страницу и при необходимости уточни следующий шаг."


def snapshot_dom_evidence_looks_weak(snapshot_json: str) -> bool:
    """Heuristic: DOM snapshot may be too thin to steer the model (mechanical only)."""
    raw = (snapshot_json or "").strip()
    if not raw or raw == "{}":
        return True
    try:
        data = json.loads(snapshot_json)
    except Exception:
        return True
    if not isinstance(data, dict):
        return True
    interactive = data.get("interactive") or []
    text = str(data.get("text", "") or "").strip()
    if not interactive and len(text) < 80:
        return True
    return False


async def execute_empty_tool_step_recovery(flow: Any, *, step: int) -> str | None:
    """Re-probe the page and refresh auth/visual guidance after a step with no tool calls.

    `flow` is the browser flow `BrowserFlowStateMachine` (duck-typed to avoid import cycles).
    """
    from src.thirdhand.services import browser_flow as bf
    from src.thirdhand.services.browser_observation import maybe_build_visual_guidance
    from src.thirdhand.services.browser_page_state import (
        derive_browser_page_state,
        summarize_browser_page_state,
    )
    from src.thirdhand.services.llm import preview_for_log

    if empty_step_recovery_limit_reached(
        page_missing=flow.session.page is None,
        attempt_count_before_retry=flow.empty_step_recovery_count,
    ):
        return None

    flow.empty_step_recovery_count += 1
    await flow._refresh_page_context()
    await flow.transition(
        bf.BrowserFlowPhase.RECOVERING_EMPTY_STEP,
        step=step,
        current_url=flow.current_url,
        recovery_attempt=flow.empty_step_recovery_count,
    )
    logger.warning(
        "browser_no_tool_calls_recovering",
        user_id=flow.user_id,
        step=step,
        current_url=flow.current_url,
        recovery_attempt=flow.empty_step_recovery_count,
    )
    probe = await bf._log_session_probe(
        flow.session,
        flow.user_id,
        "after_empty_step",
        step=step,
    )
    await flow._refresh_page_context()
    snapshot = await flow.session.inspect_page()
    flow.last_snapshot = snapshot
    weak_dom = snapshot_dom_evidence_looks_weak(snapshot)
    if weak_dom:
        logger.info(
            "browser_recovery_weak_dom_after_empty_step",
            user_id=flow.user_id,
            step=step,
            recovery_attempt=flow.empty_step_recovery_count,
        )
    flow.page_state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe=probe,
        site_key=flow.site_key,
    )
    flow.page_state_guidance = summarize_browser_page_state(flow.page_state)
    flow.auth_guidance = bf._build_auth_guidance(flow.site_key, snapshot, probe)
    vision_goal = (flow.page_context_hint or "").strip()
    if not vision_goal:
        vision_goal = preview_for_log(flow.goal, limit=600)
    flow.visual_guidance = await maybe_build_visual_guidance(
        session=flow.session,
        user_id=flow.user_id,
        goal=flow.goal,
        site_key=flow.site_key,
        snapshot_json=snapshot,
        auth_guidance=flow.auth_guidance,
        recovery_attempt=flow.empty_step_recovery_count,
        dom_evidence_weak=weak_dom,
        goal_text_for_vision=vision_goal,
        page_state=flow.page_state,
    )
    await flow.transition(
        bf.BrowserFlowPhase.READY_FOR_MODEL,
        step=step,
        current_url=flow.current_url,
        recovery_attempt=flow.empty_step_recovery_count,
        auto_login=False,
    )
    logger.info(
        "browser_no_tool_calls_recovered",
        user_id=flow.user_id,
        step=step,
        recovery_attempt=flow.empty_step_recovery_count,
        auto_login=False,
        snapshot_preview=preview_for_log(snapshot, limit=1000),
    )
    await bf._emit_progress(
        flow.progress_callback,
        "Обновил состояние страницы и продолжаю искать рабочий следующий шаг.",
    )
    return snapshot


async def resolve_no_tools_after_llm_step(
    flow: Any,
    *,
    step: int,
    compose_runtime_guidance: Callable[[], str],
) -> NoToolsPolicyResult:
    """Run recovery after an empty tool-call step and choose: continue, user block, or stall out.

    Sets ``flow.blocking_*`` before returning ``EXIT_USER_BLOCKING`` (same as mechanical recovery).
    """
    try:
        snapshot = await execute_empty_tool_step_recovery(flow, step=step)
    except Exception as exc:
        logger.warning(
            "browser_no_tool_calls_recovery_failed",
            user_id=flow.user_id,
            step=step,
            error=str(exc),
        )
        return NoToolsPolicyResult(
            outcome=NoToolsOutcome.EXIT_STALLED,
            stall_reason_code="recovery_exception",
            policy_debug_note=f"Empty-step recovery raised {type(exc).__name__}: {exc}",
        )

    if flow.blocking_message:
        return NoToolsPolicyResult(
            outcome=NoToolsOutcome.EXIT_USER_BLOCKING,
            policy_debug_note="Recovery interrupted: auth/runtime needs user input (structured ask).",
        )

    if snapshot is not None:
        prefix = (compose_runtime_guidance() or "").strip()
        return NoToolsPolicyResult(
            outcome=NoToolsOutcome.CONTINUE_LOOP,
            human_followup_message=human_message_after_no_tools_recovery(
                snapshot,
                runtime_guidance_prefix=prefix,
            ),
            policy_debug_note="Recovered snapshot and refreshed guidance; retry model step.",
        )

    if flow.session.page is None:
        return NoToolsPolicyResult(
            outcome=NoToolsOutcome.EXIT_STALLED,
            stall_reason_code="page_missing",
            policy_debug_note="No tool calls and browser page is not available after recovery policy.",
        )

    return NoToolsPolicyResult(
        outcome=NoToolsOutcome.EXIT_STALLED,
        stall_reason_code="recovery_exhausted",
        policy_debug_note="No tool calls and empty-step recovery limit reached or returned no new snapshot.",
    )
