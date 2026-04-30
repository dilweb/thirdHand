"""Search flow nodes - execute search and filter results."""

import html

from src.thirdhand.agent.state import AgentState


def execute_search_node(state: AgentState) -> dict:
    """Execute a web search.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with search results.
    """
    query = state.search_query or state.entities.get("search_query", "")

    if not query:
        return {
            "response_text": "⚠️ Укажи, что искать.",
            "response_type": "error",
            "search_results": [],
        }

    # TODO: Integrate Tavily/DuckDuckGo API
    # For now, return placeholder
    results = [
        {
            "title": f"Result 1 for '{query}'",
            "url": "https://example.com/1",
            "snippet": f"This is a placeholder result for: {query}",
        },
        {
            "title": f"Result 2 for '{query}'",
            "url": "https://example.com/2",
            "snippet": f"Another placeholder result for: {query}",
        },
    ]

    return {
        "search_results": results,
        "search_query": query,
    }


def filter_results_node(state: AgentState) -> dict:
    """Filter and format search results.

    Args:
        state: Current agent state.

    Returns:
        Dictionary with formatted response.
    """
    results = state.search_results

    if not results:
        return {
            "response_text": "🔍 Ничего не найдено по твоему запросу.",
            "response_type": "search_results",
        }

    # Format results for display (HTML for Telegram)
    lines = ["🔍 Вот что нашёл:"]
    for i, r in enumerate(results[:5], 1):
        title = html.escape(r.get("title", "N/A"), quote=False)
        snippet = html.escape((r.get("snippet", "") or "")[:200], quote=False)
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
