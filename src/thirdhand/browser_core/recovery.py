"""Centralised recovery layer for the browser agent.

Replaces the inline stuck-tool interceptor, no-progress escalation,
and captcha handling that were previously scattered across
``agent_loop.py``.

All recovery decisions flow through a single ``RecoveryLayer`` class
that returns structured ``RecoveryDecision`` values.  The caller
(``agent_loop`` or the future orchestrator) switches on the action
enum rather than duplicating recovery logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecoveryAction(str, Enum):
    """Structured recovery action returned by the recovery layer.

    The caller switches on this value rather than implementing
    ad-hoc recovery logic inline.
    """

    # Everything is fine — continue normal execution.
    CONTINUE = "continue"
    # Repeat the same step (e.g. after a transient failure).
    RETRY = "retry"
    # The current workflow policy is not working — request a new one.
    ALTERNATIVE_POLICY = "alternative_policy"
    # Use vision to understand the page before the next action.
    VISION_ASSIST = "vision_assist"
    # Re-plan the task from scratch (e.g. after a major context change).
    REPLAN = "replan"
    # Cannot recover automatically — ask the user for help.
    HUMAN_INTERVENTION = "human_intervention"


@dataclass
class RecoveryDecision:
    """Structured decision returned by ``RecoveryLayer`` methods."""

    action: RecoveryAction
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# Tools that are NEVER blocked by the recovery layer.
# The agent must always be allowed to inspect the page visually or finish.
_STUCK_SAFE_TOOLS: frozenset[str] = frozenset(
    {"use_visual_assist", "finish_task", "inspect_page"}
)

# Tools that change the page state (observation tools).
_OBSERVATION_TOOLS: frozenset[str] = frozenset(
    {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}
)


class RecoveryLayer:
    """Centralised recovery logic for the browser agent.

    Three responsibilities:

    1. **Tool blocking** — reject repetitive actions when the agent is stuck.
    2. **No-progress escalation** — decide whether to retry, use vision,
       or ask the user.
    3. **Visual-assist result handling** — detect captchas and other
       special page states from vision output.
    """

    # ------------------------------------------------------------------
    # Tool blocking
    # ------------------------------------------------------------------

    @staticmethod
    def is_tool_blocked(
        tool_name: str,
        *,
        no_progress_streak: int,
        last_stuck_tool_name: str,
        visual_assist_called_during_stuck: bool,
    ) -> bool:
        """Check whether a tool call should be rejected.

        When ``no_progress_streak >= 2``:

        * The same tool that caused the stagnation is **blocked**.
        * ``ask_user`` is **blocked** until the agent has called
          ``use_visual_assist`` at least once during this stuck period.
        * ``use_visual_assist``, ``finish_task``, ``inspect_page`` are
          **always allowed**.
        """
        if no_progress_streak < 2:
            return False

        # Always allow visual assist, finish, and inspect
        if tool_name in _STUCK_SAFE_TOOLS:
            return False

        # Block ask_user unless visual assist was already called
        if tool_name == "ask_user":
            return not visual_assist_called_during_stuck

        # Block the specific tool that caused stagnation
        if tool_name not in _OBSERVATION_TOOLS:
            return False
        return tool_name == last_stuck_tool_name

    @staticmethod
    def build_block_message(tool_name: str) -> str:
        """Build a rejection message for a blocked tool call."""
        if tool_name == "ask_user":
            return (
                "ERROR: Action rejected — you are stuck and must first "
                "call use_visual_assist to understand the page.\n"
                "Do NOT ask the user yet. Call use_visual_assist first."
            )
        return (
            "ERROR: Action rejected — you are repeating the same type "
            f"of action ({tool_name}) without making progress.\n"
            "You MUST call use_visual_assist to understand the page "
            "before taking any other action."
        )

    # ------------------------------------------------------------------
    # No-progress escalation
    # ------------------------------------------------------------------

    @staticmethod
    def assess_no_progress(
        no_progress_streak: int,
        snapshot: dict[str, Any],
        estimated_steps: int = 10,
    ) -> RecoveryDecision:
        """Assess a no-progress situation and decide the recovery action.

        Thresholds scale with task complexity (``estimated_steps`` from planner):
        - ``base = max(2, min(8, estimated_steps // 10))``
        - ``streak >= base + 3`` → ``HUMAN_INTERVENTION``
        - ``streak >= base + 2`` → ``REPLAN``
        - ``streak >= base + 1`` → ``ALTERNATIVE_POLICY``
        - ``streak >= base`` → ``VISION_ASSIST``
        - Otherwise → ``CONTINUE``

        For a simple 3-step task: base=2, so escalation starts at streak=2.
        For a complex 50-step task: base=5, so escalation starts at streak=5.
        """
        base = max(2, min(8, estimated_steps // 10))

        if no_progress_streak >= base + 3:
            return RecoveryDecision(
                action=RecoveryAction.HUMAN_INTERVENTION,
                message=(
                    "Не удалось продвинуться после нескольких попыток. "
                    "Нужна помощь пользователя или более явный ориентир на странице."
                ),
                metadata={"escalation_reason": "no_progress"},
            )

        if no_progress_streak >= base + 2:
            return RecoveryDecision(
                action=RecoveryAction.REPLAN,
                message=(
                    "Предыдущие попытки не дали результата. "
                    "Попробуй перепланировать задачу и начать заново."
                ),
                metadata={"escalation_reason": "replan"},
            )

        if no_progress_streak >= base + 1:
            hints = RecoveryLayer._build_hints(snapshot)
            return RecoveryDecision(
                action=RecoveryAction.ALTERNATIVE_POLICY,
                message=(
                    "Последние шаги не продвинули задачу.\n"
                    "Попробуй другой подход."
                    f"{hints}"
                ),
                metadata={"escalation_reason": "alternative_policy"},
            )

        if no_progress_streak >= base:
            hints = RecoveryLayer._build_hints(snapshot)
            return RecoveryDecision(
                action=RecoveryAction.VISION_ASSIST,
                message=(
                    "Последние шаги не продвинули задачу.\n"
                    "Не повторяй то же действие вслепую.\n"
                    "Используй use_visual_assist чтобы понять что делать дальше."
                    f"{hints}"
                    "\nЕсли модалка требует информацию, которой у тебя нет "
                    "(сопроводительное письмо, пароль), не закрывай её — вызови ask_user."
                ),
            )

        return RecoveryDecision(action=RecoveryAction.CONTINUE)

    # ------------------------------------------------------------------
    # Visual-assist result handling
    # ------------------------------------------------------------------

    @staticmethod
    def assess_visual_assist_result(
        visual_payload: dict[str, Any],
        *,
        visual_assist_same_page_streak: int,
    ) -> RecoveryDecision | None:
        """Analyse a ``use_visual_assist`` result for special page states.

        Currently detects **captcha** pages from the vision model output.
        Returns ``None`` when the result does not require any special handling.

        The LLM reads the raw vision text directly and decides how to proceed
        for other situations (login, modal, empty results) on its own.
        """
        task_type = str(visual_payload.get("task_type", "") or "").strip().lower()
        if task_type != "captcha":
            return None

        captcha_text = str(visual_payload.get("captcha_text", "") or "").strip()
        if not captcha_text:
            return None

        if visual_assist_same_page_streak >= 2:
            return RecoveryDecision(
                action=RecoveryAction.HUMAN_INTERVENTION,
                message=(
                    "Не удалось завершить автоматически. "
                    "Реши задачу вручную в открытом браузере, затем напиши «готово»."
                ),
                metadata={
                    "escalation_reason": "captcha_visual_assist_stuck",
                    "visual_assist_same_page_streak": visual_assist_same_page_streak,
                },
            )

        label = str(visual_payload.get("label", "") or "").strip()
        button_hint = str(visual_payload.get("button_hint", "") or "").strip()
        return RecoveryDecision(
            action=RecoveryAction.RETRY,
            message=(
                "Visual assist indicates a captcha or human verification step.\n"
                f"Vision result: captcha_text={captcha_text}, label={label}, "
                f"button_hint={button_hint}\n"
                f"CRITICAL: Your NEXT TWO tool calls MUST be:\n"
                f"1. type_text(text='{captcha_text}', label='{label}') — enter the captcha\n"
                f"2. click(text='{button_hint}' or 'Отправить' or 'Submit') — click the submit button\n"
                "DO NOT wait for auto-submit. DO NOT call use_visual_assist again.\n"
                "IMMEDIATELY call type_text, then IMMEDIATELY call click on the submit button.\n"
                "If type_text fails, try inspect_page to find element_id."
            ),
            metadata={
                "captcha_text": captcha_text,
                "label": label,
                "button_hint": button_hint,
                "task_type": "captcha",
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_hints(snapshot: dict[str, Any]) -> str:
        """Build a hints string from the page snapshot for no-progress messages."""
        parts: list[str] = []

        dialogs = snapshot.get("dialogs") or []
        if dialogs:
            parts.append(
                f"\nОткрытые диалоги/модалки: {str(dialogs[:3])[:500]}"
            )

        clickable = snapshot.get("clickable_hints", []) or []
        if clickable:
            parts.append(
                f"\nКликабельные элементы: {', '.join(str(h) for h in clickable[:10])}"
            )

        fillable = snapshot.get("fillable_hints", []) or []
        if fillable:
            parts.append(
                f"\nПоля для ввода: {', '.join(str(h) for h in fillable[:5])}"
            )

        return "".join(parts)