"""Response generation node - generates final response for chat intents."""

import structlog
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.thirdhand.agent.state import AgentState
from src.thirdhand.services.llm import create_llm
from src.thirdhand.services.telegram_format import format_agent_reply_for_telegram

logger = structlog.get_logger(__name__)

BASE_SYSTEM_PROMPT = """Ты — thirdHand, персональный AI-ассистент в Telegram.
Твоя задача — помогать с рутиной, напоминаниями и поиском информации.
Отвечай кратко и по делу. Используй эмодзи для наглядности.

Форматирование: используй HTML-теги для выделения текста:
- <b>жирный</b> для заголовков и ключевых слов
- <i>курсив</i> для пояснений
- <code>код</code> для команд и технических терминов
- <a href="URL">текст</a> для ссылок
Не используй Markdown (**, __, ```)."""


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
    llm = create_llm(temperature=0.7)
    chain = prompt | llm

    # Build conversation history
    history = state.conversation_history[-10:]  # Last 10 messages

    try:
        result = chain.invoke(
            {
                "message_text": state.message_text,
                "history": history,
            }
        )
        raw = result.content if hasattr(result, "content") else str(result)
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
