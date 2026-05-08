"""Tests for search flow nodes."""

from unittest.mock import MagicMock, patch

from src.thirdhand.agent.nodes.search import (
    execute_search_node,
    filter_results_node,
    synthesize_search_response_node,
)
from src.thirdhand.agent.state import AgentState


class TestExecuteSearchNode:
    """Tests for execute_search_node."""

    def test_search_with_query(self) -> None:
        """Test executing a search with a query."""
        state = AgentState(
            user_id=123,
            search_query="AI news",
        )

        with patch("src.thirdhand.agent.nodes.search.search_web") as mock_search:
            mock_search.return_value = {
                "answer": "",
                "results": [
                    {"title": "AI news 1", "url": "https://example.com/1", "snippet": "Snippet 1"},
                    {"title": "AI news 2", "url": "https://example.com/2", "snippet": "Snippet 2"},
                ],
            }
            result = execute_search_node(state)

        assert "search_results" in result
        assert len(result["search_results"]) == 2
        assert result["search_query"] == "AI news"

    def test_search_from_entities(self) -> None:
        """Test executing a search from entities."""
        state = AgentState(
            user_id=123,
            entities={"search_query": "Python 3.13 features"},
        )

        with patch("src.thirdhand.agent.nodes.search.search_web") as mock_search:
            mock_search.return_value = {
                "answer": "",
                "results": [
                    {
                        "title": "Python result",
                        "url": "https://example.com/python",
                        "snippet": "Snippet",
                    }
                ],
            }
            result = execute_search_node(state)

        assert "search_results" in result
        assert len(result["search_results"]) == 1

    def test_search_empty_query(self) -> None:
        """Test handling of empty search query."""
        state = AgentState(
            user_id=123,
            search_query="",
        )

        result = execute_search_node(state)

        assert result["response_type"] == "error"
        assert "Укажи" in result["response_text"]
        assert result["search_results"] == []

    def test_search_provider_error(self) -> None:
        """Search errors should surface honestly, not as placeholders."""
        state = AgentState(
            user_id=123,
            search_query="weather",
        )

        with patch("src.thirdhand.agent.nodes.search.search_web") as mock_search:
            mock_search.side_effect = Exception("boom")
            result = execute_search_node(state)

        assert result["response_type"] == "error"
        assert "Не удалось выполнить веб-поиск" in result["response_text"]

    def test_search_requests_concise_answer(self) -> None:
        """Search node should ask the provider for a concise answer when available."""
        state = AgentState(
            user_id=123,
            search_query="погода сейчас в Алматы",
        )

        with patch("src.thirdhand.agent.nodes.search.search_web") as mock_search:
            mock_search.return_value = {"answer": "В Алматы +18, ясно.", "results": []}
            result = execute_search_node(state)

        assert result["search_query"] == "погода сейчас в Алматы"
        mock_search.assert_called_once_with("погода сейчас в Алматы", include_answer=True)


