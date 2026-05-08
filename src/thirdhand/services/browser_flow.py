"""Browser orchestration: canonical phases, state machine, and main tool-calling loop."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TypeAlias

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from src.thirdhand.config import settings
from src.thirdhand.services.browser_auth import (
    looks_like_login_surface,
    snapshot_allows_ask_user_2fa,
    should_suppress_login_navigation_guidance,
)
from src.thirdhand.services.browser_observation import maybe_build_visual_guidance
from src.thirdhand.services.browser_page_state import (
    BrowserPageState,
    derive_browser_page_state,
    infer_terminal_outcome,
    summarize_browser_page_state,
)
from src.thirdhand.services.browser_recovery import (
    NoToolsOutcome,
    auth_facts_model_stalled_no_tools,
    dom_evidence_suggests_captcha,
    resolve_no_tools_after_llm_step,
    user_message_for_stalled_no_tools,
)
from src.thirdhand.services.browser_reporting import format_run_summary_telegram
from src.thirdhand.services.browser_runtime import BrowserSession as BrowserRuntimeSession
from src.thirdhand.services.browser_site_registry import (
    get_default_site_url,
    infer_site_key_from_url,
    normalize_site_name,
)
from src.thirdhand.services.browser_step_verification import (
    StepOutcome,
    build_step_expectation,
    evaluate_step_outcome,
)
from src.thirdhand.services.llm import ainvoke_with_retry, create_llm, preview_for_log

logger = structlog.get_logger(__name__)

ProgressCallback: TypeAlias = Callable[[str], Awaitable[None]]

_RUNTIME_SUCCESS_HIGH_CONFIDENCE = 0.72
_RUNTIME_SUCCESS_MEDIUM_CONFIDENCE = 0.4
_MEANINGFUL_PAGE_CHANGING_TOOLS = {
    "open_browser",
    "goto_url",
    "click",
    "type_text",
    "press_key",
    "wait_for_page",
}


class CanonicalBrowserPhase(str, Enum):
    """Stable phases for the browser subsystem (product-agnostic)."""

    INIT = "init"
    STARTING_BROWSER = "starting_browser"
    RESTORING_SESSION = "restoring_session"
    OBSERVING_PAGE = "observing_page"
    CLASSIFYING_BARRIER = "classifying_barrier"
    AUTH_FLOW = "auth_flow"
    PAGE_ACTION_FLOW = "page_action_flow"
    RECOVERY_FLOW = "recovery_flow"
    BLOCKED_WAITING_USER = "blocked_waiting_user"
    FINISHED = "finished"


_LEGACY_RUNTIME_TO_CANONICAL: dict[str, CanonicalBrowserPhase] = {
    "init": CanonicalBrowserPhase.INIT,
    "starting_browser": CanonicalBrowserPhase.STARTING_BROWSER,
    "restoring_session": CanonicalBrowserPhase.RESTORING_SESSION,
    "bootstrapping_page": CanonicalBrowserPhase.OBSERVING_PAGE,
    "detecting_auth": CanonicalBrowserPhase.CLASSIFYING_BARRIER,
    "ready_for_model": CanonicalBrowserPhase.PAGE_ACTION_FLOW,
    "waiting_for_model": CanonicalBrowserPhase.PAGE_ACTION_FLOW,
    "recovering_empty_step": CanonicalBrowserPhase.RECOVERY_FLOW,
    "blocked": CanonicalBrowserPhase.BLOCKED_WAITING_USER,
    "finished": CanonicalBrowserPhase.FINISHED,
}


def canonical_browser_phase(runtime_phase: str) -> CanonicalBrowserPhase:
    """Map a `BrowserFlowPhase` string (legacy runtime label) to the canonical phase."""
    key = (runtime_phase or "").strip().lower()
    mapped = _LEGACY_RUNTIME_TO_CANONICAL.get(key)
    if mapped is not None:
        return mapped
    logger.warning("browser_unknown_runtime_phase_label", runtime_phase=runtime_phase)
    return CanonicalBrowserPhase.INIT


class BrowserFlowPhaseTracker:
    """Hook: keep the canonical phase in one place for orchestration."""

    __slots__ = ("canonical",)

    def __init__(self) -> None:
        self.canonical: CanonicalBrowserPhase = CanonicalBrowserPhase.INIT

    def sync_from_runtime_label(self, runtime_phase: str) -> CanonicalBrowserPhase:
        self.canonical = canonical_browser_phase(runtime_phase)
        return self.canonical


@dataclass
class BrowserRunResult:
    """Final result from a browser automation run."""

    telegram_report: str
    trace: list[str]
    final_url: str
    needs_user_input: bool = False
    blocker_type: str = "other"
    debug_note: str = ""
    auth_facts: dict[str, Any] = field(default_factory=dict)
    barrier_kind: str = ""
    barrier_facts: dict[str, Any] = field(default_factory=dict)
    next_user_action: str = ""
    resume_strategy: str = "none"
    sub_intent: str = ""
    # Raw PNG, base64 without data-URL prefix; for Telegram on ask_user, stalls, step limit (debug).
    screenshot_png_base64: str = ""
    # Stable machine hint for Telegram / pending_task (e.g. user_must_complete_captcha).
    stop_reason: str = ""


class BrowserBlockerClass(str, Enum):
    """Coarse class of why the browser run cannot continue autonomously right now."""

    MACHINE_RESOLVABLE = "machine_resolvable"
    USER_DATA_NEEDED = "user_data_needed"
    MANUAL_CONFIRMATION_NEEDED = "manual_confirmation_needed"
    POLICY_FORBIDDEN_OR_IMPOSSIBLE = "policy_forbidden_or_impossible"


class BrowserFlowPhase(str, Enum):
    """Runtime orchestration phases (fine-grained transitions)."""

    INIT = "init"
    STARTING_BROWSER = "starting_browser"
    RESTORING_SESSION = "restoring_session"
    BOOTSTRAPPING_PAGE = "bootstrapping_page"
    DETECTING_AUTH = "detecting_auth"
    READY_FOR_MODEL = "ready_for_model"
    WAITING_FOR_MODEL = "waiting_for_model"
    RECOVERING_EMPTY_STEP = "recovering_empty_step"
    BLOCKED = "blocked"
    FINISHED = "finished"


class BrowserSubIntent(str, Enum):
    """Internal browser sub-goal (discovery vs selection vs apply).

    Chosen explicitly via persisted/graph ``browser_sub_intent``, not inferred from wording of ``goal``.
    """

    DISCOVER_CANDIDATES = "browser_discover_candidates"
    SELECT_TARGETS = "browser_select_targets"
    APPLY_TO_TARGETS = "browser_apply_to_targets"


BuildToolsFn: TypeAlias = Callable[
    [BrowserRuntimeSession, BrowserSubIntent], dict[str, StructuredTool]
]


def infer_browser_sub_intent(_goal: str) -> BrowserSubIntent:
    """Default sub-intent when the graph never set one; routing uses the live page + task text, not keyword tables."""
    return BrowserSubIntent.APPLY_TO_TARGETS


def _resolve_browser_sub_intent(goal: str, initial: str | None) -> BrowserSubIntent:
    """Prefer a persisted / graph-provided sub-intent when it is a known enum value."""
    raw = (initial or "").strip()
    if raw:
        try:
            return BrowserSubIntent(raw)
        except ValueError:
            logger.warning("browser_invalid_sub_intent_override", sub_intent=raw)
    return BrowserSubIntent.APPLY_TO_TARGETS


def sub_intent_execution_brief(sub: BrowserSubIntent) -> str:
    """Stable, test-visible summary of how this run should differ from a generic browser task (Stage 21)."""
    if sub is BrowserSubIntent.DISCOVER_CANDIDATES:
        return (
            "Mode: DISCOVERY — gather and summarize matching listings or options from the site.\n"
            "- Prefer search/listing pages, filters, and pagination until you have a useful candidate set.\n"
            "- Use login or saved credentials only if the listings are not visible without auth.\n"
            "- Do NOT start application/checkout/submit flows, send a résumé, or click final “apply/submit/отклик” "
            "actions; discovery ends with a concise list or table of findings.\n"
            "- Call finish_task when the user can see what was found, not when an application is sent.\n"
        )
    if sub is BrowserSubIntent.SELECT_TARGETS:
        return (
            "Mode: SELECTION — the user expects you to choose from options already on screen (or after minimal navigation).\n"
            "- Read inspect_page carefully; compare visible rows/cards before wandering away.\n"
            "- Avoid broad searches unless the current page is clearly not the right list.\n"
            "- When finishing, state which option(s) you selected and why; do not claim you submitted applications "
            "unless the user explicitly asked to apply.\n"
        )
    return (
        "Mode: APPLY / ACT — complete the user’s actionable goal on authenticated flows when required.\n"
        "- After login if needed, follow through with clicks that complete the requested task (e.g. apply, save, confirm), "
        "using inspect_page before irreversible steps.\n"
        "- Use saved credentials where configured.\n"
    )


def _sub_intent_user_task_message(goal: str, sub: BrowserSubIntent) -> str:
    """Human task line for the model (Russian, aligned with existing copy)."""
    mode_line = {
        BrowserSubIntent.DISCOVER_CANDIDATES: (
            "Режим: только поиск и сбор кандидатов — без откликов и отправки заявок."
        ),
        BrowserSubIntent.SELECT_TARGETS: (
            "Режим: выбор из уже видимых вариантов; минимум лишней навигации."
        ),
        BrowserSubIntent.APPLY_TO_TARGETS: (
            "Режим: выполнить действие до конца (включая отклик/отправку, если это в формулировке задачи)."
        ),
    }[sub]
    return (
        "Выполни задачу в браузере максимально автономно.\n"
        f"{mode_line}\n"
        f"Задача пользователя: {goal}"
    )


def _shorten(value: str, limit: int = 300) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _infer_start_url_from_goal(goal: str) -> str:
    """Infer a sensible first URL from the user's browser goal."""
    normalized = (goal or "").strip()
    if not normalized:
        return ""

    match = re.search(
        r"\b((?:https?://)?(?:[\w-]+\.)+[a-z]{2,})(/[^\s]*)?\b", normalized, flags=re.IGNORECASE
    )
    if match:
        host = match.group(1) or ""
        path = match.group(2) or ""
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        return f"{host}{path or ''}"

    for token in re.findall(r"[\w.-]+", normalized.lower()):
        site_key = normalize_site_name(token)
        default_url = get_default_site_url(site_key)
        if not default_url:
            continue
        return default_url

    return ""


