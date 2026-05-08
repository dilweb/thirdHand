"""Web search provider integration."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import structlog

from src.thirdhand.agent.schemas import SearchProviderResponse, SearchResult
from src.thirdhand.config import settings
from src.thirdhand.services.llm import preview_for_log

logger = structlog.get_logger(__name__)


class SearchError(RuntimeError):
    """Raised when web search could not be completed."""


def search_web(query: str, include_answer: bool = False) -> SearchProviderResponse:
    """Run a real web search through Tavily."""
    if not query.strip():
        return SearchProviderResponse()

    api_key = settings.TAVILY_API_KEY.strip()
    if not api_key:
        raise SearchError("не настроен TAVILY_API_KEY")

    payload = json.dumps(
        {
            "query": query,
            "max_results": settings.SEARCH_MAX_RESULTS,
            "search_depth": "advanced",
            "topic": "general",
            "include_answer": include_answer,
            "include_raw_content": False,
        }
    ).encode("utf-8")

    request = Request(
        url="https://api.tavily.com/search",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    logger.info(
        "web_search_request",
        query=preview_for_log(query, limit=300),
        include_answer=include_answer,
        max_results=settings.SEARCH_MAX_RESULTS,
    )

    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        logger.warning("web_search_http_error", status=exc.code, query=query)
        if exc.code == 401:
            raise SearchError("неверный TAVILY_API_KEY") from exc
        raise SearchError(f"ошибка провайдера поиска ({exc.code})") from exc
    except URLError as exc:
        logger.warning("web_search_network_error", error=str(exc), query=query)
        raise SearchError("ошибка сети при обращении к провайдеру поиска") from exc
    except Exception as exc:
        logger.warning("web_search_unknown_error", error=str(exc), query=query)
        raise SearchError("непредвиденная ошибка поиска") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SearchError("провайдер поиска вернул некорректный ответ") from exc

    results = data.get("results", []) or []
    normalized: list[SearchResult] = []
    for item in results:
        normalized.append(
            SearchResult(
                title=item.get("title", "") or item.get("url", "Без названия"),
                url=item.get("url", ""),
                snippet=item.get("content", "") or item.get("raw_content", "") or "",
            )
        )
    return SearchProviderResponse(
        answer=data.get("answer", "") or "",
        results=normalized,
    )
