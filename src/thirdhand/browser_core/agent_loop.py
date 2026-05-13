"""Orchestrator for the browser agent loop.

Delegates per-step execution to ``TrajectoryExecutor``.
Keeps only the loop control (LLM calls, prompt rebuild, step limit).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from src.thirdhand.browser_core.executor import TrajectoryExecutor
from src.thirdhand.browser_core.page_classifier import PageType
from src.thirdhand.browser_core.planner import HighLevelPlanner
from src.thirdhand.browser_core.policy import LocalWorkflowPolicy, WorkflowState
from src.thirdhand.browser_core.recovery import RecoveryLayer, RecoveryAction
from src.thirdhand.browser_core.sub_intent import WorkflowType
from src.thirdhand.browser_core.validator import RuntimeValidator
from src.thirdhand.browser_core.prompts import (
    build_browser_core_system_prompt,
    build_browser_core_user_prompt,
    build_no_tool_followup,
)
from src.thirdhand.browser_core.session import BrowserSession
from src.thirdhand.browser_core.tools import build_browser_core_tools
from src.thirdhand.browser_core.tracking import BrowserTrackingState
from src.thirdhand.config import settings
from src.thirdhand.services.llm import ainvoke_with_retry, create_llm, preview_for_log

logger = structlog.get_logger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

# How many (AI + Tool + Snapshot) message triplets to keep in the sliding window.
_MAX_HISTORY_TRIPLETS = 12


def _trim_messages(messages: list) -> list:
    """Keep the preamble and the last N tool-call triplets to bound token usage."""
    preamble_end = 3
    if len(messages) <= preamble_end:
        return messages
    tail = messages[preamble_end:]
    keep = _MAX_HISTORY_TRIPLETS * 3
    if len(tail) > keep:
        tail = tail[-keep:]
    return messages[:preamble_end] + tail


@dataclass
class BrowserCoreRunResult:
    """Final result from the new browser core loop."""

    trace: list[str]
    final_url: str
    final_message: str
    needs_user_input: bool = False
    request_type: str = "other"
    screenshot_png_base64: str = ""
    stop_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


async def _emit_progress(progress_callback: ProgressCallback | None, text: str) -> None:
    if progress_callback is None:
        return
    try:
        await progress_callback(text)
    except Exception as exc:
        logger.warning("browser_core_progress_callback_failed", error=str(exc))


def _parse_snapshot(snapshot_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(snapshot_text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def run_browser_core_loop(
    *,
    session: BrowserSession,
    goal: str,
    user_id: int,
    context_text: str = "",
    progress_callback: ProgressCallback | None = None,
    resume_url: str = "",
    reuse_parked_live_tab: bool = False,
    latest_user_message: str = "",
) -> BrowserCoreRunResult:
    """Run the browser agent loop."""
    tools = build_browser_core_tools(session)
    llm = create_llm(model=settings.BROWSER_MODEL or None, temperature=0.0).bind_tools(
        list(tools.values())
    )

    tracking = BrowserTrackingState()

    trace: list[str] = []
    messages = [
        SystemMessage(content=build_browser_core_system_prompt(context_text)),
        HumanMessage(content=build_browser_core_user_prompt(goal)),
    ]

    if reuse_parked_live_tab:
        await session.ensure_started()
        header = await session.page_header()
        trace.append("open_browser(reuse_live_tab): {}")
        logger.info(
            "browser_core_reusing_parked_session",
            current_url=session.page.url if session.page else "",
            page_count=len(session.context.pages) if session.context else 0,
        )
    elif resume_url.strip():
        header = await session.open_browser(resume_url.strip())
        trace.append(f"open_browser: {json.dumps({'start_url': resume_url}, ensure_ascii=False)}")
    else:
        header = await session.open_browser()
        trace.append("open_browser: {}")
    messages.append(
        HumanMessage(
            content=(
                "Браузер готов. Начни с актуального состояния страницы и действуй через инструменты.\n"
                f"{header}"
            )
        )
    )
    if reuse_parked_live_tab and latest_user_message.strip():
        messages.append(
            HumanMessage(
                content=(
                    "Это продолжение ранее приостановленной browser-задачи.\n"
                    f"Новый ответ пользователя: {latest_user_message.strip()}\n"
                    "Используй этот ответ как актуальный внешний ввод для текущего шага. "
                    "Если пользователь прислал код, текст captcha, пароль, выбор или другой запрошенный ввод, "
                    "сначала попробуй применить именно его и не начинай сценарий заново."
                )
            )
        )

    initial_snapshot = await session.inspect_page()
    latest_snapshot_text = initial_snapshot
    latest_snapshot = _parse_snapshot(initial_snapshot)
    trace.append("inspect_page: {}")
    messages.append(HumanMessage(content=f"Актуальное состояние страницы:\n{initial_snapshot}"))

    # Initialise tracking
    last_structural_signature = tracking.structural_signature(latest_snapshot)
    tracking.classify_page(latest_snapshot)
    policy = LocalWorkflowPolicy()

    # ---- Plan the task ----
    plan = await HighLevelPlanner.plan(goal, context_text)

    logger.info(
        "browser_core_plan",
        user_id=user_id,
        primary_workflow=plan.primary_workflow.value,
        fast_path=plan.fast_path,
        estimated_steps=plan.estimated_steps,
        subtask_count=len(plan.subtasks),
        expected_first_actions=plan.expected_first_actions,
        summary=plan.summary[:200] if plan.summary else "",
    )

    # Aggregated cost tracking for the entire task
    _total_cost: float = 0.0
    _total_prompt_tokens: int = 0
    _total_completion_tokens: int = 0

    # ---- Inject plan into system prompt ----
    plan_context = ""
    if not plan.fast_path:
        parts: list[str] = []
        if plan.expected_flow:
            parts.append(
                "\n---\n📋 PLAN:\n" + "\n".join(
                    f"Step {s['step']}: {s['description']}"
                    for s in plan.expected_flow[:5]
                )
            )
        if plan.expected_first_actions:
            parts.append(
                "\n📋 FIRST ACTIONS:\n" + "\n".join(
                    f"  • {a}" for a in plan.expected_first_actions
                )
            )
        if parts:
            plan_context = "\n\n".join(parts)
    messages[0] = SystemMessage(
        content=build_browser_core_system_prompt(context_text) + plan_context
    )

    # ---- Initialise policy from plan ----
    _WORKFLOW_TO_STATE = {
        WorkflowType.DISCOVER: WorkflowState.DISCOVER,
        WorkflowType.SELECT: WorkflowState.SELECT,
        WorkflowType.APPLY: WorkflowState.APPLY,
        WorkflowType.MONITOR: WorkflowState.MONITOR,
        WorkflowType.FILL: WorkflowState.APPLY,
    }
    plan_state = _WORKFLOW_TO_STATE.get(plan.primary_workflow)
    if plan_state:
        policy.transition_to(plan_state)
        logger.info(
            "browser_core_policy_initialised",
            user_id=user_id,
            from_workflow=plan.primary_workflow.value,
            to_state=plan_state.value,
        )

    # ---- Initialise multi-item tracking from plan ----
    if plan.subtasks:
        _ACTION_WORKFLOWS = {WorkflowType.APPLY, WorkflowType.FILL, WorkflowType.DISCOVER}
        action_subtasks = [s for s in plan.subtasks if s.workflow in _ACTION_WORKFLOWS]
        if action_subtasks:
            tracking.items_total = len(action_subtasks)
            logger.info(
                "browser_core_multi_item_tracking",
                user_id=user_id,
                items_total=tracking.items_total,
                action_workflows=[s.workflow.value for s in action_subtasks],
            )

    # ---- Fast Path: skip policy for simple tasks ----
    if plan.fast_path:
        logger.info("browser_core_fast_path", user_id=user_id, goal=goal[:80])
        # Fast path: just one LLM call with the plan as context
        ai_message = await ainvoke_with_retry(llm, messages)
        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if tool_calls:
            batch_result = await TrajectoryExecutor.execute_batch(
                session=session, tools=tools, tool_calls=tool_calls,
                goal=goal, messages=messages, trace=trace,
                latest_snapshot=latest_snapshot,
                latest_snapshot_text=latest_snapshot_text,
                last_structural_signature=last_structural_signature,
            )
            if batch_result.should_stop:
                return BrowserCoreRunResult(
                    trace=batch_result.trace, final_url=batch_result.final_url,
                    final_message=batch_result.final_message,
                    needs_user_input=batch_result.needs_user_input,
                    request_type=batch_result.request_type,
                    screenshot_png_base64=batch_result.screenshot_png_base64,
                    stop_reason=batch_result.stop_reason,
                    metadata={"step_count": 1, **batch_result.metadata},
                )
        return BrowserCoreRunResult(
            trace=trace, final_url=await session.current_url(),
            final_message="Задача выполнена (fast path).",
            needs_user_input=False, stop_reason="finish_task",
            metadata={"step_count": 1, "fast_path": True},
        )

    # Track whether the system prompt needs rebuilding
    last_page_type: PageType = tracking.page_type
    last_cycle_detected: bool = False
    last_no_progress_streak: int = 0
    last_workflow_state: WorkflowState = policy.state

    for step in range(settings.BROWSER_MAX_STEPS):
        # ---- Rebuild system prompt when context changes ----
        page_type_changed = tracking.page_type != last_page_type
        cycle_state_changed = tracking.is_cycling() != last_cycle_detected
        streak_crossed = (
            tracking.no_progress_streak >= 1 and last_no_progress_streak == 0
        )
        state_changed = policy.state != last_workflow_state
        if page_type_changed or cycle_state_changed or streak_crossed or state_changed:
            # Compose base prompt + policy block + progress
            base_prompt = build_browser_core_system_prompt(context_text)
            policy_block = policy.build_prompt_block(
                page_type=tracking.page_type,
                no_progress_streak=tracking.no_progress_streak,
                cycle_detected=tracking.is_cycling(),
            )
            progress_block = tracking.progress_summary()
            full_prompt = base_prompt
            if policy_block:
                full_prompt += f"\n{policy_block}"
            if progress_block:
                full_prompt += f"\n\n{progress_block}"
            messages[0] = SystemMessage(content=full_prompt)
            if state_changed:
                logger.info(
                    "browser_core_policy_transition",
                    user_id=user_id,
                    step=step + 1,
                    from_state=last_workflow_state.value,
                    to_state=policy.state.value,
                )
            last_page_type = tracking.page_type
            last_cycle_detected = tracking.is_cycling()
            last_no_progress_streak = tracking.no_progress_streak
            last_workflow_state = policy.state

        messages = _trim_messages(messages)
        ai_message = await ainvoke_with_retry(llm, messages)
        messages.append(ai_message)
        tool_calls = getattr(ai_message, "tool_calls", None) or []

        # Accumulate cost from this LLM call
        meta = getattr(ai_message, "response_metadata", None) or {}
        if isinstance(meta, dict):
            usage = meta.get("token_usage") or {}
            if isinstance(usage, dict):
                _total_cost += usage.get("cost", 0) or 0
                _total_prompt_tokens += usage.get("prompt_tokens", 0) or 0
                _total_completion_tokens += usage.get("completion_tokens", 0) or 0

        logger.info(
            "browser_core_llm_raw_response",
            user_id=user_id,
            step=step + 1,
            tool_calls_count=len(tool_calls),
            tool_calls=[
                {"name": c.get("name"), "args": dict(c.get("args", {}))}
                for c in tool_calls
            ],
            full_content=getattr(ai_message, "content", "") or "",
            response_metadata=getattr(ai_message, "response_metadata", None),
        )

        # Emit LLM reasoning as progress
        llm_content = getattr(ai_message, "content", "") or ""
        await _emit_progress(progress_callback, llm_content)

        logger.info(
            "browser_core_llm_step_result",
            user_id=user_id,
            step=step + 1,
            tool_calls=[
                {"name": call.get("name"), "args": call.get("args", {})}
                for call in tool_calls
            ],
            content_preview=preview_for_log(getattr(ai_message, "content", ""), limit=2000),
        )

        if not tool_calls:
            tracking.no_tool_steps += 1
            fresh_snapshot = await session.inspect_page()
            latest_snapshot_text = fresh_snapshot
            latest_snapshot = _parse_snapshot(fresh_snapshot)
            trace.append("inspect_page: {}")
            messages.append(HumanMessage(content=build_no_tool_followup(fresh_snapshot)))
            if tracking.no_tool_steps >= 2:
                final_url = await session.current_url()
                screenshot = await session.capture_screenshot_data_url()
                screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                return BrowserCoreRunResult(
                    trace=trace,
                    final_url=final_url,
                    final_message="Модель не выбрала следующий инструмент и остановлена.",
                    needs_user_input=True,
                    request_type="other",
                    screenshot_png_base64=screenshot_b64,
                    stop_reason="no_tool_calls",
                    metadata={"step_count": step + 1},
                )
            continue

        tracking.no_tool_steps = 0

        # ---- Execute all tool calls as one batch ----
        batch_result = await TrajectoryExecutor.execute_batch(
            session=session,
            tools=tools,
            tool_calls=tool_calls,
            goal=goal,
            messages=messages,
            trace=trace,
            latest_snapshot=latest_snapshot,
            latest_snapshot_text=latest_snapshot_text,
            last_structural_signature=last_structural_signature,
        )

        # ---- Reintegrate updated state ----
        messages = batch_result.messages
        trace = batch_result.trace
        latest_snapshot = batch_result.latest_snapshot
        latest_snapshot_text = batch_result.latest_snapshot_text
        last_structural_signature = batch_result.last_structural_signature

        # ---- Handle stop / terminal signals ----
        if batch_result.should_stop:
            if batch_result.stop_reason in ("finish_task", "ask_user"):
                return BrowserCoreRunResult(
                    trace=batch_result.trace,
                    final_url=batch_result.final_url,
                    final_message=batch_result.final_message,
                    needs_user_input=batch_result.needs_user_input,
                    request_type=batch_result.request_type,
                    screenshot_png_base64=batch_result.screenshot_png_base64,
                    stop_reason=batch_result.stop_reason,
                    metadata={
                        "step_count": step + 1,
                        **batch_result.metadata,
                    },
                )
            screenshot = await session.capture_screenshot_data_url()
            screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
            return BrowserCoreRunResult(
                trace=batch_result.trace,
                final_url=await session.current_url(),
                final_message=batch_result.final_message,
                needs_user_input=True,
                request_type=batch_result.request_type,
                screenshot_png_base64=screenshot_b64,
                stop_reason="ask_user",
                metadata={
                    "step_count": step + 1,
                    **batch_result.metadata,
                },
            )

        # ---- Record actions in tracking (for all executed calls) ----
        for executed in batch_result.executed_calls:
            tracking.record_action(
                executed["name"], executed["args"], latest_snapshot
            )

        # ---- Analyse visual-assist results for captcha detection ----
        # The LLM reads the raw vision text directly and decides how to proceed
        # for other situations (login, modal, empty results) on its own.
        for i, executed in enumerate(batch_result.executed_calls):
            if executed["name"] == "use_visual_assist":
                vision_text = ""
                if i < len(batch_result.tool_results):
                    vision_text = str(batch_result.tool_results[i] or "").lower()
                if vision_text and any(kw in vision_text for kw in
                    ("captcha", "капча", "verify you're human", "human verification")):
                    visual_payload = {
                        "task_type": "captcha",
                        "captcha_text": "",
                        "label": "",
                        "button_hint": "",
                    }
                    vision_recovery = RecoveryLayer.assess_visual_assist_result(
                        visual_payload,
                        visual_assist_same_page_streak=tracking.visual_assist_same_page_streak,
                    )
                    if vision_recovery and vision_recovery.action != RecoveryAction.CONTINUE:
                        logger.info(
                            "browser_core_captcha_detected",
                            user_id=user_id,
                            step=step + 1,
                            recovery_action=vision_recovery.action.value,
                        )
                        if vision_recovery.message:
                            messages.append(HumanMessage(content=vision_recovery.message))

        # ---- Batch-level validation ----
        verdict = RuntimeValidator.validate(
            tool_name=batch_result.executed_calls[-1]["name"] if batch_result.executed_calls else "",
            tool_failed=batch_result.batch_failed,
            snapshot=latest_snapshot,
            previous_signature=last_structural_signature,
            cycle_detector=tracking.cycle_detector,
        )
        last_structural_signature = verdict.structural_signature

        logger.info(
            "browser_core_validation_verdict",
            user_id=user_id,
            step=step + 1,
            batch_size=len(batch_result.executed_calls),
            progress_made=verdict.progress_made,
            reason=verdict.reason,
            cycle_detected=verdict.cycle_detected,
            structural_signature=verdict.structural_signature[:48] if verdict.structural_signature else "",
            no_progress_streak=tracking.no_progress_streak,
        )

        # ---- Update state based on verdict ----
        if not verdict.progress_made:
            tracking.no_progress_streak += 1
            if tracking.no_progress_streak == 1:
                last_call = batch_result.executed_calls[-1] if batch_result.executed_calls else {}
                tracking.last_stuck_tool_name = last_call.get("name", "")
        else:
            tracking.no_progress_streak = 0
            tracking.last_stuck_tool_name = ""
            tracking.visual_assist_called_during_stuck = False

            # ---- Suggest FSM transition after successful step ----
            suggested = policy.suggest_transition(
                page_type=tracking.page_type,
                no_progress_streak=tracking.no_progress_streak,
                cycle_detected=tracking.is_cycling(),
            )
            if suggested and suggested != policy.state:
                logger.info(
                    "browser_core_policy_suggested_transition",
                    user_id=user_id,
                    step=step + 1,
                    from_state=policy.state.value,
                    to_state=suggested.value,
                )
                policy.transition_to(suggested)

                # ---- Replan after COMPLETE if more items remain ----
                if suggested == WorkflowState.COMPLETE and tracking.items_total > 0 \
                        and tracking.items_completed < tracking.items_total:
                    logger.info(
                        "browser_core_replan_after_complete",
                        user_id=user_id,
                        step=step + 1,
                        items_completed=tracking.items_completed,
                        items_total=tracking.items_total,
                    )
                    plan = await HighLevelPlanner.plan(goal, context_text)
                    plan_state = _WORKFLOW_TO_STATE.get(plan.primary_workflow)
                    if plan_state:
                        policy.transition_to(plan_state)
                        logger.info(
                            "browser_core_replanned",
                            user_id=user_id,
                            step=step + 1,
                            new_workflow=plan.primary_workflow.value,
                            new_state=plan_state.value,
                        )

        # ---- Recovery ----
        if not verdict.progress_made:
            logger.info(
                "browser_core_no_progress_detected",
                user_id=user_id,
                step=step + 1,
                batch_size=len(batch_result.executed_calls),
                no_progress_streak=tracking.no_progress_streak,
            )

            recovery_decision = RecoveryLayer.assess_no_progress(
                tracking.no_progress_streak,
                latest_snapshot,
                estimated_steps=plan.estimated_steps,
            )

            logger.info(
                "browser_core_recovery_decision",
                user_id=user_id,
                step=step + 1,
                action=recovery_decision.action.value,
                message=recovery_decision.message[:200] if recovery_decision.message else "",
                metadata=recovery_decision.metadata,
            )

            if recovery_decision.action == RecoveryAction.VISION_ASSIST:
                messages.append(HumanMessage(content=recovery_decision.message))

            elif recovery_decision.action == RecoveryAction.ALTERNATIVE_POLICY:
                situation = (recovery_decision.metadata or {}).get("situation", "")
                _SITUATION_TO_STATE = {
                    "login": WorkflowState.APPLY,
                    "captcha": WorkflowState.APPLY,
                    "modal": WorkflowState.APPLY,
                    "pagination": WorkflowState.DISCOVER,
                    "empty_results": WorkflowState.ALTERNATE_SEARCH,
                    "form_error": WorkflowState.APPLY,
                    "rate_limit": WorkflowState.AWAIT_USER,
                }
                target_state = _SITUATION_TO_STATE.get(situation)
                if target_state:
                    policy.transition_to(target_state)
                    # Track multi-item progress: APPLY → DISCOVER = one item done
                    if target_state == WorkflowState.DISCOVER and tracking.items_total > 0:
                        completed = tracking.increment_completed()
                        logger.info(
                            "browser_core_item_completed",
                            user_id=user_id,
                            step=step + 1,
                            items_completed=completed,
                            items_total=tracking.items_total,
                        )
                if recovery_decision.message:
                    messages.append(HumanMessage(content=recovery_decision.message))

            elif recovery_decision.action == RecoveryAction.REPLAN:
                plan = await HighLevelPlanner.plan(goal, context_text)
                plan_state = _WORKFLOW_TO_STATE.get(plan.primary_workflow)
                if plan_state:
                    policy.transition_to(plan_state)
                if recovery_decision.message:
                    messages.append(HumanMessage(content=recovery_decision.message))

            elif recovery_decision.action == RecoveryAction.HUMAN_INTERVENTION:
                screenshot = await session.capture_screenshot_data_url()
                screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                return BrowserCoreRunResult(
                    trace=trace,
                    final_url=await session.current_url(),
                    final_message=recovery_decision.message,
                    needs_user_input=True,
                    request_type="other",
                    screenshot_png_base64=screenshot_b64,
                    stop_reason="ask_user",
                    metadata={
                        "step_count": step + 1,
                        **(recovery_decision.metadata or {}),
                    },
                )

    final_url = await session.current_url()
    screenshot = await session.capture_screenshot_data_url()
    screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot

    logger.info(
        "browser_core_task_completed",
        user_id=user_id,
        step_count=step + 1,
        total_cost=round(_total_cost, 6),
        total_prompt_tokens=_total_prompt_tokens,
        total_completion_tokens=_total_completion_tokens,
        stop_reason="step_limit",
    )

    return BrowserCoreRunResult(
        trace=trace,
        final_url=final_url,
        final_message="Достигнут лимит шагов browser core.",
        needs_user_input=True,
        request_type="other",
        screenshot_png_base64=screenshot_b64,
        stop_reason="step_limit",
        metadata={"step_count": settings.BROWSER_MAX_STEPS},
    )
