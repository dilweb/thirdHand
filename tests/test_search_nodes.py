"""Tests for search flow nodes."""

from unittest.mock import patch

from src.thirdhand.agent.nodes.search import execute_search_node, filter_results_node
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
                    {"title": "Python result", "url": "https://example.com/python", "snippet": "Snippet"}
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
        assert "Вот что нашёл" in result["response_text"]
        assert "GPT-5" in result["response_text"]
        assert "Gemini" in result["response_text"]

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

        assert "Коротко" in result["response_text"]
        assert "Алматы" in result["response_text"]
        assert "Источники" in result["response_text"]

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
                {"title": f"Result {i}", "url": f"https://example.com/{i}", "snippet": f"Snippet {i}"}
                for i in range(10)
            ],
        )

        result = filter_results_node(state)

        # Results are 0-indexed, so we get Result 0-4 (5 items)
        text = result["response_text"]
        assert "Result 0" in text
        assert "Result 4" in text
        # Result 5 should not be in the text since we limit to 5
        assert "Result 5" not in text

    def test_filter_escapes_html_in_snippet_and_title(self) -> None:
        """External titles/snippets must not inject Telegram/HTML markup."""
        state = AgentState(
            user_id=123,
            search_results=[
                {
                    "title": '<b>fake</b>',
                    "url": "https://example.com/?q=a&b=1",
                    "snippet": "Line with <tag> & ampersand",
                }
            ],
        )

        result = filter_results_node(state)

        text = result["response_text"]
        assert "<b>fake</b>" not in text
        assert "&lt;b&gt;fake&lt;/b&gt;" in text
        assert "&lt;tag&gt;" in text
        assert "&amp;" in text
        assert 'href="https://example.com/?q=a&amp;b=1"' in text
