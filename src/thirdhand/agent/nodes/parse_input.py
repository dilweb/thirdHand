"""Parse input node - classifies intent and extracts entities."""

import structlog
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.thirdhand.agent.state import AgentState
from src.thirdhand.services.llm import create_llm, safe_invoke

logger = structlog.get_logger(__name__)


class IntentClassification(BaseModel):
    """Schema for intent classification output."""

    intent: str = Field(
        description="The intent: 'reminder', 'search', 'chat', or 'profile_update'"
    )
    title: str = Field(default="", description="Title for reminders")
    remind_at: str = Field(default="", description="When to remind (for reminders)")
    description: str = Field(default="", description="Reminder description")
    search_query: str = Field(default="", description="Search query")
    topic: str = Field(default="", description="Topic for interests")
    keywords: list[str] = Field(default_factory=list, description="Keywords for interests")
    response_needed: bool = Field(
        default=True,
        description="Whether a response to the user is needed",
    )


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

Extract relevant entities based on the intent.

Examples:
- "напомни в четверг в 2 часа о собеседовании" → reminder, title="собеседование", remind_at="четверг 2:00"
- "найди новости про AI" → search, search_query="AI news"
- "я работаю питон разработчиком" → profile_update, topic="programming", keywords=["python", "developer"]
- "привет, как дела?" → chat""",
        ),
        ("human", "{message_text}"),
    ]
)

# Default fallback when LLM fails
DEFAULT_FALLBACK = {
    "intent": "chat",
    "title": "",
    "remind_at": "",
    "description": "",
    "search_query": "",
    "topic": "",
    "keywords": [],
    "response_needed": True,
}


def parse_input_node(state: AgentState) -> dict:
    """Parse user input to classify intent and extract entities.

    If LLM invocation fails, falls back to 'chat' intent.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with intent, entities, and extracted fields.
    """
    llm = create_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(IntentClassification)
    chain = INTENT_PROMPT | structured_llm

    logger.debug("classifying_intent", user_id=state.user_id, message_preview=state.message_text[:100])

    result = safe_invoke(chain, {"message_text": state.message_text}, fallback=None)

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
        result_dict = DEFAULT_FALLBACK

    # Validate intent
    valid_intents = {"reminder", "search", "chat", "profile_update"}
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

    output: dict = {
        "intent": intent,
        "entities": {
            "title": result_dict.get("title", ""),
            "remind_at": result_dict.get("remind_at", ""),
            "description": result_dict.get("description", ""),
            "search_query": result_dict.get("search_query", ""),
            "topic": result_dict.get("topic", ""),
            "keywords": result_dict.get("keywords", []),
        },
    }

    # Populate specific fields based on intent
    if intent == "reminder":
        output["reminder_title"] = result_dict.get("title", "")
        output["reminder_datetime"] = result_dict.get("remind_at", "")
        output["reminder_description"] = result_dict.get("description", "")
    elif intent == "search":
        output["search_query"] = result_dict.get("search_query", "")
    elif intent == "profile_update":
        output["profile_updates"] = {
            "topic": result_dict.get("topic", ""),
            "keywords": result_dict.get("keywords", []),
        }

    logger.info(
        "intent_classified",
        user_id=state.user_id,
        intent=intent,
    )

    return output