def _build_auth_guidance(
    site_key: str,
    snapshot_json: str,
    probe: dict[str, Any] | None = None,
) -> str:
    """Build a focused auth-specific instruction when the page is clearly a login flow."""
    try:
        snapshot = json.loads(snapshot_json)
    except Exception:
        return ""

    if should_suppress_login_navigation_guidance(snapshot, probe):
        return ""

    if not looks_like_login_surface(snapshot):
        return ""

    site_hint = f" ({site_key})" if site_key else ""
    return (
        f"This page likely needs sign-in or account steps{site_hint}. "
        "Read fields from inspect_page; use type_text with values from the goal/context; "
        "if a value or next click is unclear, use ask_user."
    )


def _compose_runtime_guidance(*parts: str) -> str:
    """Join optional runtime guidance snippets into one compact message."""
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n".join(cleaned)


_ASK_USER_VAGUE_TOKENS: tuple[str, ...] = (
    "what should i do",
    "what should i click",
    "what do i click",
    "which button",
    "what next",
    "what now",
    "what should happen next",
    "что делать",
    "что нажать",
    "что дальше",
    "какую кнопку",
    "куда нажать",
    "как дальше",
    "что мне сделать",
)

_ASK_USER_CONCRETE_VALUE_TOKENS: tuple[str, ...] = (
    "password",
    "passcode",
    "otp",
    "sms",
    "code",
    "verification",
    "login",
    "username",
    "email",
    "e-mail",
    "phone",
    "address",
    "cvv",
    "card",
    "парол",
    "код",
    "смс",
    "логин",
    "почт",
    "телефон",
    "адрес",
    "карт",
)

_ASK_USER_CONFIRMATION_TOKENS: tuple[str, ...] = (
    "confirm",
    "approve",
    "finalize",
    "submit",
    "place order",
    "purchase",
    "подтверд",
    "оформ",
    "отправ",
    "оплат",
)


@dataclass(frozen=True)
class AskUserGuardDecision:
    """Whether a model-issued ask_user call is acceptable right now."""

    allowed: bool
    reason_code: str = ""
    tool_message: str = ""
    human_followup: str = ""


def _question_looks_vague_for_ask_user(question: str) -> bool:
    low = " ".join((question or "").strip().lower().split())
    if not low:
        return True
    if any(token in low for token in _ASK_USER_VAGUE_TOKENS):
        return True
    word_count = len(low.split())
    if word_count <= 3 and not any(tok in low for tok in _ASK_USER_CONCRETE_VALUE_TOKENS):
        return True
    return False


def _question_requests_concrete_user_value(question: str, blocker_type: str) -> bool:
    low = " ".join((question or "").strip().lower().split())
    if not low:
        return False
    if blocker_type == "confirmation":
        return any(token in low for token in _ASK_USER_CONFIRMATION_TOKENS)
    return any(token in low for token in _ASK_USER_CONCRETE_VALUE_TOKENS)


def _snapshot_allows_user_blocker(snapshot_json: str, blocker_type: str) -> bool:
    try:
        snapshot = json.loads(snapshot_json) if snapshot_json else {}
    except Exception:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    if blocker_type == "2fa":
        return snapshot_allows_ask_user_2fa(snapshot)
    return True


def _guard_ask_user_request(
    *,
    question: str,
    blocker_type: str,
    step_number: int,
    tool_actions_taken: int,
    page_reads_taken: int,
    snapshot_json: str,
    page_state: BrowserPageState | None = None,
) -> AskUserGuardDecision:
    """Reject vague or premature user escalation and steer the model back to the live page."""
    normalized_question = (question or "").strip()
    if _question_looks_vague_for_ask_user(normalized_question):
        return AskUserGuardDecision(
            allowed=False,
            reason_code="vague_question",
            tool_message=(
                "ASK_USER_REJECTED: the question is vague or asks the user to interpret the visible page."
            ),
            human_followup=(
                "Your ask_user call was rejected because it was vague or delegated visible UI interpretation "
                "to the user. Inspect the live page again and identify the specific field, button, or missing "
                "value yourself. Ask the user only for a concrete missing value or confirmation."
            ),
        )
    if blocker_type == "2fa" and not _snapshot_allows_user_blocker(snapshot_json, blocker_type):
        return AskUserGuardDecision(
            allowed=False,
            reason_code="2fa_not_visible",
            tool_message=(
                "ASK_USER_REJECTED: blocker_type=2fa is not supported by the current DOM evidence."
            ),
            human_followup=(
                "Your ask_user call was rejected because the current page does not clearly show an OTP/2FA input. "
                "Do not ask for a verification code yet. Re-read the page and continue navigating until the code "
                "step is visibly present."
            ),
        )
    if page_state is not None:
        if (
            blocker_type in {"other", "missing_info"}
            and page_state.can_proceed_without_user
            and page_state.candidate_actions
            and _question_requests_concrete_user_value(normalized_question, blocker_type) is False
        ):
            return AskUserGuardDecision(
                allowed=False,
                reason_code="page_state_action_available",
                tool_message=(
                    "ASK_USER_REJECTED: the page state still shows available actions the agent can try first."
                ),
                human_followup=(
                    "Your ask_user call was rejected because the derived page state still shows actionable "
                    "controls on the live page. Continue using the available buttons/fields before escalating "
                    "to the user."
                ),
            )
    if not _question_requests_concrete_user_value(normalized_question, blocker_type):
        return AskUserGuardDecision(
            allowed=False,
            reason_code="missing_concrete_value",
            tool_message=(
                "ASK_USER_REJECTED: the request does not name a concrete missing value or confirmation."
            ),
            human_followup=(
                "Your ask_user call was rejected because it did not request a concrete missing value or explicit "
                "confirmation. Do not ask the user to figure out the page for you. Continue with tools until you "
                "can name the exact missing input."
            ),
        )
    if tool_actions_taken == 0 and page_reads_taken < 2 and step_number <= 2:
        return AskUserGuardDecision(
            allowed=False,
            reason_code="premature_escalation",
            tool_message=(
                "ASK_USER_REJECTED: escalation is premature; gather more evidence from the live page first."
            ),
            human_followup=(
                "Your ask_user call was rejected because you escalated too early. First use the live page: "
                "inspect_page/read_page and, if useful, scroll, wait_for_page, or inspect again. Only after "
                "exhausting the visible evidence may you ask for the concrete missing value."
            ),
        )
    return AskUserGuardDecision(allowed=True)


