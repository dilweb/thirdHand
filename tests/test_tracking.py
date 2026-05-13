"""Tests for BrowserTrackingState — unified tracking state."""

from src.thirdhand.browser_core.tracking import BrowserTrackingState


class TestBrowserTrackingState:
    def test_initial_state(self) -> None:
        t = BrowserTrackingState()
        assert t.no_progress_streak == 0
        assert t.no_tool_steps == 0
        assert t.page_type.value == "generic_page"
        assert not t.is_cycling()
        assert t.last_stuck_tool_name == ""

    def test_record_action_updates_history(self) -> None:
        t = BrowserTrackingState()
        t.record_action("click", {"element_id": "th-x"}, {"headings": [], "text": ""})
        assert len(t.cycle_detector.action_history) == 1
        assert len(t.cycle_detector.structural_history) == 1

    def test_classify_page_updates_page_type(self) -> None:
        t = BrowserTrackingState()
        snap = {
            "fillable": [
                {"type": "text"}, {"type": "password"},
            ],
            "actionable": [{"tag": "button"}],
        }
        t.classify_page(snap)
        assert t.page_type.value == "login_page"

    def test_structural_signature_includes_url(self) -> None:
        t = BrowserTrackingState()
        sig1 = t.structural_signature({
            "url": "https://a.com",
            "headings": ["X"],
            "text": "hello",
        })
        sig2 = t.structural_signature({
            "url": "https://b.com",
            "headings": ["X"],
            "text": "hello",
        })
        assert sig1 != sig2

    def test_canonical_args_click(self) -> None:
        args = {"element_id": "th-abc", "text": "Submit", "exact": True}
        canonical = BrowserTrackingState._canonical_args("click", args)
        assert "th-abc" in canonical
        assert "Submit" in canonical

    def test_canonical_args_type_text(self) -> None:
        args = {"element_id": "th-abc", "label": "Имя", "placeholder": "", "text": "John"}
        canonical = BrowserTrackingState._canonical_args("type_text", args)
        assert "th-abc" in canonical
        assert "Имя" in canonical
        # 'text' value should NOT be in canonical (it's the value to type, not a locator)
        assert "John" not in canonical