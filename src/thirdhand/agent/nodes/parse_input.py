"""Parse input node - analyzes the user's task and required capabilities."""

import json

import structlog
from langchain_core.prompts import ChatPromptTemplate

from src.thirdhand.agent.schemas import TaskAnalysis
from src.thirdhand.agent.state import AgentState
from src.thirdhand.services.llm import create_llm, safe_invoke

logger = structlog.get_logger(__name__)


INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are an intent classifier for a personal assistant Telegram bot.
Classify the user message into one of these intents:
- **reminder**: User wants to be reminded of something at a specific time
- **search**: User wants to search for information on a topic
- **chat**: General conversation or question
- **profile_update**: User is sharing information about themselves (interests, preferences, context)
- **browser_task**: User wants you to operate websites, web apps, or a browser autonomously

You also receive saved user profile context. Use it when it helps fill missing details.

Also decide which capabilities are required:
- **requires_web_search=true** when you need fresh information from the public internet
- **requires_browser=true** when you must click, type, navigate, or otherwise control a website/app UI

Do not rely on surface keywords only. Base the decision on the user's actual goal.
Use browser_task for tasks that need action in a logged-in site or multi-step UI flow.
Use search for information-seeking tasks that can be solved with web results only.
If the user omitted a required detail, first try to resolve it from the saved profile.
Only put something into missing_context if it is truly required and still unavailable.
If missing_context is not empty, set a short clarification_question and avoid fabricating a query.

Extract relevant entities based on the intent and capabilities.

Examples:
- "напомни в четверг в 2 часа о собеседовании" → intent=reminder, requires_web_search=false, requires_browser=false, title="собеседование", remind_at="четверг 2:00"
- "найди новости про AI" → intent=search, requires_web_search=true, requires_browser=false, search_query="AI news"
- "какие вакансии по бэкенду есть?" → intent=search, requires_web_search=true, requires_browser=false, search_query="backend вакансии"
- "я работаю питон разработчиком" → intent=profile_update, requires_web_search=false, requires_browser=false, topic="programming", keywords=["python", "developer"]
- "прочитай последние 10 писем в яндекс почте и удали спам" → intent=browser_task, requires_web_search=false, requires_browser=true, browser_goal="прочитай последние 10 писем в яндекс почте и удали спам"
- "зайди на hh.ru и откликнись на 3 вакансии" → intent=browser_task, requires_web_search=false, requires_browser=true
- saved profile says location=Алматы, user says "погода в моем городе" → intent=search, requires_web_search=true, requires_browser=false, required_context=["location"], missing_context=[], search_query="погода сейчас в Алматы"
- no saved location, user says "погода в моем городе" → intent=search, requires_web_search=false, requires_browser=false, required_context=["location"], missing_context=["location"], clarification_question="В каком городе посмотреть погоду?"
- "привет, как дела?" → intent=chat, requires_web_search=false, requires_browser=false""",
        ),
        ("human", "Saved user profile: {profile_context}"),
        ("human", "{message_text}"),
    ]
)

# Default fallback when LLM fails
DEFAULT_FALLBACK = TaskAnalysis(intent="chat")


def parse_input_node(state: AgentState) -> dict:
    """Parse user input to classify intent and extract entities.

    If LLM invocation fails, falls back to 'chat' intent.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with intent, entities, and extracted fields.
    """
    profile_context = json.dumps(
        state.user_profile.get("context_summary", {}) or {},
        ensure_ascii=False,
    )
    llm = create_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(TaskAnalysis)
    chain = INTENT_PROMPT | structured_llm

    logger.debug("classifying_intent", user_id=state.user_id, message_preview=state.message_text[:100])

    result = safe_invoke(
        chain,
        {
            "message_text": state.message_text,
            "profile_context": profile_context[:2000],
        },
        fallback=None,
    )

    # Handle fallback (LLM failed or returned None)
    if result is None:
        logger.warning("llm_failed_using_fallback", user_id=state.user_id)
        result = DEFAULT_FALLBACK

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
    intent = result_dict.get("intent", "chat")
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

    output: dict = {
        "intent": intent,
        "requires_web_search": analysis.requires_web_search,
        "requires_browser": analysis.requires_browser,
        "routing_reason": analysis.routing_reason,
        "user_goal": analysis.user_goal or state.message_text,
        "required_context": analysis.required_context,
        "missing_context": analysis.missing_context,
        "clarification_question": analysis.clarification_question,
        "entities": {
            "title": analysis.title,
            "remind_at": analysis.remind_at,
            "description": analysis.description,
            "search_query": analysis.search_query,
            "topic": analysis.topic,
            "keywords": analysis.keywords,
            "browser_goal": analysis.browser_goal,
            "user_goal": analysis.user_goal or state.message_text,
            "requires_web_search": analysis.requires_web_search,
            "requires_browser": analysis.requires_browser,
            "routing_reason": analysis.routing_reason,
            "required_context": analysis.required_context,
            "missing_context": analysis.missing_context,
            "clarification_question": analysis.clarification_question,
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
        output["browser_goal"] = analysis.browser_goal or state.message_text
    elif intent == "profile_update":
        output["profile_updates"] = {
            "topic": analysis.topic,
            "keywords": analysis.keywords,
        }

    if output["requires_web_search"] and not output.get("search_query"):
        output["search_query"] = analysis.search_query or state.message_text

    if output["requires_browser"] and not output.get("browser_goal"):
        output["browser_goal"] = analysis.browser_goal or state.message_text

    logger.info(
        "intent_classified",
        user_id=state.user_id,
        intent=intent,
        requires_web_search=output["requires_web_search"],
        requires_browser=output["requires_browser"],
        missing_context=output["missing_context"],
    )

    return output