def _infer_blocker_class(
    *,
    blocker_type: str,
    stop_reason: str = "",
    page_state: BrowserPageState | None = None,
    outcome: str = "",
) -> str:
    """Map concrete blocker hints to a stable coarse blocker class."""
    bt = (blocker_type or "").strip().lower()
    sr = (stop_reason or "").strip().lower()
    oc = (outcome or "").strip().lower()

    if bt == "captcha" or sr == "user_must_complete_captcha":
        return BrowserBlockerClass.POLICY_FORBIDDEN_OR_IMPOSSIBLE.value
    if bt == "confirmation":
        return BrowserBlockerClass.MANUAL_CONFIRMATION_NEEDED.value
    if bt in {"login", "2fa", "missing_info"}:
        return BrowserBlockerClass.USER_DATA_NEEDED.value
    if oc in {"model_stalled_no_tools", "step_limit_reached"}:
        return BrowserBlockerClass.MACHINE_RESOLVABLE.value
    if sr in {"user_must_assist_after_model_stall", "step_limit_reached"}:
        return BrowserBlockerClass.MACHINE_RESOLVABLE.value
    if bt == "other":
        if page_state is not None and page_state.can_proceed_without_user:
            return BrowserBlockerClass.MACHINE_RESOLVABLE.value
        return BrowserBlockerClass.USER_DATA_NEEDED.value
    return BrowserBlockerClass.USER_DATA_NEEDED.value


def _with_blocker_class(
    facts: dict[str, Any],
    *,
    blocker_type: str,
    page_state: BrowserPageState | None = None,
) -> dict[str, Any]:
    """Ensure structured browser facts carry a stable coarse blocker class."""
    out = dict(facts)
    out.setdefault("blocker_type", blocker_type)
    out.setdefault(
        "blocker_class",
        _infer_blocker_class(
            blocker_type=blocker_type,
            stop_reason=str(out.get("stop_reason", "") or ""),
            page_state=page_state,
            outcome=str(out.get("outcome", "") or ""),
        ),
    )
    return out


def _screen_kind_progress_hint(page_state: BrowserPageState | None) -> str:
    if page_state is None:
        return "Проверяю, что можно сделать дальше на странице."
    if page_state.screen_kind == "login":
        return "Проверяю, как лучше продолжить вход на сайте."
    if page_state.screen_kind == "code_verification":
        return "Проверяю шаг подтверждения и что нужно дальше."
    if page_state.screen_kind == "selection_list":
        return "Сравниваю найденные варианты и ищу лучший следующий шаг."
    if page_state.screen_kind == "form":
        return "Проверяю форму и каких данных ещё не хватает."
    if page_state.screen_kind == "challenge":
        return "Проверяю, как устроена проверка на странице."
    return "Проверяю, что можно сделать дальше на странице."


def _tool_progress_message(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    page_state: BrowserPageState | None = None,
) -> str:
    args = args or {}
    if tool_name == "inspect_page":
        return "Изучаю текущую страницу и доступные элементы."
    if tool_name == "read_page":
        return "Читаю содержимое страницы, чтобы точнее понять следующий шаг."
    if tool_name == "open_browser":
        return "Открываю браузер и подготавливаю страницу."
    if tool_name == "goto_url":
        return "Перехожу на нужную страницу."
    if tool_name == "click":
        if page_state is not None and page_state.screen_kind == "selection_list":
            return "Пробую открыть один из найденных вариантов."
        return "Пробую нужный элемент на странице."
    if tool_name == "type_text":
        return "Заполняю поля на странице."
    if tool_name == "press_key":
        return "Пробую продолжить действие с клавиатуры."
    if tool_name == "scroll":
        direction = str(args.get("direction", "") or "").lower()
        if direction == "up":
            return "Просматриваю страницу выше."
        return "Просматриваю страницу ниже, ищу нужные элементы."
    if tool_name == "wait_for_page":
        return "Жду, пока страница обновится или догрузится."
    if tool_name == "ask_user":
        return "Нужны уточнение или данные от тебя."
    if tool_name == "finish_task":
        return "Завершаю задачу и собираю результат."
    return _screen_kind_progress_hint(page_state)


def _model_progress_message(
    *,
    tool_calls: list[dict[str, Any]],
    page_state: BrowserPageState | None = None,
) -> str:
    if tool_calls:
        first = tool_calls[0]
        return _tool_progress_message(
            str(first.get("name", "") or ""),
            first.get("args", {}) or {},
            page_state=page_state,
        )
    if page_state is not None and page_state.screen_kind == "selection_list":
        return "Ищу другой способ открыть нужный вариант на этой странице."
    if page_state is not None and page_state.screen_kind == "form":
        return "Проверяю, можно ли продолжить без дополнительных данных."
    return "Пробую найти следующий рабочий шаг на текущей странице."


async def _emit_progress(progress_callback: ProgressCallback | None, text: str) -> None:
    """Send progress update to the caller if a callback is configured."""
    if progress_callback is None:
        return
    try:
        await progress_callback(text)
    except Exception as exc:
        logger.warning("browser_progress_callback_failed", error=str(exc))


async def _await_browser_llm_step(llm, messages, user_id: int, step: int):
    """Await one browser LLM step with heartbeat logs while the provider is pending."""
    started_at = time.monotonic()
    task = asyncio.create_task(ainvoke_with_retry(llm, messages))
    heartbeat_seconds = settings.BROWSER_LLM_STEP_HEARTBEAT_SECONDS
    hard_timeout_seconds = settings.BROWSER_LLM_STEP_TIMEOUT_SECONDS
    heartbeat_count = 0

    logger.info(
        "browser_llm_step_started",
        user_id=user_id,
        step=step,
        timeout_seconds=hard_timeout_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )

    while True:
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat_seconds)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started_at
            heartbeat_count += 1
            logger.warning(
                "browser_llm_step_waiting",
                user_id=user_id,
                step=step,
                elapsed_seconds=round(elapsed, 2),
                heartbeat_count=heartbeat_count,
            )
            if elapsed >= hard_timeout_seconds:
                task.cancel()
                logger.error(
                    "browser_llm_step_timeout",
                    user_id=user_id,
                    step=step,
                    elapsed_seconds=round(elapsed, 2),
                    timeout_seconds=hard_timeout_seconds,
                )
                raise TimeoutError(
                    f"Browser LLM step timed out after {hard_timeout_seconds} seconds"
                )
            continue
        except Exception:
            elapsed = time.monotonic() - started_at
            logger.info(
                "browser_llm_step_finished",
                user_id=user_id,
                step=step,
                elapsed_seconds=round(elapsed, 2),
                outcome="error",
            )
            raise
        else:
            elapsed = time.monotonic() - started_at
            logger.info(
                "browser_llm_step_finished",
                user_id=user_id,
                step=step,
                elapsed_seconds=round(elapsed, 2),
                outcome="success",
            )
            return result


async def _log_session_probe(
    session: BrowserRuntimeSession,
    user_id: int,
    probe_stage: str,
    **extra: Any,
) -> dict[str, Any] | None:
    """Log a lightweight diagnostic snapshot of the current browser session."""
    try:
        probe = await session.session_probe()
    except Exception as exc:
        logger.warning(
            "browser_session_probe_failed",
            user_id=user_id,
            probe_stage=probe_stage,
            error=str(exc),
            **extra,
        )
        return None

    logger.info(
        "browser_session_probe",
        user_id=user_id,
        probe_stage=probe_stage,
        title=probe.get("title", ""),
        url=probe.get("url", ""),
        cookie_count=probe.get("cookie_count", 0),
        cookie_domains=probe.get("cookie_domains", []),
        auth_signals=probe.get("auth_signals", {}),
        body_text_preview=preview_for_log(probe.get("body_text_preview", ""), limit=400),
        interactive_texts=probe.get("interactive_texts", []),
        **extra,
    )
    return probe


