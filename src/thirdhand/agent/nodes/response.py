"""Response generation node - generates final response for chat intents."""

import structlog
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.thirdhand.agent.state import AgentState
from src.thirdhand.config import settings
from src.thirdhand.services.llm import create_llm, preview_for_log
from src.thirdhand.services.telegram_format import format_agent_reply_for_telegram

logger = structlog.get_logger(__name__)

BASE_SYSTEM_PROMPT = """You are ThirdHand, a personal AI assistant in Telegram.
Your job is to provide routine tasks, reminders, and information search.
My answers are brief and to the point. I use emojis for clarity, but rarely.
Write in plain text, no HTML or Markdown."""


def generate_response_node(state: AgentState) -> dict:
    """Generate a response for conversational intents.

    If the state already has a response_text (from other nodes), pass it through.
    Otherwise, generate a response using the LLM with context injection.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with response text.
    """
    # If we already have a response from another node, pass it through
    if state.response_text:
        return {
            "response_text": state.response_text,
            "response_type": state.response_type,
        }

    # Build system prompt with context
    context_text = state.user_profile.get("context_text", "")
    if context_text:
        system_prompt = f"{BASE_SYSTEM_PROMPT}\n\n{context_text}"
    else:
        system_prompt = BASE_SYSTEM_PROMPT

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="history", optional=True),
            ("human", "{message_text}"),
        ]
    )

    # Generate a conversational response
    llm = create_llm(model=settings.CHAT_MODEL or None, temperature=0.7)
    chain = prompt | llm

    # Build conversation history
    history = state.conversation_history[-10:]  # Last 10 messages
    logger.info(
        "response_generation_request",
        user_id=state.user_id,
        model=settings.CHAT_MODEL or settings.DEFAULT_MODEL,
        message_text=preview_for_log(state.message_text, limit=300),
        history=preview_for_log(history, limit=1200),
        system_prompt=preview_for_log(system_prompt, limit=1200),
    )

    try:
        result = chain.invoke(
            {
                "message_text": state.message_text,
                "history": history,
            }
        )
        raw = result.content if hasattr(result, "content") else str(result)
        logger.info(
            "response_generation_result",
            user_id=state.user_id,
            model=settings.CHAT_MODEL or settings.DEFAULT_MODEL,
            raw_response=preview_for_log(raw, limit=1200),
        )
        response_text = format_agent_reply_for_telegram(raw)
    except Exception as e:
        logger.error("response_generation_failed", error=str(e))
        response_text = format_agent_reply_for_telegram(
            "🤔 Хм, не могу сейчас ответить. Попробуй позже."
        )

    return {
        "response_text": response_text,
        "response_type": "text",
    }
