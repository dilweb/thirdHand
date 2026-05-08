"""Task context resolution and clarification node."""

from src.thirdhand.agent.schemas import ContextResolutionResult
from src.thirdhand.agent.state import AgentState


def resolve_task_context_node(state: AgentState) -> dict:
    """Decide whether the task can proceed or needs clarification first."""
    missing_context = state.missing_context or []
    clarification_question = (state.clarification_question or "").strip()

    if missing_context:
        # Do not short-circuit to a chat reply when the user asked for browser automation:
        # missing fields (код, пароль) are collected inside the browser loop via tools/ask_user
        # with real DOM evidence — otherwise the classifier's clarification may hallucinate actions.
        if (
            state.intent == "browser_task"
            and (state.browser_goal or "").strip()
        ):
            return ContextResolutionResult().model_dump(exclude_none=True, exclude_defaults=True)
        return ContextResolutionResult(
            requires_web_search=False,
            requires_browser=False,
            response_text=clarification_question
            or f"Нужно уточнить: {', '.join(missing_context)}.",
            response_type="text",
        ).model_dump(exclude_none=True)

    if state.requires_web_search and not (state.search_query or "").strip():
        return ContextResolutionResult(
            requires_web_search=False,
            response_text="Уточни, пожалуйста, что именно нужно найти.",
            response_type="text",
        ).model_dump(exclude_none=True)

    if state.requires_browser and not (state.browser_goal or "").strip():
        return ContextResolutionResult(
            requires_browser=False,
            response_text="Уточни, пожалуйста, что именно нужно сделать в браузере.",
            response_type="text",
        ).model_dump(exclude_none=True)

    if state.ambiguous_request and clarification_question:
        return ContextResolutionResult(
            requires_web_search=False,
            requires_browser=False,
            ambiguous_request=True,
            response_text=clarification_question,
            response_type="text",
        ).model_dump(exclude_none=True)

    return ContextResolutionResult().model_dump(exclude_none=True, exclude_defaults=True)