class TestFilterResultsNode:
    """Tests for filter_results_node."""

    def test_filter_results_with_data(self) -> None:
        """Test filtering and formatting search results."""
        state = AgentState(
            user_id=123,
            search_results=[
                {
                    "title": "OpenAI GPT-5 Released",
                    "url": "https://example.com/gpt5",
                    "snippet": "OpenAI has released GPT-5 with improved reasoning capabilities.",
                },
                {
                    "title": "Google Gemini 2.0",
                    "url": "https://example.com/gemini",
                    "snippet": "Google announces Gemini 2.0 with multimodal capabilities.",
                },
            ],
        )

        result = filter_results_node(state)

        assert result["response_type"] == "search_results"
        assert "search_evidence" in result
        assert len(result["search_evidence"]) == 2
        assert result["search_evidence"][0]["title"] == "OpenAI GPT-5 Released"

    def test_filter_empty_results(self) -> None:
        """Test handling of empty search results."""
        state = AgentState(
            user_id=123,
            search_results=[],
        )

        result = filter_results_node(state)

        assert result["response_type"] == "search_results"
        assert "Ничего не найдено" in result["response_text"]

    def test_filter_uses_answer_summary(self) -> None:
        """If provider returned a concise answer, prefer it over raw snippets."""
        state = AgentState(
            user_id=123,
            search_answer="В Алматы сейчас +18°C, ясно.",
            search_results=[
                {
                    "title": "Яндекс Погода",
                    "url": "https://example.com/weather",
                    "snippet": "Очень длинный сниппет",
                }
            ],
        )

        result = filter_results_node(state)

        assert result["search_answer"] == "В Алматы сейчас +18°C, ясно."
        assert len(result["search_evidence"]) == 1

    def test_filter_preserves_upstream_error(self) -> None:
        """Upstream search errors should not be overwritten by empty-results text."""
        state = AgentState(
            user_id=123,
            search_results=[],
            response_type="error",
            response_text="⚠️ Не удалось выполнить веб-поиск: неверный TAVILY_API_KEY",
        )

        result = filter_results_node(state)

        assert result["response_type"] == "error"
        assert "неверный TAVILY_API_KEY" in result["response_text"]

    def test_filter_limits_to_5_results(self) -> None:
        """Test that results are limited to 5."""
        state = AgentState(
            user_id=123,
            search_results=[
                {
                    "title": f"Result {i}",
                    "url": f"https://example.com/{i}",
                    "snippet": f"Snippet {i}",
                }
                for i in range(10)
            ],
        )

        result = filter_results_node(state)

        titles = [item["title"] for item in result["search_evidence"]]
        assert "Result 0" in titles
        assert "Result 3" in titles
        assert "Result 4" not in titles

    def test_filter_escapes_html_in_snippet_and_title(self) -> None:
        """External titles/snippets must not inject Telegram/HTML markup."""
        state = AgentState(
            user_id=123,
            search_results=[
                {
                    "title": "<b>fake</b>",
                    "url": "https://example.com/?q=a&b=1",
                    "snippet": "Line with <tag> & ampersand",
                }
            ],
        )

        result = filter_results_node(state)

        assert result["search_evidence"][0]["title"] == "<b>fake</b>"
        assert "<tag>" in result["search_evidence"][0]["snippet"]


class TestSynthesizeSearchResponseNode:
    def test_synthesize_search_response_passes_error(self) -> None:
        state = AgentState(
            response_type="error",
            response_text="⚠️ Ошибка поиска",
        )

        result = synthesize_search_response_node(state)

        assert result["response_text"] == "⚠️ Ошибка поиска"

    def test_synthesize_search_response_uses_existing_text(self) -> None:
        state = AgentState(
            response_type="search_results",
            response_text="🔍 Уже готовый ответ",
        )

        result = synthesize_search_response_node(state)

        assert result["response_text"] == "🔍 Уже готовый ответ"

    def test_synthesize_search_response_fallback(self) -> None:
        """When chain.invoke fails, fall back to HTML summary from answer_hint + evidence."""
        state = AgentState(
            user_id=123,
            message_text="сколько стоит m273 в казахстане?",
            search_answer="Диапазон цен от 125 507 ₸ до 1 200 000 ₸.",
            search_evidence=[
                {
                    "title": "Kolesa",
                    "url": "https://example.com/kolesa",
                    "snippet": "M273 за 1 200 000 ₸ в Алматы",
                }
            ],
        )

        bad_chain = MagicMock()
        bad_chain.invoke.side_effect = RuntimeError("llm unavailable")
        mock_prompt = MagicMock()
        mock_prompt.__or__ = MagicMock(return_value=bad_chain)

        with patch(
            "src.thirdhand.agent.nodes.search.ChatPromptTemplate.from_messages",
            return_value=mock_prompt,
        ):
            result = synthesize_search_response_node(state)

        assert "Коротко" in result["response_text"]
        assert "125 507" in result["response_text"]
        assert "Kolesa" in result["response_text"]

    def test_synthesize_search_response_empty_evidence(self) -> None:
        state = AgentState(
            user_id=123,
            search_evidence=[],
        )

        result = synthesize_search_response_node(state)

        assert "Ничего не найдено" in result["response_text"]