@dataclass
class BrowserFlowStateMachine:
    """State machine that orchestrates browser startup, auth assistance, and recovery."""

    session: BrowserRuntimeSession
    user_id: int
    goal: str
    trace: list[str]
    progress_callback: ProgressCallback | None
    phase: BrowserFlowPhase = BrowserFlowPhase.INIT
    canonical_phase: CanonicalBrowserPhase = CanonicalBrowserPhase.INIT
    last_snapshot: str = ""
    current_url: str = ""
    site_key: str = ""
    empty_step_recovery_count: int = 0
    auth_guidance: str = ""
    visual_guidance: str = ""
    page_state: BrowserPageState | None = None
    page_state_guidance: str = ""
    blocking_message: str = ""
    blocking_type: str = "other"
    blocking_debug_note: str = ""
    blocking_auth_facts: dict[str, Any] = field(default_factory=dict)
    sub_intent: BrowserSubIntent = BrowserSubIntent.APPLY_TO_TARGETS
    last_visual_guidance_url: str = ""
    page_context_hint: str = ""

    async def transition(self, phase: BrowserFlowPhase, **extra: Any) -> None:
        """Move to the next orchestration phase and log it explicitly."""
        previous = self.phase
        self.phase = phase
        self.canonical_phase = canonical_browser_phase(phase.value)
        logger.info(
            "browser_flow_transition",
            user_id=self.user_id,
            previous_phase=previous.value,
            phase=phase.value,
            canonical_phase=self.canonical_phase.value,
            **extra,
        )

    async def _refresh_page_context(self) -> None:
        """Refresh current URL and normalized site key from the live browser page."""
        if self.session.page is None:
            self.current_url = ""
            self.site_key = ""
            return
        self.current_url = await self.session.current_url()
        self.site_key = infer_site_key_from_url(self.current_url)

    async def bootstrap(self, *, resume_url: str = "") -> str:
        """Open or restore the browser and prepare the first usable live snapshot."""
        if resume_url.strip():
            await self.transition(BrowserFlowPhase.RESTORING_SESSION, resume_url=resume_url)
            await _emit_progress(self.progress_callback, "Восстанавливаю браузерную сессию.")
            restored_page = await self.session.open_browser(resume_url)
            await self._refresh_page_context()
            logger.info(
                "browser_resume_restored",
                user_id=self.user_id,
                resume_url=resume_url,
                page_preview=preview_for_log(restored_page, limit=800),
            )
            await _log_session_probe(
                self.session,
                self.user_id,
                "after_resume_restore",
                resume_url=resume_url,
            )
        else:
            start_url = _infer_start_url_from_goal(self.goal)
            await self.transition(BrowserFlowPhase.STARTING_BROWSER, start_url=start_url)
            await _emit_progress(
                self.progress_callback,
                "Открываю браузер и стартовую страницу.",
            )
            logger.info(
                "browser_bootstrap_started",
                user_id=self.user_id,
                goal=preview_for_log(self.goal, limit=400),
                start_url=start_url,
            )
            open_result = await self.session.open_browser(start_url)
            self.trace.append(
                "open_browser: "
                + _shorten(
                    json.dumps(
                        {"start_url": start_url},
                        ensure_ascii=False,
                    )
                )
            )
            await self._refresh_page_context()
            await _log_session_probe(
                self.session,
                self.user_id,
                "after_first_step_bootstrap_open",
                start_url=start_url,
            )
            logger.info(
                "browser_bootstrap_opened",
                user_id=self.user_id,
                open_result_preview=preview_for_log(open_result, limit=600),
            )

        await self.transition(
            BrowserFlowPhase.DETECTING_AUTH,
            current_url=self.current_url,
            site=self.site_key,
        )
        await self._refresh_page_context()

        self.trace.append("inspect_page: {}")
        if not self.blocking_message:
            await _rebuild_auth_visual_for_flow(self, recovery_attempt=0)
            snapshot = self.last_snapshot
        else:
            snapshot = await self.session.inspect_page()
            self.last_snapshot = snapshot
        await self.transition(
            BrowserFlowPhase.READY_FOR_MODEL,
            current_url=self.current_url,
            site=self.site_key,
            auto_login=False,
        )
        if not resume_url.strip():
            logger.info(
                "browser_bootstrap_completed",
                user_id=self.user_id,
                snapshot_preview=preview_for_log(snapshot, limit=1000),
            )
        await _emit_progress(
            self.progress_callback, "Браузер открыт, изучаю текущую страницу."
        )
        return snapshot

    async def bootstrap_live_continuation(self) -> str:
        """Continue after ``ask_user`` without closing Playwright: same tab, no ``goto``."""
        await self.session.ensure_started()
        await self._refresh_page_context()
        await self.transition(
            BrowserFlowPhase.RESTORING_SESSION,
            reason="parked_live_tab",
            current_url=self.current_url,
        )
        await _emit_progress(
            self.progress_callback,
            "Продолжаю работу в той же вкладке.",
        )
        await self.transition(
            BrowserFlowPhase.DETECTING_AUTH,
            current_url=self.current_url,
            site=self.site_key,
        )
        self.trace.append("inspect_page(live_continuation): {}")
        await _rebuild_auth_visual_for_flow(self, recovery_attempt=0)
        snapshot = self.last_snapshot
        await self.transition(
            BrowserFlowPhase.READY_FOR_MODEL,
            current_url=self.current_url,
            site=self.site_key,
            auto_login=False,
        )
        logger.info(
            "browser_bootstrap_live_continuation",
            user_id=self.user_id,
            current_url=self.current_url,
            snapshot_preview=preview_for_log(snapshot, limit=1000),
        )
        await _emit_progress(
            self.progress_callback, "Продолжаю работу с текущей страницы."
        )
        return snapshot


async def _rebuild_auth_visual_for_flow(flow: BrowserFlowStateMachine, *, recovery_attempt: int) -> None:
    """Re-run auth heuristics + optional vision after navigation or URL change."""
    await flow._refresh_page_context()
    snapshot = await flow.session.inspect_page()
    flow.last_snapshot = snapshot
    probe = await _log_session_probe(
        flow.session,
        flow.user_id,
        "auth_visual_rebuild",
        recovery_attempt=recovery_attempt,
        current_url=flow.current_url,
    )
    flow.page_state = derive_browser_page_state(
        snapshot_json=snapshot,
        probe=probe,
        site_key=flow.site_key,
    )
    flow.page_state_guidance = summarize_browser_page_state(flow.page_state)
    flow.auth_guidance = _build_auth_guidance(flow.site_key, snapshot, probe)
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
        recovery_attempt=recovery_attempt,
        goal_text_for_vision=vision_goal,
        page_state=flow.page_state,
    )
    flow.last_visual_guidance_url = flow.current_url


def _bootstrap_auth_facts_or_fallback(
    flow: BrowserFlowStateMachine, blocker_type: str
) -> dict[str, Any]:
    """Merge saved-login auth facts with blocker_type; else emit a minimal deterministic fallback."""
    if flow.blocking_auth_facts:
        out = dict(flow.blocking_auth_facts)
        out.setdefault("blocker_type", blocker_type)
        return _with_blocker_class(out, blocker_type=blocker_type, page_state=flow.page_state)
    return _with_blocker_class(
        {
        "facts_version": 1,
        "source": "browser_agent",
        "outcome": "bootstrap_login_blocked",
        "blocker_type": blocker_type,
        },
        blocker_type=blocker_type,
        page_state=flow.page_state,
    )


async def _try_viewport_screenshot_b64(session: BrowserRuntimeSession) -> str:
    """Best-effort viewport PNG as raw base64 (for Telegram / debugging)."""
    try:
        data_url = await session.capture_screenshot_data_url()
        return _data_url_to_base64_png(data_url)
    except Exception as exc:
        logger.warning("browser_viewport_screenshot_failed", error=str(exc))
        return ""


async def _finalize_browser_exit_needs_user(
    *,
    flow: BrowserFlowStateMachine,
    session: BrowserRuntimeSession,
    user_id: int,
    exit_kind: str,
    stall_reason_code: str = "",
    generic_user_message: str = "",
    policy_debug_note: str = "",
) -> tuple[str, str, dict[str, Any], str]:
    """Build user-facing message, blocker_type, auth_facts, debug_note for stall/step-limit exits.

    ``exit_kind`` is ``stalled_no_tools`` or ``step_limit``. When the live page looks like a CAPTCHA
    barrier, refresh vision with the captcha-oriented prompt and ask the user to continue manually.
    """
    try:
        fresh = await session.inspect_page()
        if fresh.strip():
            flow.last_snapshot = fresh
    except Exception:
        pass

    snap = (flow.last_snapshot or "").strip()
    note = (policy_debug_note or "").strip()

    if dom_evidence_suggests_captcha(snap):
        vision_goal = (flow.page_context_hint or "").strip() or preview_for_log(flow.goal, limit=600)
        captcha_visual = await maybe_build_visual_guidance(
            session=session,
            user_id=user_id,
            goal=flow.goal,
            site_key=flow.site_key,
            snapshot_json=snap,
            auth_guidance="",
            recovery_attempt=max(flow.empty_step_recovery_count, 1),
            goal_text_for_vision=vision_goal,
            page_state=flow.page_state,
        )
        lines = [
            "Страница показывает капчу или проверку «я не робот». Автоматически это обойти нельзя.",
            "Пройди проверку в браузере (или по присланному снимку), затем напиши «готово» или «продолжай».",
        ]
        if captcha_visual:
            lines.append("")
            lines.append(captcha_visual)
        msg = "\n".join(lines)
        facts: dict[str, Any] = {
            "facts_version": 1,
            "source": "browser_agent",
            "outcome": "blocked_user_interaction_required",
            "blocker_type": "captcha",
            "stop_reason": "user_must_complete_captcha",
        }
        if exit_kind == "stalled_no_tools":
            facts["stall_reason_code"] = stall_reason_code
        else:
            facts["exit_kind"] = "step_limit"
        if note:
            facts["policy_debug_note"] = note
        logger.info(
            "browser_exit_escalated_captcha",
            user_id=user_id,
            exit_kind=exit_kind,
            stall_reason_code=stall_reason_code or None,
        )
        return (
            msg,
            "captcha",
            _with_blocker_class(facts, blocker_type="captcha", page_state=flow.page_state),
            note,
        )

    if exit_kind == "stalled_no_tools":
        msg = user_message_for_stalled_no_tools(stall_reason_code=stall_reason_code)
        facts = auth_facts_model_stalled_no_tools(
            "missing_info", stall_reason_code=stall_reason_code
        )
        facts["stop_reason"] = "user_must_assist_after_model_stall"
        if note:
            facts["policy_debug_note"] = note
        return (
            msg,
            "missing_info",
            _with_blocker_class(facts, blocker_type="missing_info", page_state=flow.page_state),
            note,
        )

    msg = (generic_user_message or "").strip() or (
        "Достигнут лимит шагов. Нужна следующая инструкция или ещё один запуск."
    )
    facts = {
        "facts_version": 1,
        "source": "browser_agent",
        "outcome": "step_limit_reached",
        "blocker_type": "missing_info",
        "stop_reason": "step_limit_reached",
    }
    if note:
        facts["policy_debug_note"] = note
    return (
        msg,
        "missing_info",
        _with_blocker_class(facts, blocker_type="missing_info", page_state=flow.page_state),
        note,
    )


