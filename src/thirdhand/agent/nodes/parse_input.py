"""Parse input node - analyzes the user's task and required capabilities."""

import json
from typing import Any

import structlog
from langchain_core.prompts import ChatPromptTemplate

from src.thirdhand.agent.schemas import TaskAnalysis
from src.thirdhand.agent.state import AgentState
from src.thirdhand.browser_core.goal_context import (
    build_operational_browser_goal,
    derive_canonical_objective_from_pending,
    truncate_display_title,
)
from src.thirdhand.browser_core.sub_intent import infer_browser_sub_intent
from src.thirdhand.config import settings
from src.thirdhand.services.llm import create_llm, preview_for_log, safe_invoke

logger = structlog.get_logger(__name__)

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a personal assistant Telegram bot.
Classify the user message into one of these intents:
- **reminder**: User wants to be reminded of something at a specific time
- **search**: User wants to search for information on a topic
- **chat**: General conversation or question
- **profile_update**: User is sharing information about themselves (interests, preferences, context)
- **browser_task**: User wants you to operate websites, web apps, or a browser autonomously

You also receive saved user profile context. Use it when it helps fill missing details.
You also receive recent conversation history and an optional pending unresolved task.

Also decide which capabilities are required:
- **requires_web_search=true** when you need fresh information from the public internet
- **requires_browser=true** when you must click, type, navigate, or otherwise control a website/app UI (including when the user might next send a short token or answer for a field on the page).

Do not rely on surface keywords only. Base the decision on the user's actual goal.
Use browser_task for tasks that need action in a logged-in site or multi-step UI flow.
Use search for information-seeking tasks that can be solved with web results only.
If the user omitted a required detail, first try to resolve it from the saved profile.
If there is a pending task, explicitly decide whether the current message continues that task. Set continue_pending_task=true only when the message is best understood as staying within that unresolved task; otherwise set it to false.
Only put something into missing_context if it is truly required and still unavailable.
If missing_context is not empty, set a short clarification_question and avoid fabricating a query.
**Never** claim in clarification_question that you already performed a browser/UI action (typing a phone number, clicking, logging in). This classifier has **no** Playwright/runtime proof; only the browser subgraph does. Ask only what is missing, neutrally — do not assume the page is waiting for SMS specifically.

Extract relevant entities based on the intent and capabilities.

