"""Tests for AutoElementResolver (_find_by_substring helper).

No scoring, no hardcoded weights — just substring matching.
"""

from src.thirdhand.browser_core.tools import _find_by_substring


class TestFindBySubstring:
    def test_exact_match(self) -> None:
        els = [
            {"id": "th-1", "text": "Откликнуться", "label": ""},
            {"id": "th-2", "text": "Найти", "label": ""},
        ]
        result = _find_by_substring(els, "text", "Откликнуться")
        assert result is not None
        assert result["id"] == "th-1"

    def test_partial_match(self) -> None:
        els = [
            {"id": "th-1", "text": "Откликнуться без сопроводительного", "label": ""},
        ]
        result = _find_by_substring(els, "text", "Откликнуться")
        assert result is not None
        assert result["id"] == "th-1"

    def test_no_match(self) -> None:
        els = [{"id": "th-1", "text": "Откликнуться", "label": ""}]
        result = _find_by_substring(els, "text", "Найти")
        assert result is None

    def test_empty_needle_returns_first(self) -> None:
        els = [{"id": "th-1", "text": "A"}, {"id": "th-2", "text": "B"}]
        result = _find_by_substring(els, "text", "")
        assert result is not None
        assert result["id"] == "th-1"

    def test_empty_list(self) -> None:
        assert _find_by_substring([], "text", "A") is None

    def test_match_by_label(self) -> None:
        els = [
            {"id": "th-1", "text": "", "label": "Профессия, должность или компания"},
            {"id": "th-2", "text": "", "label": "Исключить слова"},
        ]
        result = _find_by_substring(els, "label", "Профессия")
        assert result is not None
        assert result["id"] == "th-1"

    def test_case_insensitive(self) -> None:
        els = [{"id": "th-1", "text": "Python Developer", "label": ""}]
        result = _find_by_substring(els, "text", "python")
        assert result is not None
        assert result["id"] == "th-1"

    def test_long_text_match(self) -> None:
        """Very long text should still match via substring."""
        long_text = (
            "Senior / Lead Full-Stack Developer (Python/Django/FastAPI + Vue/PrimeVue) "
            "до 1 200 000 ₸ за месяц, на руки Опыт более 6 лет "
            "Можно удалённо Выплаты: раз в месяц ТОО Apple City Corps "
            "Алматы, улица Кабдолова, 1/7"
        )
        els = [{"id": "th-1", "text": long_text, "label": ""}]
        result = _find_by_substring(els, "text", "Senior / Lead Full-Stack")
        assert result is not None
        assert result["id"] == "th-1"