def _merge_barrier_facts(
    auth_facts: dict[str, Any],
    *,
    barrier_kind: str,
    final_url: str,
) -> dict[str, Any]:
    """Attach canonical barrier hints for Phase E structured reporting."""
    out = dict(auth_facts)
    if barrier_kind:
        out.setdefault("barrier_kind", barrier_kind)
    if final_url:
        out.setdefault("page_url", final_url)
    return out


def _infer_barrier_kind(needs_user_input: bool, blocker_type: str) -> str:
    if not needs_user_input:
        return ""
    return (blocker_type or "other").strip() or "other"


def _infer_resume_strategy(needs_user_input: bool, auth_facts: dict[str, Any]) -> str:
    if not needs_user_input:
        return "none"
    if auth_facts.get("outcome") == "finish_task_stopped_checkpoint":
        return "continue_after_checkpoint"
    return "await_user_message"


def _data_url_to_base64_png(data_url: str) -> str:
    """Strip ``data:image/...;base64,`` prefix; return empty if invalid."""
    if not data_url or "base64," not in data_url:
        return ""
    return data_url.split("base64,", 1)[1].strip()


def _make_browser_run_result(
    *,
    goal: str,
    goal_display: str = "",
    trace: list[str],
    final_message: str,
    final_url: str,
    needs_user_input: bool,
    blocker_type: str,
    auth_facts: dict[str, Any],
    debug_note: str = "",
    sub_intent: str = "",
    screenshot_png_base64: str = "",
) -> BrowserRunResult:
    """Build Telegram report plus Phase E structured browser fields."""
    bk = _infer_barrier_kind(needs_user_input, blocker_type)
    facts = dict(auth_facts)
    if needs_user_input:
        facts = _with_blocker_class(facts, blocker_type=blocker_type)
    rs = _infer_resume_strategy(needs_user_input, facts)
    sr = ""
    if needs_user_input:
        sr = str(facts.get("stop_reason", "") or "").strip()
    next_action = (final_message or "").strip() if needs_user_input else ""
    return BrowserRunResult(
        telegram_report=format_run_summary_telegram(
            goal_display=goal_display,
            goal_internal=goal,
            trace=trace,
            final_message=final_message,
            final_url=final_url,
            needs_user_input=needs_user_input,
            blocker_type=blocker_type,
            debug_note=debug_note,
            auth_facts=facts,
        ),
        trace=trace,
        final_url=final_url,
        needs_user_input=needs_user_input,
        blocker_type=blocker_type,
        debug_note=debug_note,
        auth_facts=facts,
        barrier_kind=bk,
        barrier_facts=_merge_barrier_facts(facts, barrier_kind=bk, final_url=final_url),
        next_user_action=next_action,
        resume_strategy=rs,
        sub_intent=sub_intent,
        screenshot_png_base64=screenshot_png_base64 or "",
        stop_reason=sr,
    )


def _runtime_success_summary(
    *,
    page_state: BrowserPageState | None,
    reason_code: str,
    confidence: float,
) -> str:
    screen = page_state.screen_kind if page_state is not None else "unknown"
    heading = (page_state.dominant_heading if page_state is not None else "") or "result page"
    return (
        f"Задача завершена автоматически: страница перешла в терминальное состояние "
        f"({screen}, {heading}; detector={reason_code}, confidence={confidence:.2f})."
    )


def _runtime_success_auth_facts(
    *,
    tool_name: str,
    reason_code: str,
    confidence: float,
    final_url: str,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "facts_version": 1,
        "source": "runtime_success_detector",
        "outcome": "runtime_success_detected",
        "success_detected_by_runtime": True,
        "tool_name": tool_name,
        "reason_code": reason_code,
        "confidence": round(confidence, 2),
    }
    if final_url:
        facts["page_url"] = final_url
    return facts


def _runtime_verification_followup(*, tool_name: str, confidence: float, reason_code: str) -> str:
    return (
        f"The last {tool_name} may already have completed the task (detector={reason_code}, "
        f"confidence={confidence:.2f}). Inspect the live page once more before asking the user or "
        "declaring a stall; if the action surface is gone, finish the task."
    )


def _should_run_runtime_detector(tool_name: str, result: Any) -> bool:
    return tool_name in _MEANINGFUL_PAGE_CHANGING_TOOLS and not (
        isinstance(result, str) and result.startswith("ERROR:")
    )


def _system_prompt(context_text: str, sub_intent: BrowserSubIntent) -> str:
    """Build the browser agent instruction prompt."""
    context_block = f"\nUser context:\n{context_text}\n" if context_text else ""
    mode_block = f"\nSub-intent policy:\n{sub_intent_execution_brief(sub_intent)}\n"
    return (
        "You are an autonomous browser agent controlling a real Chromium window.\n"
        "Primary operating loop:\n"
        "1. Observe the live page with inspect_page.\n"
        "2. Infer what screen this is, which actions are available, and which required values are still missing.\n"
        "3. Use the available tools to continue autonomously: read_page, scroll, wait_for_page, inspect_page again, "
        "click, type_text, press_key, goto_url when truly needed.\n"
        "4. Ask the user only as a last resort when a concrete required value or confirmation is missing after you "
        "have exhausted the visible page evidence and provided context.\n"
        "5. Finish only when the task is actually done or you intentionally stop at a safe checkpoint.\n"
        "Never use ask_user just because the page is unfamiliar.\n"
        "Do not ask the user to identify buttons, fields, or labels that are already visible on the page.\n"
        "If the next action is unclear, first gather more evidence from the live page instead of escalating.\n"
        "When a page looks visually ambiguous or partially unreadable, rely on the runtime's visual guidance if it is "
        "present and continue using tools before asking the user.\n"
        "If you do ask the user, make it a short specific request for the missing value or confirmation; "
        "blocker_type is a loose hint (login, captcha, missing_info, confirmation, other, 2fa) — pick what fits best.\n"
        "Do not assume site-specific flows (SMS vs captcha vs password) from memory; read the current page each time.\n"
        "Never click inspect_page rows where fillable=true: use type_text for inputs and textareas; "
        "click is for buttons, links, and non-fillable controls.\n"
        "The human task text may contain outdated assumptions from earlier turns — trust the latest "
        "snapshot and visible labels over that text when they disagree.\n"
        "Do not put a password or verification string into a field that the snapshot shows as phone/email/login "
        "unless the label clearly matches.\n"
        "Use finish_task when the task is done or you stop at a safe checkpoint, not when you are merely uncertain.\n"
        "Keep moving; use tools instead of long narration.\n"
        f"{mode_block}"
        f"{context_block}"
    )


