"""Browser automation node for open-ended web tasks."""

import structlog

from src.thirdhand.agent.state import AgentState
from src.thirdhand.browser_core.api import run_browser_task
from src.thirdhand.browser_core.goal_context import truncate_display_title

logger = structlog.get_logger(__name__)


async def run_browser_task_node(state: AgentState) -> dict:
    """Run the autonomous browser agent for generic web tasks."""
    logger.info(
        "browser_node_invoked",
        user_id=state.user_id,
        has_browser_goal=bool(state.browser_goal),
        has_message_text=bool(state.message_text),
        has_pending_task=bool(state.pending_task),
        sub_intent=getattr(state, "browser_sub_intent", None),
    )
    
    goal = state.browser_goal or state.message_text

    if not goal.strip():
        logger.warning("browser_node_empty_goal", user_id=state.user_id)
        return {
            "response_text": "⚠️ Не понял, что нужно сделать в браузере.",
            "response_type": "error",
        }

    goal_display = (state.browser_goal_display or "").strip()
    if not goal_display:
        canon = (state.canonical_user_objective or state.user_goal or "").strip()
        goal_display = truncate_display_title(canon)
    page_context_hint = (
        (state.canonical_user_objective or state.user_goal or state.message_text or "").strip()
    )
    if len(page_context_hint) > 600:
        page_context_hint = page_context_hint[:600].rstrip()

    sub_intent = str(getattr(state, "browser_sub_intent", "") or "").strip() or None
    logger.info(
        "browser_node_starting_task",
        user_id=state.user_id,
        goal_preview=goal[:200],
        goal_display=goal_display,
        sub_intent=sub_intent,
        resume_url=(state.pending_task or {}).get("browser_final_url", ""),
    )
    
    result = await run_browser_task(
        goal=goal,
        user_id=state.user_id,
        context_text=state.user_profile.get("context_text", ""),
        progress_callback=state.status_callback,
        resume_url=(state.pending_task or {}).get("browser_final_url", ""),
        sub_intent=sub_intent,
        goal_display=goal_display,
        page_context_hint=page_context_hint,
        latest_user_message=state.message_text,
    )

    logger.info(
        "browser_node_completed",
        user_id=state.user_id,
        needs_user_input=result.needs_user_input,
        blocker_type=result.blocker_type,
        final_url=result.final_url[:200] if result.final_url else "",
        trace_length=len(result.trace),
    )

    return {
        "browser_goal": goal,
        "user_goal": state.user_goal,
        "canonical_user_objective": state.canonical_user_objective,
        "browser_goal_display": state.browser_goal_display or goal_display,
        "browser_trace": result.trace,
        "browser_final_url": result.final_url,
        "browser_needs_user_input": result.needs_user_input,
        "browser_blocker_type": result.blocker_type,
        "browser_next_user_action": result.next_user_action,
        "browser_resume_strategy": result.resume_strategy,
        "browser_sub_intent": result.sub_intent,
        "browser_screenshot_png_base64": result.screenshot_png_base64,
        "browser_stop_reason": result.stop_reason,
        "response_text": result.telegram_report,
        "response_type": "text",
    }
