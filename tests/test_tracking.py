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

    def test_check_progress_tool_error(self) -> None:
        t = BrowserTrackingState()
        result = t.check_progress("click", tool_failed=True, snapshot={})
        assert not result
        assert t.no_progress_streak == 1
        assert t.last_stuck_tool_name == "click"

    def test_check_progress_structural_change(self) -> None:
        t = BrowserTrackingState()
        t.last_structural_signature = t.structural_signature({"headings": ["A"], "text": "old"})
        result = t.check_progress(
            "click",
            tool_failed=False,
            snapshot={"headings": ["B"], "text": "new"},
            progress_changed=True,
        )
        assert result
        assert t.no_progress_streak == 0
        assert t.last_stuck_tool_name == ""  # reset on progress

    def test_check_progress_no_structural_change(self) -> None:
        t = BrowserTrackingState()
        t.last_structural_signature = t.structural_signature({"headings": ["A"], "text": "same"})
        result = t.check_progress(
            "click",
            tool_failed=False,
            snapshot={"headings": ["A"], "text": "same"},
            progress_changed=False,
        )
        assert not result
        assert t.no_progress_streak == 1
        assert t.last_stuck_tool_name == "click"

    def test_check_progress_cycle_detected(self) -> None:
        t = BrowserTrackingState()
        # Create a cycle: same action 3 times
        for _ in range(3):
            t.record_action("click", {"element_id": "th-x"}, {"headings": []})
        # Now check_progress should detect the cycle
        result = t.check_progress(
            "click",
            tool_failed=False,
            snapshot={"headings": ["B"]},  # structural change, but cycle overrides
            progress_changed=True,
        )
        assert not result
        assert t.no_progress_streak >= 1
        assert t.last_stuck_tool_name == "click"

    def test_last_stuck_tool_resets_on_progress(self) -> None:
        """After progress, last_stuck_tool_name must be cleared."""
        t = BrowserTrackingState()
        t.last_structural_signature = t.structural_signature({"headings": ["A"], "text": "old"})
        # First no-progress
        t.check_progress("type_text", tool_failed=False, snapshot={"headings": ["A"], "text": "old"}, progress_changed=False)
        assert t.last_stuck_tool_name == "type_text"
        # Then progress
        t.check_progress("click", tool_failed=False, snapshot={"headings": ["B"], "text": "new"}, progress_changed=True)
        assert t.last_stuck_tool_name == ""  # cleared

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

    def test_structural_signature_excludes_url(self) -> None:
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
        assert sig1 == sig2

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