async def _maybe_complete_via_runtime_detector(
    *,
    flow: BrowserFlowStateMachine,
    session: BrowserRuntimeSession,
    goal: str,
    goal_display: str,
    trace: list[str],
    step_number: int,
    tool_name: str,
    before_snapshot: str,
    before_page_state: BrowserPageState | None,
    before_url: str,
    step_outcome: StepOutcome | None = None,
) -> BrowserRunResult | None:
    """Try to auto-complete the run when runtime can prove terminal page-state transition."""
    after_url = ""
    try:
        after_url = await session.current_url()
    except Exception:
        after_url = flow.current_url
    inference = infer_terminal_outcome(
        sub_intent=flow.sub_intent.value,
        tool_name=tool_name,
        before_snapshot=before_snapshot,
        after_snapshot=flow.last_snapshot,
        before_page_state=before_page_state,
        after_page_state=flow.page_state,
        before_url=before_url,
        after_url=after_url,
    )
    if step_outcome is not None:
        logger.info(
            "browser_runtime_step_outcome_considered",
            user_id=flow.user_id,
            step=step_number,
            tool_name=tool_name,
            outcome_status=step_outcome.status,
            outcome_confidence=step_outcome.confidence,
        )
        if step_outcome.status == "success" and step_outcome.confidence >= _RUNTIME_SUCCESS_HIGH_CONFIDENCE:
            inference = type(inference)(
                completed=True,
                confidence=max(inference.confidence, step_outcome.confidence),
                reason_code="step_verifier_confirmed_success",
                explanation=(
                    "Step verifier observed a strong post-action transition consistent with success. "
                    + step_outcome.summary
                ),
            )
        elif (
            step_outcome.status == "probable_success"
            and step_outcome.confidence >= _RUNTIME_SUCCESS_HIGH_CONFIDENCE
            and inference.confidence < _RUNTIME_SUCCESS_HIGH_CONFIDENCE
        ):
            inference = type(inference)(
                completed=True,
                confidence=step_outcome.confidence,
                reason_code="step_verifier_probable_success",
                explanation=(
                    "Step verifier observed a likely successful transition strong enough to treat as "
                    "runtime completion. " + step_outcome.summary
                ),
            )
    logger.info(
        "browser_runtime_terminal_outcome_inferred",
        user_id=flow.user_id,
        step=step_number,
        tool_name=tool_name,
        completed=inference.completed,
        confidence=inference.confidence,
        reason_code=inference.reason_code,
    )
    if not inference.completed or inference.confidence < _RUNTIME_SUCCESS_HIGH_CONFIDENCE:
        return None

    final_message = _runtime_success_summary(
        page_state=flow.page_state,
        reason_code=inference.reason_code,
        confidence=inference.confidence,
    )
    trace.append(
        "runtime_success_detected: "
        + _shorten(
            json.dumps(
                {
                    "tool_name": tool_name,
                    "reason_code": inference.reason_code,
                    "confidence": round(inference.confidence, 2),
                },
                ensure_ascii=False,
            )
        )
    )
    await flow.transition(
        BrowserFlowPhase.FINISHED,
        step=step_number,
        finish_status="completed",
        current_url=after_url,
        runtime_detector_reason=inference.reason_code,
    )
    return _make_browser_run_result(
        goal=goal,
        goal_display=goal_display,
        trace=trace,
        final_message=final_message,
        final_url=after_url,
        needs_user_input=False,
        blocker_type="other",
        auth_facts=_runtime_success_auth_facts(
            tool_name=tool_name,
            reason_code=inference.reason_code,
            confidence=inference.confidence,
            final_url=after_url,
        ),
        sub_intent=flow.sub_intent.value,
    )


def _runtime_detector_followup_message(
    *,
    flow: BrowserFlowStateMachine,
    tool_name: str,
    before_snapshot: str,
    before_page_state: BrowserPageState | None,
    before_url: str,
    verification_already_requested: bool,
    step_outcome: StepOutcome | None = None,
) -> str:
    """Optional one-shot prompt nudging the model to verify a medium-confidence success state."""
    if verification_already_requested:
        return ""
    if step_outcome is not None:
        if step_outcome.status in {"success", "blocked"}:
            return ""
        if step_outcome.status in {"probable_success", "ambiguous"} and step_outcome.confidence >= 0.45:
            return (
                "Inspect the live page once more before taking another action. "
                "The last step may already have changed the page meaningfully; verify whether the target state, "
                "local action surface, or confirmation markers changed."
            )
    inference = infer_terminal_outcome(
        sub_intent=flow.sub_intent.value,
        tool_name=tool_name,
        before_snapshot=before_snapshot,
        after_snapshot=flow.last_snapshot,
        before_page_state=before_page_state,
        after_page_state=flow.page_state,
        before_url=before_url,
        after_url=flow.current_url,
    )
    if inference.completed:
        return ""
    if inference.confidence < _RUNTIME_SUCCESS_MEDIUM_CONFIDENCE:
        return ""
    return _runtime_verification_followup(
        tool_name=tool_name,
        confidence=inference.confidence,
        reason_code=inference.reason_code,
    )


def _verification_trace_line(*, status: str, confidence: float, markers: list[str]) -> str:
    payload = {
        "status": status,
        "confidence": round(confidence, 2),
        "markers": markers[:6],
    }
    return "step_verification: " + _shorten(json.dumps(payload, ensure_ascii=False))