Examples:
- "напомни в четверг в 2 часа о собеседовании" → intent=reminder, requires_web_search=false, requires_browser=false, title="собеседование", remind_at="четверг 2:00"
- "найди новости про AI" → intent=search, requires_web_search=true, requires_browser=false, search_query="AI news"
- "какие вакансии по бэкенду есть?" → intent=search, requires_web_search=true, requires_browser=false, search_query="backend вакансии"
- "я работаю питон разработчиком" → intent=profile_update, requires_web_search=false, requires_browser=false, topic="programming", keywords=["python", "developer"]
- "прочитай последние 10 писем в яндекс почте и удали спам" → intent=browser_task, requires_web_search=false, requires_browser=true, browser_goal="прочитай последние 10 писем в яндекс почте и удали спам"
- "зайди на hh.ru и откликнись на 3 вакансии" → intent=browser_task, requires_web_search=false, requires_browser=true
- pending task says browser_task is waiting for the next manual step, user says "готово" or "продолжай" → continue_pending_task=true, intent=browser_task, requires_browser=true, browser_goal should stay tied to the pending task instead of starting a new chat
- pending browser task exists, user asks "ты использовал распознавание картинки?" → continue_pending_task=true, intent=chat, requires_browser=false, answer the question within the active task context and keep the task resumable
- pending browser task exists, user says "теперь зайди в gmail и проверь письма" → continue_pending_task=false because this is a brand-new task
- saved profile says location=Алматы, user says "погода в моем городе" → intent=search, requires_web_search=true, requires_browser=false, required_context=["location"], missing_context=[], search_query="погода сейчас в Алматы"
- no saved location, user says "погода в моем городе" → intent=search, requires_web_search=false, requires_browser=false, required_context=["location"], missing_context=["location"], clarification_question="В каком городе посмотреть погоду?"
- "привет, как дела?" → intent=chat, requires_web_search=false, requires_browser=false"""


INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", INTENT_SYSTEM_PROMPT),
        ("human", "Saved user profile: {profile_context}"),
        ("human", "Recent conversation history:\n{recent_history}"),
        ("human", "Pending unresolved task: {pending_task_context}"),
        ("human", "{message_text}"),
    ]
)

# Default fallback when LLM fails
DEFAULT_FALLBACK = TaskAnalysis(intent="chat")


def looks_like_pending_browser_followup(message_text: str) -> bool:
    """True when the message plausibly continues a pending browser wait (vs a brand‑new goal).

    Kept permissive enough for normal sentences (OTP notes, «продолжай после лимита»); excludes
    obvious paste‑drops (URLs, huge walls of text).
    """
    raw = message_text or ""
    normalized = " ".join(raw.split())
    if not normalized:
        return False
    if "\n\n" in raw:
        return False
    lowered = normalized.lower()
    if "http://" in lowered or "https://" in lowered:
        return False
    if len(normalized) > 220:
        return False
    if len(normalized.split()) > 28:
        return False
    return True


def _looks_like_pending_followup(message_text: str) -> bool:
    """Backward-compatible alias within this module."""
    return looks_like_pending_browser_followup(message_text)


def _pending_browser_waiting(pending_task: dict[str, Any]) -> bool:
    """True when there is an unresolved browser task waiting for the user."""
    if not isinstance(pending_task, dict):
        return False
    return (
        pending_task.get("intent") == "browser_task"
        and bool(pending_task.get("requires_browser"))
        and bool(pending_task.get("awaiting_user_step"))
    )


def _active_task_goal_from_pending(pending_task: dict[str, Any]) -> str:
    """Stable task goal suitable for prompt/context reuse across all sites."""
    return (
        derive_canonical_objective_from_pending(pending_task)
        or str(pending_task.get("user_goal", "") or "").strip()
        or str(pending_task.get("browser_goal", "") or "").strip()
    )


def _summarize_active_task_context(pending_task: dict[str, Any]) -> dict[str, Any]:
    """Compact generic task context that can be injected into chat without site-specific logic."""
    if not isinstance(pending_task, dict) or not pending_task:
        return {}
    keys = (
        "intent",
        "user_goal",
        "canonical_user_objective",
        "requires_browser",
        "requires_web_search",
        "awaiting_user_step",
        "blocker_type",
        "browser_final_url",
        "browser_next_user_action",
        "browser_resume_strategy",
        "browser_stop_reason",
        "browser_sub_intent",
        "clarification_question",
        "missing_context",
    )
    out: dict[str, Any] = {}
    for key in keys:
        value = pending_task.get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def _hydrate_browser_continuation_from_pending(
    *,
    pending_task: dict[str, Any],
    latest_user_message: str,
    output: dict[str, Any],
) -> None:
    """Keep browser continuations anchored to the existing task while letting the LLM choose intent."""
    canon = (
        _active_task_goal_from_pending(pending_task)
        or str(output.get("canonical_user_objective", "") or "").strip()
        or str(output.get("user_goal", "") or "").strip()
        or latest_user_message.strip()
    )
    # browser_final_url may be empty if the page URL wasn't captured at ask_user time.
    # Don't pass a garbage URL - build_operational_browser_goal will fall back to
    # "Continue from the live page in the session" when resume_url is empty.
    resume_url = str(pending_task.get("browser_final_url", "") or "").strip()
    output["user_goal"] = canon
    output["canonical_user_objective"] = canon
    output["browser_goal_display"] = truncate_display_title(canon)
    output["browser_goal"] = build_operational_browser_goal(
        canonical_objective=canon,
        latest_user_message=latest_user_message,
        resume_url=resume_url,
    )
    # When browser_final_url is missing but we have a parked session,
    # the LLM will get the current page snapshot from bootstrap_live_continuation
    # and can inspect the page to find the right elements.
    output["entities"]["browser_final_url"] = resume_url
    output["entities"]["user_goal"] = canon
    output["entities"]["browser_goal"] = output["browser_goal"]
    output["entities"]["canonical_user_objective"] = canon
    output["entities"]["browser_goal_display"] = output["browser_goal_display"]
    persisted_si = str(pending_task.get("browser_sub_intent") or "").strip()
    output["browser_sub_intent"] = persisted_si or infer_browser_sub_intent(canon).value
    output["entities"]["browser_sub_intent"] = output["browser_sub_intent"]


def _fallback_task_analysis(message_text: str) -> TaskAnalysis:
    """Fallback when structured LLM output fails - no hardcoded markers, use LLM only."""
    # No hardcoded markers - rely entirely on LLM classification
    # If LLM fails, return a generic fallback that asks for clarification
    return TaskAnalysis(
        intent="chat",
        user_goal=message_text,
        routing_reason="Fallback: LLM classification failed, defaulting to chat.",
    )


def _should_relax_browser_missing_context(
    message_text: str,
    browser_goal: str,
    missing_context: list[str],
) -> bool:
    """Return True when a browser task can start even without preselected links/items.
    
    All decisions are made by LLM - no hardcoded markers.
    """
    if not missing_context:
        return False
    # Let LLM decide if context can be relaxed based on the goal
    relaxed_keys = {"vacancy_links", "vacancy_link", "links", "item_links", "result_links"}
    return set(missing_context).issubset(relaxed_keys)


def parse_input_node(state: AgentState) -> dict:
    """Parse user input to classify intent and extract entities.

    If LLM invocation fails, falls back to 'chat' intent.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with intent, entities, and extracted fields.
    """
    pending_task = state.pending_task or {}
    active_task_context = dict(pending_task) if isinstance(pending_task, dict) else {}
    active_task_intent = str(pending_task.get("intent", "") or "").strip()
    active_task_goal = _active_task_goal_from_pending(pending_task)

    profile_context = json.dumps(
        state.user_profile.get("context_summary", {}) or {},
        ensure_ascii=False,
    )
    recent_history = json.dumps(
        state.conversation_history[-10:] if state.conversation_history else [],
        ensure_ascii=False,
    )
    pending_task_context = json.dumps(pending_task, ensure_ascii=False)
    llm = create_llm(model=settings.INTENT_MODEL or None, temperature=0.0)
    structured_llm = llm.with_structured_output(TaskAnalysis)
    chain = INTENT_PROMPT | structured_llm
    llm_input = {
        "message_text": state.message_text,
        "profile_context": profile_context[:2000],
        "recent_history": recent_history[:3000],
        "pending_task_context": pending_task_context[:2000],
    }

    logger.info(
        "task_analysis_request",
        user_id=state.user_id,
        model=settings.INTENT_MODEL or settings.DEFAULT_MODEL,
        message_text=preview_for_log(state.message_text, limit=300),
        recent_history=preview_for_log(
            state.conversation_history[-10:] if state.conversation_history else [], limit=1200
        ),
        pending_task=state.pending_task or {},
    )

    result = safe_invoke(chain, llm_input, fallback=None)

    # Handle fallback (LLM failed or returned None)
    if result is None:
        logger.warning("llm_failed_using_fallback", user_id=state.user_id)
        result = _fallback_task_analysis(state.message_text)

    # safe_invoke may return a dict (from fallback) or a Pydantic model
    # Normalize to dict
    if hasattr(result, "model_dump"):
        result_dict = result.model_dump()
    elif isinstance(result, dict):
        result_dict = result
    else:
        logger.warning("unexpected_result_type", user_id=state.user_id, got=type(result).__name__)
        result_dict = DEFAULT_FALLBACK.model_dump()

    # Validate intent
    valid_intents = {"reminder", "search", "chat", "profile_update", "browser_task"}
    intent = str(result_dict.get("intent", "chat") or "chat").strip()
    intent_aliases = {
        "browsertask": "browser_task",
        "browser task": "browser_task",
        "profileupdate": "profile_update",
        "profile update": "profile_update",
    }
    intent = intent_aliases.get(intent.lower(), intent)
    if intent not in valid_intents:
        logger.warning(
            "invalid_intent_from_llm",
            user_id=state.user_id,
            got=intent,
            fallback_to="chat",
        )
        intent = "chat"
        result_dict["intent"] = intent

    analysis = TaskAnalysis.model_validate(result_dict)
    if (
        analysis.intent == "browser_task"
        and analysis.requires_browser
        and _should_relax_browser_missing_context(
            state.message_text,
            analysis.browser_goal or "",
            analysis.missing_context,
        )
    ):
        logger.info(
            "browser_missing_context_relaxed",
            user_id=state.user_id,
            model=settings.INTENT_MODEL or settings.DEFAULT_MODEL,
            original_missing_context=analysis.missing_context,
            original_clarification_question=analysis.clarification_question,
        )
        analysis.required_context = [
            item
            for item in analysis.required_context
            if item not in {"vacancy_links", "vacancy_link", "links"}
        ]
        analysis.missing_context = []
        analysis.clarification_question = ""

    logger.info(
        "task_analysis_result",
        user_id=state.user_id,
        model=settings.INTENT_MODEL or settings.DEFAULT_MODEL,
        analysis=analysis.model_dump(),
    )

    output: dict = {
        "intent": intent,
        "requires_web_search": analysis.requires_web_search,
        "requires_browser": analysis.requires_browser,
        "routing_reason": analysis.routing_reason,
        "user_goal": analysis.user_goal or state.message_text,
        "required_context": analysis.required_context,
        "missing_context": analysis.missing_context,
        "clarification_question": analysis.clarification_question,
        "ambiguous_request": False,
        "continue_pending_task": bool(analysis.continue_pending_task),
        "active_task_intent": active_task_intent,
        "active_task_goal": active_task_goal,
        "active_task_context": active_task_context,
        "preserve_pending_task": False,
        "entities": {
            "title": analysis.title,
            "remind_at": analysis.remind_at,
            "description": analysis.description,
            "search_query": analysis.search_query,
            "topic": analysis.topic,
            "keywords": analysis.keywords,
            "browser_goal": analysis.browser_goal,
            "canonical_user_objective": "",
            "browser_goal_display": "",
            "user_goal": analysis.user_goal or state.message_text,
            "requires_web_search": analysis.requires_web_search,
            "requires_browser": analysis.requires_browser,
            "routing_reason": analysis.routing_reason,
            "required_context": analysis.required_context,
            "missing_context": analysis.missing_context,
            "clarification_question": analysis.clarification_question,
            "ambiguous_request": False,
            "continue_pending_task": bool(analysis.continue_pending_task),
        },
    }

    # Populate specific fields based on intent
    if intent == "reminder":
        output["reminder_title"] = analysis.title
        output["reminder_datetime"] = analysis.remind_at
        output["reminder_description"] = analysis.description
    elif intent == "search":
        output["search_query"] = analysis.search_query
    elif intent == "browser_task":
        canon = (
            (analysis.user_goal or "").strip()
            or (analysis.browser_goal or "").strip()
            or state.message_text.strip()
        )
        output["user_goal"] = canon
        output["canonical_user_objective"] = canon
        output["browser_goal_display"] = truncate_display_title(canon)
        output["browser_goal"] = build_operational_browser_goal(
            canonical_objective=canon,
            latest_user_message=state.message_text,
            resume_url="",
        )
        output["entities"]["user_goal"] = canon
        output["entities"]["browser_goal"] = output["browser_goal"]
        output["entities"]["canonical_user_objective"] = canon
        output["entities"]["browser_goal_display"] = output["browser_goal_display"]
    elif intent == "profile_update":
        output["profile_updates"] = {
            "topic": analysis.topic,
            "keywords": analysis.keywords,
        }

    if output["requires_web_search"] and not output.get("search_query"):
        output["search_query"] = analysis.search_query or state.message_text

    if output["requires_browser"] and not output.get("browser_goal"):
        canon_fb = (
            (analysis.user_goal or "").strip()
            or (analysis.browser_goal or "").strip()
            or state.message_text.strip()
        )
        output["user_goal"] = output.get("user_goal") or canon_fb
        output["canonical_user_objective"] = output.get("canonical_user_objective") or canon_fb
        output["browser_goal_display"] = output.get("browser_goal_display") or truncate_display_title(
            canon_fb
        )
        output["browser_goal"] = build_operational_browser_goal(
            canonical_objective=output["canonical_user_objective"],
            latest_user_message=state.message_text,
            resume_url="",
        )
        output["entities"]["user_goal"] = output["user_goal"]
        output["entities"]["browser_goal"] = output["browser_goal"]
        output["entities"]["canonical_user_objective"] = output["canonical_user_objective"]
        output["entities"]["browser_goal_display"] = output["browser_goal_display"]

    if (
        _pending_browser_waiting(pending_task)
        and bool(analysis.continue_pending_task)
        and output["requires_browser"]
    ):
        _hydrate_browser_continuation_from_pending(
            pending_task=pending_task,
            latest_user_message=state.message_text,
            output=output,
        )
        output["routing_reason"] = (
            output["routing_reason"]
            or "Continuing the active browser task with the latest user message."
        )
        output["entities"]["routing_reason"] = output["routing_reason"]

    if intent == "browser_task":
        output["requires_browser"] = True
        output["entities"]["requires_browser"] = True

    if (
        analysis.clarification_question.strip()
        and not output["missing_context"]
        and output["requires_web_search"]
        and not output["requires_browser"]
    ):
        output["ambiguous_request"] = True
        output["entities"]["ambiguous_request"] = True

    if output["requires_browser"] and output.get("browser_goal"):
        output["browser_sub_intent"] = str(output.get("browser_sub_intent", "") or "").strip() or (
            infer_browser_sub_intent(
                output.get("canonical_user_objective") or output["browser_goal"]
            ).value
        )
    else:
        output["browser_sub_intent"] = ""
    output["entities"]["browser_sub_intent"] = output["browser_sub_intent"]

    # Preserve pending browser task when it was waiting for user input.
    # When a browser task calls ask_user, it parks the session and sets await_user_step=True.
    # ANY user reply should continue that task - we don't wait for LLM to correctly classify
    # continue_pending_task, because the user's phone number reply IS the continuation.
    if (
        pending_task.get("intent") == "browser_task"
        and pending_task.get("awaiting_user_step")
    ):
        output["preserve_pending_task"] = True
        # Force requires_browser so router sends to run_browser_task node
        output["requires_browser"] = True
        output["entities"]["requires_browser"] = True
        # Hydrate the continuation so browser node gets resume_url
        if not output.get("browser_goal"):
            _hydrate_browser_continuation_from_pending(
                pending_task=pending_task,
                latest_user_message=state.message_text,
                output=output,
            )
        logger.info(
            "pending_browser_awaiting_user_reply_preserved",
            user_id=state.user_id,
            browser_final_url=pending_task.get("browser_final_url", ""),
            intent=intent,
        )
    elif (
        active_task_intent
        and bool(analysis.continue_pending_task)
        and intent == "chat"
        and not output["requires_browser"]
        and not output["requires_web_search"]
        and not output["missing_context"]
    ):
        output["preserve_pending_task"] = bool(pending_task.get("awaiting_user_step"))

    logger.info(
        "intent_classified",
        user_id=state.user_id,
        intent=intent,
        requires_web_search=output["requires_web_search"],
        requires_browser=output["requires_browser"],
        missing_context=output["missing_context"],
        ambiguous_request=output["ambiguous_request"],
    )

    return output
