"""Search flow nodes - execute search, select evidence, and synthesize results."""

import html
import structlog

from langchain_core.prompts import ChatPromptTemplate

from src.thirdhand.agent.schemas import (
    SearchEvidenceItem,
    SearchEvidencePack,
    SearchExecutionResult,
    SearchProviderResponse,
)
from src.thirdhand.agent.state import AgentState
from src.thirdhand.config import settings
from src.thirdhand.services.llm import create_llm, preview_for_log
from src.thirdhand.services.web_search import SearchError, search_web

logger = structlog.get_logger(__name__)


def _clean_snippet(text: str, limit: int = 180) -> str:
    """Normalize noisy search snippets for Telegram output."""
    normalized = " ".join((text or "").split())
    normalized = normalized.replace(" # ", " ").replace("## ", "")
    return normalized[:limit].rstrip()


def execute_search_node(state: AgentState) -> dict:
    """Execute a web search.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with search results.
    """
    query = state.search_query or state.entities.get("search_query", "")
    logger.info(
        "search_node_request",
        user_id=state.user_id,
        user_message=preview_for_log(state.message_text, limit=300),
        search_query=preview_for_log(query, limit=300),
        pending_task=preview_for_log(state.pending_task or {}, limit=700),
        recent_history=preview_for_log(
            state.conversation_history[-10:] if state.conversation_history else [], limit=1200
        ),
    )

    if not query:
        return SearchExecutionResult(
            response_text="⚠️ Укажи, что искать.",
            response_type="error",
            search_results=[],
        ).model_dump()

    try:
        search_response = SearchProviderResponse.model_validate(
            search_web(query, include_answer=True)
        )
        logger.info(
            "search_node_result",
            user_id=state.user_id,
            search_query=preview_for_log(query, limit=300),
            answer=preview_for_log(search_response.answer, limit=500),
            top_results=search_response.model_dump().get("results", [])[:3],
        )
    except (SearchError, Exception) as exc:
        return SearchExecutionResult(
            response_text=f"⚠️ Не удалось выполнить веб-поиск: {html.escape(str(exc), quote=False)}",
            response_type="error",
            search_results=[],
            search_query=query,
        ).model_dump()

    return SearchExecutionResult(
        search_results=search_response.results,
        search_answer=search_response.answer,
        search_query=query,
    ).model_dump()


def filter_results_node(state: AgentState) -> dict:
    """Select compact evidence from raw search output.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with compact evidence for final answer synthesis.
    """
    if state.response_type == "error" and state.response_text:
        return {
            "response_text": state.response_text,
            "response_type": state.response_type,
        }

    results = state.search_results
    answer = _clean_snippet(state.search_answer, limit=300)

    if not results:
        return {
            "response_text": "🔍 Ничего не найдено по твоему запросу.",
            "response_type": "search_results",
        }

    evidence = [
        SearchEvidenceItem(
            title=r.get("title", "N/A"),
            url=(r.get("url") or "").strip(),
            snippet=_clean_snippet(r.get("snippet", "")),
        ).model_dump()
        for r in results[:4]
    ]
    pack = SearchEvidencePack(
        search_query=state.search_query,
        answer_hint=answer,
        evidence=[SearchEvidenceItem.model_validate(item) for item in evidence],
    )
    logger.info(
        "search_evidence_selected",
        user_id=state.user_id,
        search_query=preview_for_log(state.search_query, limit=300),
        answer_hint=preview_for_log(answer, limit=400),
        evidence_count=len(pack.evidence),
    )
    return {
        "search_evidence": pack.model_dump().get("evidence", []),
        "search_answer": pack.answer_hint,
        "search_query": pack.search_query,
        "response_type": "search_results",
    }


SEARCH_SYNTHESIS_SYSTEM_PROMPT = """Ты готовишь финальный ответ пользователю на основе результатов веб-поиска.
Отвечай на языке пользователя. Если вопрос был по-русски, отвечай по-русски.
Не копируй сырой ответ провайдера слово в слово, а кратко перескажи суть.
Используй только факты из evidence.
Если информация неоднозначна, скажи это прямо.
Структура ответа:
1. Короткий вывод в 1-2 предложениях.
2. Если есть диапазон цен или несколько вариантов, укажи его.
3. В конце короткий блок с 1-3 источниками.
Пиши обычным plain text без HTML и без Markdown."""


def synthesize_search_response_node(state: AgentState) -> dict:
    """Turn selected evidence into a concise user-facing answer."""
    if state.response_type == "error" and state.response_text:
        return {
            "response_text": state.response_text,
            "response_type": state.response_type,
        }

    if state.response_text:
        return {
            "response_text": state.response_text,
            "response_type": state.response_type,
        }

    evidence = state.search_evidence or []
    if not evidence:
        return {
            "response_text": "🔍 Ничего не найдено по твоему запросу.",
            "response_type": "search_results",
        }

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SEARCH_SYNTHESIS_SYSTEM_PROMPT),
            (
                "human",
                "Запрос пользователя: {user_query}\n"
                "Подсказка от поискового провайдера: {answer_hint}\n"
                "Evidence: {evidence_json}",
            ),
        ]
    )
    llm = create_llm(model=settings.SEARCH_MODEL or settings.CHAT_MODEL or None, temperature=0.2)
    chain = prompt | llm
    llm_input = {
        "user_query": state.message_text,
        "answer_hint": state.search_answer or "",
        "evidence_json": preview_for_log(evidence, limit=4000),
    }
    logger.info(
        "search_synthesis_request",
        user_id=state.user_id,
        user_query=preview_for_log(state.message_text, limit=300),
        answer_hint=preview_for_log(state.search_answer, limit=500),
        evidence=preview_for_log(evidence, limit=1500),
    )
    try:
        result = chain.invoke(llm_input)
        raw = result.content if hasattr(result, "content") else str(result)
        logger.info(
            "search_synthesis_result",
            user_id=state.user_id,
            raw_response=preview_for_log(raw, limit=1200),
        )
        response_text = raw
    except Exception as exc:
        logger.warning(
            "search_synthesis_failed",
            user_id=state.user_id,
            error=str(exc),
        )
        lines = []
        if state.search_answer:
            lines.append(f"🔍 <b>Коротко:</b> {html.escape(state.search_answer, quote=False)}")
        else:
            first = evidence[0]
            lines.append(
                f"🔍 <b>Коротко:</b> По запросу нашёл несколько вариантов. "
                f"Например, <b>{html.escape(first.get('title', 'результат'), quote=False)}</b>."
            )
        lines.append("")
        lines.append("<b>Источники:</b>")
        for i, item in enumerate(evidence[:3], 1):
            title = html.escape(item.get("title", "N/A"), quote=False)
            url = (item.get("url") or "").strip()
            if url:
                href = html.escape(url, quote=True)
                lines.append(f'{i}. <a href="{href}">{title}</a>')
            else:
                lines.append(f"{i}. {title}")
        response_text = "\n".join(lines)

    return {
        "response_text": response_text,
        "response_type": "search_results",
    }
