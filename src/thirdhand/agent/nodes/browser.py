"""Browser automation node for open-ended web tasks."""

from src.thirdhand.agent.state import AgentState
from src.thirdhand.services.browser_agent import run_browser_task


async def run_browser_task_node(state: AgentState) -> dict:
    """Run the autonomous browser agent for generic web tasks."""
    goal = state.browser_goal or state.message_text

    if not goal.strip():
        return {
            "response_text": "⚠️ Не понял, что нужно сделать в браузере.",
            "response_type": "error",
        }

    result = await run_browser_task(
        goal=goal,
        user_id=state.user_id,
        context_text=state.user_profile.get("context_text", ""),
    )

    return {
        "browser_goal": goal,
        "browser_trace": result.trace,
        "browser_final_url": result.final_url,
        "browser_needs_user_input": result.needs_user_input,
        "response_text": result.telegram_report,
        "response_type": "text",
    }
