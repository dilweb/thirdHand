"""Tests for CycleDetector — universal behavioural cycle detection."""

from src.thirdhand.browser_core.cycle_detector import CycleDetector


class TestCycleDetector:
    def test_initial_state_not_cycling(self) -> None:
        d = CycleDetector()
        assert not d.is_cycling()

    def test_detects_same_action_repeated(self) -> None:
        d = CycleDetector()
        for _ in range(3):
            d.record_action("click", '{"element_id":"th-x"}', "sig1")
        assert d.is_cycling()

    def test_two_actions_not_enough_for_repeat(self) -> None:
        d = CycleDetector()
        d.record_action("click", '{"element_id":"th-x"}', "sig1")
        d.record_action("click", '{"element_id":"th-x"}', "sig2")
        assert not d.is_cycling()

    def test_detects_toggle_cycle(self) -> None:
        d = CycleDetector()
        d.record_action("click", '{"element_id":"th-a"}', "sig1")
        d.record_action("click", '{"element_id":"th-b"}', "sig2")
        d.record_action("click", '{"element_id":"th-a"}', "sig3")
        d.record_action("click", '{"element_id":"th-b"}', "sig4")
        assert d.is_cycling()

    def test_no_false_positive_on_progress(self) -> None:
        d = CycleDetector()
        d.record_action("click", "a", "sig1")
        d.record_action("type_text", "b", "sig2")
        d.record_action("click", "c", "sig3")
        assert not d.is_cycling()

    def test_structural_stagnation_with_same_action(self) -> None:
        """Structural stagnation + same action = cycle."""
        d = CycleDetector()
        stagnant_sig = '{"headings":[],"dialogs":[],"fillable_count":0,"actionable_count":0,"text_hash":123}'
        for _ in range(3):
            d.record_action("click", '{"element_id":"th-x"}', stagnant_sig)
        assert d.is_cycling()

    def test_structural_stagnation_with_different_actions(self) -> None:
        """Structural stagnation alone is NOT a cycle — agent may be clicking
        different items on a list page that looks the same."""
        d = CycleDetector()
        stagnant_sig = '{"headings":[],"dialogs":[],"fillable_count":0,"actionable_count":0,"text_hash":123}'
        for i in range(3):
            d.record_action("click", f'{{"element_id":"th-{i}"}}', stagnant_sig)
        assert not d.is_cycling()

    def test_structural_change_is_not_stagnation(self) -> None:
        d = CycleDetector()
        for i in range(3):
            d.record_action(
                "click",
                f'{{"element_id":"th-{i}"}}',
                f'{{"headings":[],"text_hash":{i}}}',
            )
        assert not d.is_cycling()

    def test_mixed_actions_no_cycle(self) -> None:
        d = CycleDetector()
        actions = [
            ("click", "a", "s1"),
            ("type_text", "b", "s2"),
            ("scroll", "c", "s3"),
            ("click", "d", "s4"),
            ("wait", "e", "s5"),
        ]
        for tool, args, sig in actions:
            d.record_action(tool, args, sig)
        assert not d.is_cycling()

    def test_structural_signature_includes_url(self) -> None:
        """URL changes MUST affect structural signature (different pages)."""
        sig1 = CycleDetector.structural_signature({
            "url": "https://example.com/page1",
            "headings": ["Results"],
            "fillable": [],
            "actionable": [{"tag": "a"}],
            "text": "hello world",
        })
        sig2 = CycleDetector.structural_signature({
            "url": "https://example.com/page2?filter=on",
            "headings": ["Results"],
            "fillable": [],
            "actionable": [{"tag": "a"}],
            "text": "hello world",
        })
        assert sig1 != sig2, "URL change must change structural signature"

    def test_structural_signature_changes_on_content(self) -> None:
        sig1 = CycleDetector.structural_signature({
            "headings": ["Results"],
            "text": "page one",
        })
        sig2 = CycleDetector.structural_signature({
            "headings": ["Results", "More"],
            "text": "page two",
        })
        assert sig1 != sig2