async def run_browser_task_orchestration(
    *,
    session: BrowserRuntimeSession,
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    reuse_parked_live_tab: bool = False,
    build_tools: BuildToolsFn,
    initial_sub_intent: str | None = None,
    goal_display: str = "",
    page_context_hint: str = "",
) -> BrowserRunResult:
    """Main browser loop: bootstrap → LLM steps → tools until finish, block, stall, or step limit.

    Phase transitions and structured exits live here; tool definitions are injected via ``build_tools``.
    """
    resolved_goal_display = (goal_display or "").strip()
    hint = (page_context_hint or "").strip() or resolved_goal_display
    resolved_sub_intent = _resolve_browser_sub_intent(goal, initial_sub_intent)
    tools = build_tools(session, sub_intent=resolved_sub_intent)
    llm = create_llm(model=settings.BROWSER_MODEL or None, temperature=0.0).bind_tools(
        list(tools.values())
    )
    trace: list[str] = []
    final_message = ""
    needs_user_input = False
    blocker_type = "other"
    flow = BrowserFlowStateMachine(
        session=session,
        user_id=user_id,
        goal=goal,
        trace=trace,
        progress_callback=progress_callback,
        sub_intent=resolved_sub_intent,
        page_context_hint=hint,
    )
    debug_note = ""
    loop_exit_auth_facts: dict[str, Any] | None = None
    loop_exit_screenshot_b64 = ""
    tool_actions_taken = 0
    runtime_verification_requested = False
    last_transition_for_runtime_detector: dict[str, Any] | None = None
    messages = [
        SystemMessage(content=_system_prompt(context_text, resolved_sub_intent)),
        HumanMessage(content=_sub_intent_user_task_message(goal, resolved_sub_intent)),
    ]

    logger.info(
        "browser_task_started",
        user_id=user_id,
        model=settings.BROWSER_MODEL or settings.DEFAULT_MODEL,
        goal=goal,
        context_text=preview_for_log(context_text, limit=1200),
        max_steps=settings.BROWSER_MAX_STEPS,
        headless=settings.BROWSER_HEADLESS,
        profile_dir=settings.BROWSER_PROFILE_DIR,
        resume_url=resume_url,
        reuse_parked_live_tab=reuse_parked_live_tab,
        sub_intent=flow.sub_intent.value,
    )
    if reuse_parked_live_tab:
        await _emit_progress(progress_callback, "Возвращаюсь к незавершённой задаче в браузере.")
    else:
        await _emit_progress(progress_callback, "Запускаю браузер для выполнения задачи.")

    if reuse_parked_live_tab:
        try:
            restored_page = await flow.bootstrap_live_continuation()
        except Exception as exc:
            restored_page = f"ERROR: {type(exc).__name__}: {exc}"
            logger.warning(
                "browser_live_continuation_failed",
                user_id=user_id,
                error=str(exc),
            )
        else:
            messages.append(
                HumanMessage(
                    content=(
                        "Chromium stayed open while waiting for the user. The live tab still shows "
                        "the continuation point.\n"
                        "Do NOT call goto_url/open_browser unless inspect_page proves you navigated "
                        "away — reloading often resets interactive challenges.\n"
                        "Use type_text/click/etc. from the snapshot below with the user's reply in the goal.\n"
                        f"{restored_page}"
                    )
                )
            )
    elif resume_url.strip():
        try:
            restored_page = await flow.bootstrap(resume_url=resume_url)
        except Exception as exc:
            restored_page = f"ERROR: {type(exc).__name__}: {exc}"
            logger.warning(
                "browser_resume_restore_failed",
                user_id=user_id,
                resume_url=resume_url,
                error=str(exc),
            )
        else:
            messages.append(
                HumanMessage(
                    content=(
                        "The browser session has already been restored to the current page. "
                        "Use this as the live starting point and inspect before navigating away:\n"
                        f"{restored_page}"
                    )
                )
            )
    else:
        try:
            initial_snapshot = await flow.bootstrap()
        except Exception as exc:
            initial_snapshot = ""
            logger.warning(
                "browser_initial_bootstrap_failed",
                user_id=user_id,
                error=str(exc),
            )
        else:
            messages.append(
                HumanMessage(
                    content=(
                        "The browser was started before the first planning step. "
                        "Use this live page snapshot and continue with tools:\n"
                        f"{initial_snapshot}"
                    )
                )
            )
    page_reads_taken = 1 if flow.last_snapshot else 0
    runtime_guidance = _compose_runtime_guidance(
        flow.page_state_guidance,
        flow.auth_guidance,
        flow.visual_guidance,
    )
    if runtime_guidance:
        messages.append(HumanMessage(content=runtime_guidance))
    if flow.blocking_message:
        final_url = await session.current_url()
        final_message = flow.blocking_message
        blocker_type = flow.blocking_type
        debug_note = (
            flow.blocking_debug_note
            or "Браузерный рантайм был заблокирован во время bootstrap логина."
        )
        bootstrap_facts = _bootstrap_auth_facts_or_fallback(flow, blocker_type)
        logger.info(
            "browser_task_blocked_during_bootstrap",
            user_id=user_id,
            url=final_url,
            blocker_type=blocker_type,
            question=preview_for_log(final_message, limit=1200),
            auth_facts=bootstrap_facts,
        )
        shot = await _try_viewport_screenshot_b64(session)
        return _make_browser_run_result(
            goal=goal,
            goal_display=resolved_goal_display,
            trace=trace,
            final_message=final_message,
            final_url=final_url,
            needs_user_input=True,
            blocker_type=blocker_type,
            debug_note=debug_note,
            auth_facts=bootstrap_facts,
            sub_intent=flow.sub_intent.value,
            screenshot_png_base64=shot,
        )
    for step in range(settings.BROWSER_MAX_STEPS):
        await flow.transition(BrowserFlowPhase.WAITING_FOR_MODEL, step=step + 1)
        logger.info(
            "browser_llm_step_request",
            user_id=user_id,
            model=settings.BROWSER_MODEL or settings.DEFAULT_MODEL,
            step=step + 1,
            message_count=len(messages),
            last_message_preview=preview_for_log(
                messages[-1].content if messages else "", limit=1200
            ),
        )
        try:
            ai_message = await _await_browser_llm_step(
                llm=llm,
                messages=messages,
                user_id=user_id,
                step=step + 1,
            )
        except Exception as exc:
            logger.error(
                "browser_llm_step_failed",
                user_id=user_id,
                step=step + 1,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        logger.info(
            "browser_llm_step_result",
            user_id=user_id,
            model=settings.BROWSER_MODEL or settings.DEFAULT_MODEL,
            step=step + 1,
            tool_calls=[
                {
                    "name": call.get("name"),
                    "args": call.get("args", {}),
                }
                for call in tool_calls
            ],
            content_preview=preview_for_log(getattr(ai_message, "content", ""), limit=1200),
        )
        await _emit_progress(
            progress_callback,
            _model_progress_message(tool_calls=tool_calls, page_state=flow.page_state),
        )
        if not tool_calls:
            if step == 0:
                logger.warning(
                    "browser_no_tool_calls_bootstrap",
                    user_id=user_id,
                    step=step + 1,
                    message_count=len(messages),
                )
            policy = await resolve_no_tools_after_llm_step(
                flow,
                step=step + 1,
                compose_runtime_guidance=lambda: _compose_runtime_guidance(
                    flow.page_state_guidance,
                    flow.auth_guidance,
                    flow.visual_guidance,
                ),
            )
            if policy.outcome is NoToolsOutcome.CONTINUE_LOOP and policy.human_followup_message:
                messages.append(HumanMessage(content=policy.human_followup_message))
                continue
            if policy.outcome is NoToolsOutcome.EXIT_USER_BLOCKING:
                final_url = await session.current_url()
                final_message = flow.blocking_message
                blocker_type = flow.blocking_type
                shot = await _try_viewport_screenshot_b64(session)
                return _make_browser_run_result(
                    goal=goal,
                    goal_display=resolved_goal_display,
                    trace=trace,
                    final_message=final_message,
                    final_url=final_url,
                    needs_user_input=True,
                    blocker_type=blocker_type,
                    debug_note=flow.blocking_debug_note or policy.policy_debug_note,
                    auth_facts=_bootstrap_auth_facts_or_fallback(flow, blocker_type),
                    sub_intent=flow.sub_intent.value,
                    screenshot_png_base64=shot,
                )

            await flow.transition(
                BrowserFlowPhase.BLOCKED,
                step=step + 1,
                current_url=await session.current_url()
                if session.page is not None
                else "about:blank",
            )
            logger.warning(
                "browser_no_tool_calls",
                user_id=user_id,
                step=step + 1,
                message_count=len(messages),
                policy_outcome=policy.outcome.value,
                stall_reason=policy.stall_reason_code,
                current_url=await session.current_url()
                if session.page is not None
                else "about:blank",
                session_started=session.page is not None,
            )
            trace.append(
                policy.recommended_trace_line(step + 1)
                or f"Шаг {step + 1}: модель не вызвала инструмент, завершаю."
            )
            (
                final_message,
                blocker_type,
                loop_exit_auth_facts,
                debug_note,
            ) = await _finalize_browser_exit_needs_user(
                flow=flow,
                session=session,
                user_id=user_id,
                exit_kind="stalled_no_tools",
                stall_reason_code=policy.stall_reason_code,
                policy_debug_note=policy.policy_debug_note,
            )
            needs_user_input = True
            loop_exit_screenshot_b64 = await _try_viewport_screenshot_b64(session)
            if loop_exit_screenshot_b64:
                trace.append("viewport_screenshot: модель не вызвала инструмент (снимок для отладки)")
            break

        ask_user_rejected = False
        for call in tool_calls:
            tool_name = call["name"]
            args = call.get("args", {}) or {}
            before_snapshot = flow.last_snapshot
            before_page_state = flow.page_state
            before_url = flow.current_url
            try:
                if session.page is not None:
                    before_url = await session.current_url()
            except Exception:
                pass
            logger.info(
                "browser_tool_call_started",
                user_id=user_id,
                step=step + 1,
                tool_name=tool_name,
                tool_args=args,
            )

            if tool_name == "finish_task":
                final_message = str(args.get("summary", "")).strip()
                finish_status = str(args.get("status", "completed")).strip() or "completed"
                completed = finish_status == "completed"
                trace.append(f"Финиш: {_shorten(final_message)}")
                final_url = await session.current_url()
                await flow.transition(
                    BrowserFlowPhase.FINISHED,
                    step=step + 1,
                    finish_status=finish_status,
                    current_url=final_url,
                )
                logger.info(
                    "browser_task_finished",
                    user_id=user_id,
                    step=step + 1,
                    url=final_url,
                    finish_status=finish_status,
                    final_message=preview_for_log(final_message, limit=1200),
                )
                shot_ft = ""
                if not completed:
                    shot_ft = await _try_viewport_screenshot_b64(session)
                return _make_browser_run_result(
                    goal=goal,
                    goal_display=resolved_goal_display,
                    trace=trace,
                    final_message=final_message,
                    final_url=final_url,
                    needs_user_input=not completed,
                    blocker_type=blocker_type,
                    auth_facts={
                        "facts_version": 1,
                        "source": "llm_tool_finish_task",
                        "outcome": "finish_task_completed"
                        if completed
                        else "finish_task_stopped_checkpoint",
                        "finish_status": finish_status,
                    },
                    sub_intent=flow.sub_intent.value,
                    screenshot_png_base64=shot_ft,
                )

            if tool_name == "ask_user":
                final_message = str(args.get("question", "")).strip()
                blocker_type = str(args.get("blocker_type", "other")).strip() or "other"
                decision = _guard_ask_user_request(
                    question=final_message,
                    blocker_type=blocker_type,
                    step_number=step + 1,
                    tool_actions_taken=tool_actions_taken,
                    page_reads_taken=page_reads_taken,
                    snapshot_json=flow.last_snapshot,
                    page_state=flow.page_state,
                )
                if not decision.allowed:
                    trace.append(
                        "ask_user_rejected: "
                        + _shorten(
                            json.dumps(
                                {
                                    "reason": decision.reason_code,
                                    "question": final_message,
                                    "blocker_type": blocker_type,
                                },
                                ensure_ascii=False,
                            )
                        )
                    )
                    logger.info(
                        "browser_ask_user_rejected",
                        user_id=user_id,
                        step=step + 1,
                        reason_code=decision.reason_code,
                        blocker_type=blocker_type,
                        question=preview_for_log(final_message, limit=800),
                    )
                    messages.append(
                        ToolMessage(
                            content=decision.tool_message,
                            tool_call_id=call["id"],
                        )
                    )
                    messages.append(HumanMessage(content=decision.human_followup))
                    ask_user_rejected = True
                    break
                trace.append(f"Нужна помощь пользователя: {_shorten(final_message)}")
                final_url = await session.current_url()
                screenshot_b64 = await _try_viewport_screenshot_b64(session)
                if screenshot_b64:
                    trace.append("viewport_screenshot: attached for user (PNG, ask_user)")
                    logger.info(
                        "browser_ask_user_screenshot_captured",
                        user_id=user_id,
                        step=step + 1,
                        bytes_len=len(screenshot_b64),
                        current_url=final_url,
                        blocker_type=blocker_type,
                    )
                else:
                    logger.warning(
                        "browser_ask_user_screenshot_empty",
                        user_id=user_id,
                        step=step + 1,
                        current_url=final_url,
                    )
                await flow.transition(
                    BrowserFlowPhase.BLOCKED,
                    step=step + 1,
                    blocker_type=blocker_type,
                    current_url=final_url,
                )
                logger.info(
                    "browser_task_blocked",
                    user_id=user_id,
                    step=step + 1,
                    url=final_url,
                    blocker_type=blocker_type,
                    question=preview_for_log(final_message, limit=1200),
                )
                return _make_browser_run_result(
                    goal=goal,
                    goal_display=resolved_goal_display,
                    trace=trace,
                    final_message=final_message,
                    final_url=final_url,
                    needs_user_input=True,
                    blocker_type=blocker_type,
                    auth_facts=_with_blocker_class(
                        {
                            "facts_version": 1,
                            "source": "llm_tool_ask_user",
                            "outcome": "agent_requested_user_input",
                            "blocker_type": blocker_type,
                            "stop_reason": "agent_asked_user",
                        },
                        blocker_type=blocker_type,
                        page_state=flow.page_state,
                    ),
                    sub_intent=flow.sub_intent.value,
                    screenshot_png_base64=screenshot_b64,
                )

            tool = tools[tool_name]
            try:
                result = await tool.ainvoke(args)
            except Exception as exc:
                result = f"ERROR: {type(exc).__name__}: {exc}"
                logger.warning(
                    "browser_tool_failed",
                    user_id=user_id,
                    tool_name=tool_name,
                    error=str(exc),
                )
            else:
                logger.info(
                    "browser_tool_call_completed",
                    user_id=user_id,
                    step=step + 1,
                    tool_name=tool_name,
                    tool_result=preview_for_log(result, limit=100),
                )
                if tool_name in {
                    "open_browser",
                    "goto_url",
                    "click",
                    "type_text",
                    "press_key",
                    "wait_for_page",
                }:
                    await _log_session_probe(
                        session,
                        user_id,
                        "after_tool",
                        step=step + 1,
                        tool_name=tool_name,
                    )
                    last_transition_for_runtime_detector = {
                        "tool_name": tool_name,
                        "before_snapshot": before_snapshot,
                        "before_page_state": before_page_state,
                        "before_url": before_url,
                    }

            step_text = f"{tool_name}: {_shorten(json.dumps(args, ensure_ascii=False))}"
            trace.append(step_text)
            await _emit_progress(
                progress_callback,
                _tool_progress_message(tool_name, args, page_state=flow.page_state),
            )
            messages.append(
                ToolMessage(
                    content=result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            )
            if (
                _should_run_runtime_detector(tool_name, result)
            ):
                tool_actions_taken += 1
                await _rebuild_auth_visual_for_flow(flow, recovery_attempt=0)
                step_outcome: StepOutcome | None = None
                expectation = build_step_expectation(
                    step_number=step + 1,
                    user_objective=goal,
                    tool_name=tool_name,
                    tool_args=args if isinstance(args, dict) else {},
                    before_snapshot=before_snapshot,
                    before_page_state=before_page_state,
                )
                if expectation is not None:
                    step_outcome = evaluate_step_outcome(
                        expectation=expectation,
                        tool_result=result,
                        before_snapshot=before_snapshot,
                        after_snapshot=flow.last_snapshot,
                        before_page_state=before_page_state,
                        after_page_state=flow.page_state,
                        before_url=before_url,
                        after_url=flow.current_url,
                    )
                    markers = list(step_outcome.evidence.success_markers) + list(
                        step_outcome.evidence.blocker_markers or step_outcome.evidence.failure_markers
                    )
                    trace.append(
                        _verification_trace_line(
                            status=step_outcome.status,
                            confidence=step_outcome.confidence,
                            markers=markers,
                        )
                    )
                    logger.info(
                        "browser_step_outcome_evaluated",
                        user_id=user_id,
                        step=step + 1,
                        tool_name=tool_name,
                        outcome_status=step_outcome.status,
                        confidence=step_outcome.confidence,
                        success_markers=list(step_outcome.evidence.success_markers),
                        failure_markers=list(step_outcome.evidence.failure_markers),
                        blocker_markers=list(step_outcome.evidence.blocker_markers),
                    )
                    last_transition_for_runtime_detector = {
                        **(last_transition_for_runtime_detector or {}),
                        "step_outcome": step_outcome,
                    }
                runtime_result = await _maybe_complete_via_runtime_detector(
                    flow=flow,
                    session=session,
                    goal=goal,
                    goal_display=resolved_goal_display,
                    trace=trace,
                    step_number=step + 1,
                    tool_name=tool_name,
                    before_snapshot=before_snapshot,
                    before_page_state=before_page_state,
                    before_url=before_url,
                    step_outcome=step_outcome,
                )
                if runtime_result is not None:
                    return runtime_result
                rg = _compose_runtime_guidance(
                    flow.page_state_guidance,
                    flow.auth_guidance,
                    flow.visual_guidance,
                )
                if rg:
                    messages.append(
                        HumanMessage(
                            content=(
                                "The page changed (new URL or content). Prefer this refreshed "
                                "reading of the live page over earlier assumptions:\n"
                                f"{rg}"
                            )
                        )
                    )
                followup = _runtime_detector_followup_message(
                    flow=flow,
                    tool_name=tool_name,
                    before_snapshot=before_snapshot,
                    before_page_state=before_page_state,
                    before_url=before_url,
                    verification_already_requested=runtime_verification_requested,
                    step_outcome=step_outcome,
                )
                if followup:
                    messages.append(HumanMessage(content=followup))
                    runtime_verification_requested = True
            if tool_name in {"inspect_page", "read_page"} and not (
                isinstance(result, str) and result.startswith("ERROR:")
            ):
                page_reads_taken += 1
                runtime_verification_requested = False
            logger.info(
                "browser_tool_message_appended",
                user_id=user_id,
                step=step + 1,
                tool_name=tool_name,
                conversation_size=len(messages),
            )
        if ask_user_rejected:
            continue

    final_url = ""
    try:
        final_url = await session.current_url()
    except Exception:
        final_url = ""

    if last_transition_for_runtime_detector is not None:
        runtime_result = await _maybe_complete_via_runtime_detector(
            flow=flow,
            session=session,
            goal=goal,
            goal_display=resolved_goal_display,
            trace=trace,
            step_number=settings.BROWSER_MAX_STEPS,
            tool_name=str(last_transition_for_runtime_detector.get("tool_name", "") or "runtime_check"),
            before_snapshot=str(last_transition_for_runtime_detector.get("before_snapshot", "") or ""),
            before_page_state=last_transition_for_runtime_detector.get("before_page_state"),
            before_url=str(last_transition_for_runtime_detector.get("before_url", "") or ""),
            step_outcome=last_transition_for_runtime_detector.get("step_outcome"),
        )
        if runtime_result is not None:
            return runtime_result

    needs_user_input = True
    if loop_exit_auth_facts is None:
        (
            final_message,
            blocker_type,
            loop_exit_auth_facts,
            debug_note,
        ) = await _finalize_browser_exit_needs_user(
            flow=flow,
            session=session,
            user_id=user_id,
            exit_kind="step_limit",
            generic_user_message=final_message,
            policy_debug_note=debug_note,
        )
    else:
        bt_facts = str(loop_exit_auth_facts.get("blocker_type") or "").strip()
        if bt_facts:
            blocker_type = bt_facts
        loop_exit_auth_facts.setdefault("blocker_type", blocker_type)

    logger.info(
        "browser_task_step_limit",
        user_id=user_id,
        goal=goal,
        url=final_url,
        final_message=preview_for_log(final_message, limit=1200),
        trace_size=len(trace),
    )
    shot_out = loop_exit_screenshot_b64
    if not shot_out:
        shot_out = await _try_viewport_screenshot_b64(session)
        if shot_out:
            trace.append("viewport_screenshot: лимит шагов / выход (снимок для отладки)")
    return _make_browser_run_result(
        goal=goal,
        goal_display=resolved_goal_display,
        trace=trace,
        final_message=final_message,
        final_url=final_url,
        needs_user_input=needs_user_input,
        blocker_type=blocker_type,
        auth_facts=loop_exit_auth_facts,
        debug_note=debug_note,
        sub_intent=flow.sub_intent.value,
        screenshot_png_base64=shot_out,
    )
