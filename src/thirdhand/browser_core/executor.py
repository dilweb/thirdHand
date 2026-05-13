"""Batch executor for the browser agent.

Executes multiple tool calls from a single LLM response as a batch,
then performs one compact inspect + validation for the entire batch.

LLM decides batch size: if actions don't change the page (type_text + click
on same form), they can be batched. If an action navigates (goto_url), the
LLM puts it alone. No rules, no heuristics — the model decides.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, ToolMessage

from src.thirdhand.browser_core.inspect import compact_inspect_page
from src.thirdhand.browser_core.session import BrowserSession
from src.thirdhand.services.llm import preview_for_log

logger = structlog.get_logger(__name__)

# Tools that change the page state — triggers a mid-batch snapshot.
_OBSERVATION_TOOLS: frozenset[str] = frozenset(
    {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}
)


@dataclass
class BatchExecutionResult:
    """Result of executing one batch of tool calls.

    One LLM response → N tool calls → one batch → one validation.
    """

    # Updated context after the batch
    messages: list = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    latest_snapshot: dict[str, Any] = field(default_factory=dict)
    latest_snapshot_text: str = ""
    last_structural_signature: str = ""

    # All executed actions in this batch
    executed_calls: list[dict] = field(default_factory=list)
    tool_results: list[str] = field(default_factory=list)

    # Did any tool in the batch fail?
    batch_failed: bool = False
    error_tool: str = ""
    error_message: str = ""
    # Accumulated errors from individual tools (non-terminal — batch continues).
    batch_errors: list[dict] = field(default_factory=list)

    # Stop signals — set when a terminal tool or human intervention needed
    should_stop: bool = False
    stop_reason: str = ""
    final_url: str = ""
    final_message: str = ""
    needs_user_input: bool = False
    request_type: str = "other"
    screenshot_png_base64: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_snapshot(snapshot_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(snapshot_text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class TrajectoryExecutor:
    """Executes one batch of tool calls from a single LLM response.

    Stateless — all mutable state is passed in and returned out.
    """

    @staticmethod
    async def execute_batch(
        *,
        session: BrowserSession,
        tools: dict,
        tool_calls: list[dict],
        goal: str,
        messages: list,
        trace: list[str],
        latest_snapshot: dict[str, Any],
        latest_snapshot_text: str,
        last_structural_signature: str,
    ) -> BatchExecutionResult:
        """Execute a batch of tool calls from one LLM response.

        Tools are executed sequentially. After the batch, one compact
        inspect snapshot is taken (unless an observation tool already
        took one mid-batch).
        """
        result = BatchExecutionResult(
            messages=list(messages),
            trace=list(trace),
            latest_snapshot=dict(latest_snapshot),
            latest_snapshot_text=latest_snapshot_text,
            last_structural_signature=last_structural_signature,
        )

        snapshot_taken = False

        for call in tool_calls:
            tool_name = str(call.get("name", "") or "")
            args = call.get("args", {}) or {}
            args["_call_id"] = call["id"]
            result.trace.append(f"{tool_name}: {preview_for_log(args, limit=800)}")
            result.executed_calls.append({"name": tool_name, "args": args})

            # ---- Terminal tool: finish_task ----
            if tool_name == "finish_task":
                result.should_stop = True
                result.stop_reason = "finish_task"
                result.final_url = await session.current_url()
                result.final_message = str(args.get("summary", "") or "").strip()
                status = str(args.get("status", "completed") or "completed").strip()
                result.needs_user_input = status != "completed"
                if status != "completed":
                    screenshot = await session.capture_screenshot_data_url()
                    result.screenshot_png_base64 = screenshot.split(",", 1)[1] if "," in screenshot else ""
                return result

            # ---- Terminal tool: ask_user ----
            if tool_name == "ask_user":
                result.should_stop = True
                result.stop_reason = "ask_user"
                result.final_url = await session.current_url()
                result.final_message = str(args.get("prompt", "") or "").strip()
                result.request_type = str(args.get("request_type", "other") or "other").strip()
                result.needs_user_input = True
                screenshot = await session.capture_screenshot_data_url()
                result.screenshot_png_base64 = screenshot.split(",", 1)[1] if "," in screenshot else ""
                return result

            # ---- Execute the tool ----
            tool_result = await TrajectoryExecutor._execute_one(
                session=session,
                tools=tools,
                tool_name=tool_name,
                args=args,
                goal=goal,
            )
            tool_failed = isinstance(tool_result, str) and tool_result.startswith("ERROR:")
            result.tool_results.append(tool_result)

            # ---- Error in batch → record and continue ----
            if tool_failed:
                result.batch_failed = True
                result.error_tool = tool_name
                result.error_message = tool_result
                result.batch_errors.append({
                    "tool": tool_name,
                    "error": tool_result[:500],
                    "call_id": call["id"],
                })
                result.messages.append(
                    ToolMessage(
                        content=tool_result,
                        tool_call_id=call["id"],
                    )
                )
                continue  # Continue processing remaining tools in the batch

            # ---- Append tool result to messages ----
            result.messages.append(
                ToolMessage(
                    content=tool_result if isinstance(tool_result, str)
                    else json.dumps(tool_result, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            )

            # ---- Observation tool → mid-batch snapshot ----
            if tool_name in _OBSERVATION_TOOLS:
                await TrajectoryExecutor._take_mid_batch_snapshot(
                    session=session, result=result,
                )
                snapshot_taken = True

        # ---- Post-batch snapshot (if not taken mid-batch) ----
        if not snapshot_taken:
            fresh_snapshot = await compact_inspect_page(session.page)
            result.latest_snapshot_text = fresh_snapshot
            result.latest_snapshot = _parse_snapshot(fresh_snapshot)
            result.trace.append("inspect_page(compact): {}")
            result.messages.append(
                HumanMessage(content=f"Состояние после батча:\n{fresh_snapshot}")
            )

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    async def _execute_one(
        session: BrowserSession,
        tools: dict,
        tool_name: str,
        args: dict,
        goal: str,
    ) -> str:
        """Execute a single tool call. Returns the result string."""
        # Inject goal into visual assist
        if tool_name == "use_visual_assist" and not args.get("goal"):
            args["goal"] = goal

        tool = tools[tool_name]
        retries = 1 if tool_name in _OBSERVATION_TOOLS else 0
        for attempt in range(retries + 1):
            try:
                result = await tool.ainvoke(args)
                break
            except Exception as exc:
                if attempt < retries:
                    logger.info(
                        "browser_core_tool_retry",
                        tool_name=tool_name,
                        attempt=attempt + 1,
                        error=str(exc)[:200],
                    )
                    await asyncio.sleep(1.0)
                    continue
                result = f"ERROR: {type(exc).__name__}: {exc}"
                logger.warning(
                    "browser_core_tool_failed",
                    tool_name=tool_name,
                    error=str(exc),
                )

        logger.info(
            "browser_core_tool_result",
            tool_name=tool_name,
            args_preview=preview_for_log(args, limit=800),
            result_preview=preview_for_log(result, limit=3000),
        )
        return result

    @staticmethod
    async def _take_mid_batch_snapshot(
        session: BrowserSession,
        result: BatchExecutionResult,
    ) -> None:
        """Take a compact snapshot mid-batch (after observation tool).

        Waits briefly for the page to stabilise before inspecting,
        so the DOM snapshot reflects the post-action state accurately.
        """
        # Wait for network idle (max 3s) so dynamic content loads before snapshot
        try:
            await asyncio.wait_for(
                session.page.wait_for_load_state("networkidle"),
                timeout=3.0,
            )
        except Exception:
            pass  # timeout is acceptable — take snapshot anyway

        fresh_snapshot = "{}"
        for _attempt in range(3):
            try:
                fresh_snapshot = await compact_inspect_page(session.page)
                break
            except Exception:
                await asyncio.sleep(1.0)
        result.latest_snapshot_text = fresh_snapshot
        result.latest_snapshot = _parse_snapshot(fresh_snapshot)
        result.trace.append("inspect_page(compact): {}")
        result.messages.append(
            HumanMessage(content=f"Страница после действия:\n{fresh_snapshot}")
        )