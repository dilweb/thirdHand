"""Tests for PageClassifier — language-agnostic page type detection."""

from src.thirdhand.browser_core.page_classifier import PageClassifier, PageType


class TestPageClassifier:
    def test_classify_search_results(self) -> None:
        """Many links, few forms = search results (any language)."""
        snap = {
            "text": "any language text here español 日本語",
            "actionable": [{"tag": "a", "role": ""} for _ in range(8)],
            "fillable": [{"type": "text"}],
            "headings": ["Results"],
        }
        assert PageClassifier.classify(snap) == PageType.SEARCH_RESULTS

    def test_classify_login_page(self) -> None:
        """Password field = login page (HTML standard, any language)."""
        snap = {
            "text": "iniciar sesión contraseña",
            "fillable": [
                {"type": "text", "autocomplete": "username"},
                {"type": "password", "autocomplete": "current-password"},
            ],
            "actionable": [{"tag": "button"}],
        }
        assert PageClassifier.classify(snap) == PageType.LOGIN_PAGE

    def test_classify_login_page_japanese(self) -> None:
        """Japanese site — same HTML standard type=password."""
        snap = {
            "text": "ログイン パスワード",
            "fillable": [
                {"type": "email"},
                {"type": "password"},
            ],
            "actionable": [{"tag": "button"}],
        }
        assert PageClassifier.classify(snap) == PageType.LOGIN_PAGE

    def test_classify_login_page_russian(self) -> None:
        """Russian site — same HTML standard type=password."""
        snap = {
            "text": "войти в аккаунт",
            "fillable": [
                {"type": "text"},
                {"type": "password"},
            ],
            "actionable": [{"tag": "button"}],
        }
        assert PageClassifier.classify(snap) == PageType.LOGIN_PAGE

    def test_classify_form_page(self) -> None:
        """4+ fillable fields = form page."""
        snap = {
            "fillable": [
                {"type": "text"}, {"type": "text"},
                {"type": "textarea"}, {"type": "text"},
            ],
            "actionable": [{"tag": "button"}],
        }
        assert PageClassifier.classify(snap) == PageType.FORM_PAGE

    def test_classify_detail_page(self) -> None:
        """Few headings, few actions, no forms = detail page."""
        snap = {
            "headings": ["Product Name"],
            "actionable": [{"tag": "button"}, {"tag": "a"}],
            "fillable": [],
        }
        assert PageClassifier.classify(snap) == PageType.DETAIL_PAGE

    def test_classify_generic_page(self) -> None:
        """Everything else = generic."""
        snap = {
            "headings": ["A", "B", "C", "D"],
            "actionable": [{"tag": "a"} for _ in range(3)],
            "fillable": [{"type": "text"}],
        }
        assert PageClassifier.classify(snap) == PageType.GENERIC_PAGE

    def test_guidance_for_search_results(self) -> None:
        guidance = PageClassifier.guidance_for(PageType.SEARCH_RESULTS)
        assert "SEARCH RESULTS" in guidance
        assert "filters" in guidance.lower()

    def test_guidance_for_login(self) -> None:
        guidance = PageClassifier.guidance_for(PageType.LOGIN_PAGE)
        assert "LOGIN" in guidance
        assert "credentials" in guidance.lower()

    def test_guidance_for_generic_is_empty(self) -> None:
        assert PageClassifier.guidance_for(PageType.GENERIC_PAGE) == ""