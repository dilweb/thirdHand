"""Search flow nodes - execute search and filter results."""

import html

from src.thirdhand.agent.schemas import SearchExecutionResult, SearchProviderResponse
from src.thirdhand.agent.state import AgentState
from src.thirdhand.services.web_search import SearchError, search_web


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
    """Filter and format search results.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with formatted response.
    """
    if state.response_type == "error" and state.response_text:
        return {
            "response_text": state.response_text,
            "response_type": state.response_type,
        }

    results = state.search_results
    answer = _clean_snippet(state.search_answer, limit=300)

    if answer:
        lines = [f"🔍 <b>Коротко:</b> {html.escape(answer, quote=False)}"]
        if results:
            lines.append("")
            lines.append("<b>Источники:</b>")
            for i, r in enumerate(results[:3], 1):
                title = html.escape(r.get("title", "N/A"), quote=False)
                url = (r.get("url") or "").strip()
                if url:
                    href = html.escape(url, quote=True)
                    lines.append(f'{i}. <a href="{href}">{title}</a>')
                else:
                    lines.append(f"{i}. {title}")
        return {
            "response_text": "\n".join(lines),
            "response_type": "search_results",
        }

    if not results:
        return {
            "response_text": "🔍 Ничего не найдено по твоему запросу.",
            "response_type": "search_results",
        }

    # Format results for display (HTML for Telegram)
    lines = ["🔍 Вот что нашёл:"]
    for i, r in enumerate(results[:5], 1):
        title = html.escape(r.get("title", "N/A"), quote=False)
        snippet = html.escape(_clean_snippet(r.get("snippet", "")), quote=False)
        url = (r.get("url") or "").strip()
        lines.append(f"\n{i}. <b>{title}</b>")
        lines.append(f"   {snippet}")
        if url:
            href = html.escape(url, quote=True)
            visible = html.escape(url, quote=False)
            lines.append(f'   <a href="{href}">{visible}</a>')

    return {
        "response_text": "\n".join(lines),
        "response_type": "search_results",
    }
