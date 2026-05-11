"""Simple observe-act-observe loop for the new browser core."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.thirdhand.browser_core.inspect import compact_inspect_page
from src.thirdhand.browser_core.page_classifier import PageType
from src.thirdhand.browser_core.prompts import (
    build_adaptive_system_prompt,
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

_OBSERVATION_TOOLS = {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}

# Tools that are NEVER blocked by the stuck-tool interceptor.
# The agent must always be allowed to inspect the page visually or finish.
# NOTE: ask_user is NOT in this set — when stuck, the agent MUST try
# use_visual_assist FIRST before asking the user for help.
_STUCK_SAFE_TOOLS = {"use_visual_assist", "finish_task", "inspect_page"}

# How many (AI + Tool + Snapshot) message triplets to keep in the sliding window.
# Each triplet is approximately 3 messages.  Keeping 8 means ~24 tail messages
# plus the protected preamble (system + goal + browser_ready), so the total
# stays well under 100k tokens for most models.
_MAX_HISTORY_TRIPLETS = 8


def _trim_messages(messages: list) -> list:
    """Keep the preamble and the last N tool-call triplets to bound token usage.

    Protected (never trimmed):
      - messages[0]: SystemMessage (system prompt)
      - messages[1]: HumanMessage with the user goal
      - messages[2]: HumanMessage "browser ready + initial snapshot"

    Everything after index 2 is trimmed to the last
    ``_MAX_HISTORY_TRIPLETS * 3`` messages.
    """
    preamble_end = 3  # indices 0, 1, 2 are always kept
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


def _parse_visual_payload(raw_result: Any) -> dict[str, Any]:
    if not isinstance(raw_result, str):
        return {}
    text = raw_result.strip()
    if not text:
        return {}
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
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
    """Run the new minimal browser loop."""
    tools = build_browser_core_tools(session)
    llm = create_llm(model=settings.BROWSER_MODEL or None, temperature=0.0).bind_tools(
        list(tools.values())
    )

    # Unified tracking state (replaces scattered scalar variables)
    tracking = BrowserTrackingState()

    trace: list[str] = []
    messages = [
        SystemMessage(content=build_adaptive_system_prompt(context_text)),
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

    # Initialise tracking with the first snapshot
    tracking.update_structural_signature(latest_snapshot)
    tracking.classify_page(latest_snapshot)

    # Track whether the system prompt needs rebuilding
    last_page_type: PageType = tracking.page_type
    last_cycle_detected: bool = False
    last_no_progress_streak: int = 0

    for step in range(settings.BROWSER_MAX_STEPS):
        # ---- Rebuild system prompt when context changes ----
        page_type_changed = tracking.page_type != last_page_type
        cycle_state_changed = tracking.is_cycling() != last_cycle_detected
        streak_crossed = (
            tracking.no_progress_streak >= 1 and last_no_progress_streak == 0
        )
        if page_type_changed or cycle_state_changed or streak_crossed:
            new_prompt = build_adaptive_system_prompt(
                context_text=context_text,
                page_type=tracking.page_type,
                no_progress_streak=tracking.no_progress_streak,
                cycle_detected=tracking.is_cycling(),
            )
            # Replace the system message at index 0
            messages[0] = SystemMessage(content=new_prompt)
            last_page_type = tracking.page_type
            last_cycle_detected = tracking.is_cycling()
            last_no_progress_streak = tracking.no_progress_streak

        messages = _trim_messages(messages)
        ai_message = await ainvoke_with_retry(llm, messages)
        messages.append(ai_message)
        tool_calls = getattr(ai_message, "tool_calls", None) or []

        # Emit LLM's actual reasoning as progress instead of a hardcoded status
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
        for call in tool_calls:
            tool_name = str(call.get("name", "") or "")
            args = call.get("args", {}) or {}
            trace.append(f"{tool_name}: {preview_for_log(args, limit=800)}")

            if tool_name == "finish_task":
                final_url = await session.current_url()
                summary = str(args.get("summary", "") or "").strip()
                status = str(args.get("status", "completed") or "completed").strip()
                screenshot_b64 = ""
                if status != "completed":
                    screenshot = await session.capture_screenshot_data_url()
                    screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                return BrowserCoreRunResult(
                    trace=trace,
                    final_url=final_url,
                    final_message=summary,
                    needs_user_input=status != "completed",
                    request_type="other",
                    screenshot_png_base64=screenshot_b64,
                    stop_reason="finish_task",
                    metadata={"status": status, "step_count": step + 1},
                )

            if tool_name == "ask_user":
                final_url = await session.current_url()
                prompt = str(args.get("prompt", "") or "").strip()
                request_type = str(args.get("request_type", "other") or "other").strip()
                screenshot = await session.capture_screenshot_data_url()
                screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                return BrowserCoreRunResult(
                    trace=trace,
                    final_url=final_url,
                    final_message=prompt,
                    needs_user_input=True,
                    request_type=request_type,
                    screenshot_png_base64=screenshot_b64,
                    stop_reason="ask_user",
                    metadata={"step_count": step + 1},
                )

            # ---- Stuck-tool interceptor: force use_visual_assist ----
            if _is_stuck_tool(tool_name, tracking):
                if tool_name == "ask_user":
                    reject_msg = (
                        "ERROR: Action rejected — you are stuck and must first "
                        "call use_visual_assist to understand the page.\n"
                        "Do NOT ask the user yet. Call use_visual_assist first."
                    )
                else:
                    reject_msg = (
                        "ERROR: Action rejected — you are repeating the same type "
                        f"of action ({tool_name}) without making progress.\n"
                        "You MUST call use_visual_assist to understand the page "
                        "before taking any other action."
                    )
                result = reject_msg
                logger.info(
                    "browser_core_tool_rejected_stuck",
                    user_id=user_id,
                    step=step + 1,
                    tool_name=tool_name,
                    no_progress_streak=tracking.no_progress_streak,
                )
            else:
                # Track when use_visual_assist is called during stuck period
                if tool_name == "use_visual_assist" and tracking.no_progress_streak >= 2:
                    tracking.visual_assist_called_during_stuck = True

                # Inject the user's goal into use_visual_assist so the vision
                # model knows what we're trying to accomplish.
                if tool_name == "use_visual_assist" and not args.get("goal"):
                    args["goal"] = goal

                tool = tools[tool_name]
                try:
                    result = await tool.ainvoke(args)
                except Exception as exc:
                    result = f"ERROR: {type(exc).__name__}: {exc}"
                    logger.warning(
                        "browser_core_tool_failed",
                        user_id=user_id,
                        step=step + 1,
                        tool_name=tool_name,
                        error=str(exc),
                    )
            logger.info(
                "browser_core_tool_result",
                user_id=user_id,
                step=step + 1,
                tool_name=tool_name,
                args_preview=preview_for_log(args, limit=800),
                result_preview=preview_for_log(result, limit=3000),
            )

            action_signature = json.dumps(
                {"tool_name": tool_name, "args": args},
                ensure_ascii=False,
                sort_keys=True,
            )

            if tool_name == "use_visual_assist":
                visual_payload = _parse_visual_payload(result)
                current_signature = json.dumps(
                    {
                        "url": await session.current_url(),
                        "snapshot": latest_snapshot_text,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if current_signature == tracking.last_visual_signature:
                    tracking.visual_assist_same_page_streak += 1
                else:
                    tracking.visual_assist_same_page_streak = 1
                    tracking.last_visual_signature = current_signature

                task_type = str(visual_payload.get("task_type", "") or "").strip().lower()
                next_action = str(visual_payload.get("next_action", "") or "").strip()
                captcha_text = str(visual_payload.get("captcha_text", "") or "").strip()
                if task_type == "captcha":
                    label = str(visual_payload.get("label", "") or "").strip()
                    button_hint = str(visual_payload.get("button_hint", "") or "").strip()
                    messages.append(
                        HumanMessage(
                            content=(
                                "Visual assist indicates a captcha or human verification step.\n"
                                f"Vision result: {result}\n"
                                f"CRITICAL: Your NEXT TWO tool calls MUST be:\n"
                                f"1. type_text(text='{captcha_text}', label='{label}') — enter the captcha\n"
                                f"2. click(text='{button_hint}' or 'Отправить' or 'Submit') — click the submit button\n"
                                f"DO NOT wait for auto-submit. DO NOT call use_visual_assist again.\n"
                                f"IMMEDIATELY call type_text, then IMMEDIATELY call click on the submit button.\n"
                                "If type_text fails, try inspect_page to find element_id."
                            )
                        )
                    )
                    logger.info(
                        "browser_core_captcha_visual_state",
                        user_id=user_id,
                        step=step + 1,
                        visual_assist_same_page_streak=tracking.visual_assist_same_page_streak,
                        task_type=task_type,
                        captcha_text=captcha_text,
                        next_action=next_action,
                    )
                    if tracking.visual_assist_same_page_streak >= 2:
                        final_url = await session.current_url()
                        screenshot = await session.capture_screenshot_data_url()
                        screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                        return BrowserCoreRunResult(
                            trace=trace,
                            final_url=final_url,
                            final_message=(
                                "Не удалось завершить автоматически. "
                                "Реши задачу вручную в открытом браузере, затем напиши «готово»."
                            ),
                            needs_user_input=True,
                            request_type="captcha",
                            screenshot_png_base64=screenshot_b64,
                            stop_reason="ask_user",
                            metadata={
                                "step_count": step + 1,
                                "escalation_reason": "captcha_visual_assist_stuck",
                                "visual_assist_same_page_streak": tracking.visual_assist_same_page_streak,
                            },
                        )
                else:
                    tracking.visual_assist_same_page_streak = 0
                    tracking.last_visual_signature = current_signature

            messages.append(
                ToolMessage(
                    content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            )

            # ---- Record action in tracking state ----
            tracking.record_action(tool_name, args, latest_snapshot)

            progress_changed = False
            # After any observation action, take a compact auto-snapshot to verify
            # the result.  compact_inspect_page returns ~1-2k tokens instead of
            # the 30-50k that full inspect_page produces, keeping the context
            # window bounded.  The LLM can always call inspect_page explicitly
            # for the full dump if it needs more detail.
            if tool_name in _OBSERVATION_TOOLS:
                # After actions that trigger navigation (e.g. type_text with submit=True,
                # press_key Enter, click on a link), the page may still be loading.
                # compact_inspect_page will fail with "Execution context was destroyed"
                # if called during navigation. Retry up to 3 times with a 1s delay.
                fresh_snapshot = "{}"
                for _attempt in range(3):
                    try:
                        fresh_snapshot = await compact_inspect_page(session.page)
                        break
                    except Exception:
                        await asyncio.sleep(1.0)
                latest_snapshot_text = fresh_snapshot
                latest_snapshot = _parse_snapshot(fresh_snapshot)
                trace.append("inspect_page(compact): {}")
                messages.append(HumanMessage(content=f"Страница после действия:\n{fresh_snapshot}"))
                tracking.visual_assist_same_page_streak = 0
                # Compute new signature WITHOUT storing it yet
                new_structural_sig = tracking.structural_signature(latest_snapshot)
                old_structural_sig = tracking.last_structural_signature
                progress_changed = new_structural_sig != old_structural_sig
                logger.info(
                    "browser_core_progress_check",
                    user_id=user_id,
                    step=step + 1,
                    tool_name=tool_name,
                    progress_changed=progress_changed,
                    previous_state_signature_preview=preview_for_log(
                        old_structural_sig, limit=1000
                    ),
                    current_state_signature_preview=preview_for_log(new_structural_sig, limit=1000),
                )
                # Now store the new signature
                tracking.last_structural_signature = new_structural_sig
                # Re-classify page when structure changes
                tracking.classify_page(latest_snapshot)

            tool_failed = isinstance(result, str) and result.startswith("ERROR:")
            repeated_same_action = action_signature == tracking.last_action_signature
            tracking.last_action_signature = action_signature

            # ---- Check progress using the pre-computed progress_changed ----
            had_progress = tracking.check_progress(
                tool_name, tool_failed, latest_snapshot, progress_changed
            )

            if not had_progress:
                logger.info(
                    "browser_core_no_progress_detected",
                    user_id=user_id,
                    step=step + 1,
                    tool_name=tool_name,
                    tool_failed=tool_failed,
                    repeated_same_action=repeated_same_action,
                    no_progress_streak=tracking.no_progress_streak,
                )
                if tracking.no_progress_streak >= 2:
                    dialogs_info = ""
                    if latest_snapshot.get("dialogs"):
                        dialogs_info = "\nОткрытые диалоги/модалки: " + str(latest_snapshot.get("dialogs")[:500])

                    clickable_hints = latest_snapshot.get("clickable_hints", []) or []
                    fillable_hints = latest_snapshot.get("fillable_hints", []) or []
                    hints_info = ""
                    if clickable_hints:
                        hints_info += "\nКликабельные элементы: " + ", ".join(clickable_hints[:10])
                    if fillable_hints:
                        hints_info += "\nПоля для ввода: " + ", ".join(fillable_hints[:5])

                    messages.append(
                        HumanMessage(
                            content=(
                                "Последние шаги не продвинули задачу.\n"
                                "Не повторяй то же действие вслепую.\n"
                                "Используй use_visual_assist чтобы понять что делать дальше.\n"
                                f"Подсказки из inspect_page:{hints_info}{dialogs_info}"
                                "\nЕсли модалка требует информацию, которой у тебя нет (сопроводительное письмо, пароль), не закрывай её — вызови ask_user."
                            )
                        )
                    )
                if tracking.no_progress_streak >= 3:
                    final_url = await session.current_url()
                    screenshot = await session.capture_screenshot_data_url()
                    screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
                    return BrowserCoreRunResult(
                        trace=trace,
                        final_url=final_url,
                        final_message=(
                            "Не удалось продвинуться после нескольких попыток. "
                            "Нужна помощь пользователя или более явный ориентир на странице."
                        ),
                        needs_user_input=True,
                        request_type="other",
                        screenshot_png_base64=screenshot_b64,
                        stop_reason="ask_user",
                        metadata={
                            "step_count": step + 1,
                            "escalation_reason": "no_progress",
                            "no_progress_streak": tracking.no_progress_streak,
                        },
                    )

    final_url = await session.current_url()
    screenshot = await session.capture_screenshot_data_url()
    screenshot_b64 = screenshot.split(",", 1)[1] if "," in screenshot else screenshot
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


# ---------------------------------------------------------------------------
# Stuck-tool interceptor
# ---------------------------------------------------------------------------

def _is_stuck_tool(tool_name: str, tracking: BrowserTrackingState) -> bool:
    """Check whether a tool call should be rejected because the agent is stuck.

    When ``no_progress_streak >= 2``:

    * The same tool that caused the stagnation is **blocked**.
    * ``ask_user`` is **blocked** until the agent has called
      ``use_visual_assist`` at least once during this stuck period.
    * ``use_visual_assist``, ``finish_task``, ``inspect_page`` are
      **always allowed**.
    """
    if tracking.no_progress_streak < 2:
        return False

    # Always allow visual assist, finish, and inspect
    if tool_name in _STUCK_SAFE_TOOLS:
        return False

    # Block ask_user unless visual assist was already called
    if tool_name == "ask_user":
        return not tracking.visual_assist_called_during_stuck

    # Block the specific tool that caused stagnation
    if tool_name not in _OBSERVATION_TOOLS:
        return False
    return tool_name == tracking.last_stuck_tool_name
