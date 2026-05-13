"""Tests for the RuntimeValidator."""

from src.thirdhand.browser_core.cycle_detector import CycleDetector
from src.thirdhand.browser_core.validator import RuntimeValidator, ValidationVerdict


class _HelperCycleDetector:
    """Helper to create a CycleDetector pre-populated with actions."""

    @staticmethod
    def make(snapshot_count: int = 0) -> CycleDetector:
        cd = CycleDetector()
        for i in range(snapshot_count):
            sig = cd.structural_signature({"headings": [f"h{i}"], "text": f"t{i}"})
            cd.record_action("click", f"action-{i}", sig)
        return cd


class TestValidateToolError:
    def test_tool_error_is_no_progress(self) -> None:
        cd = _HelperCycleDetector.make(2)
        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=True,
            snapshot={},
            previous_signature="",
            cycle_detector=cd,
        )
        assert not verdict.progress_made
        assert verdict.reason == "tool_error"

    def test_tool_error_sets_correct_signature(self) -> None:
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["test"], "text": "hello"}
        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=True,
            snapshot=snapshot,
            previous_signature="old_sig",
            cycle_detector=cd,
        )
        # Even on error, the structural signature should be computed
        assert verdict.structural_signature
        assert verdict.structural_signature != "old_sig"


class TestValidateStructuralChange:
    def test_structural_change_is_progress(self) -> None:
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["new"], "text": "changed", "fillable": [], "actionable": []}
        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=False,
            snapshot=snapshot,
            previous_signature="old_sig",
            cycle_detector=cd,
        )
        assert verdict.progress_made
        assert verdict.reason == "progress"

    def test_no_structural_change_is_no_progress(self) -> None:
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["same"], "text": "same"}
        same_sig = cd.structural_signature(snapshot)
        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=False,
            snapshot=snapshot,
            previous_signature=same_sig,
            cycle_detector=cd,
        )
        assert not verdict.progress_made
        assert verdict.reason == "no_structural_change"

    def test_non_observation_tool_ignores_structural_change(self) -> None:
        """Non-observation tools (like inspect_page) don't check structure."""
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["same"], "text": "same"}
        same_sig = cd.structural_signature(snapshot)
        verdict = RuntimeValidator.validate(
            tool_name="inspect_page",
            tool_failed=False,
            snapshot=snapshot,
            previous_signature=same_sig,
            cycle_detector=cd,
        )
        # inspect_page is not in OBSERVATION_TOOLS, so structural check is skipped
        # and the snapshot hasn't changed, so no cycle either → progress
        assert verdict.progress_made
        assert verdict.reason == "progress"

    def test_scroll_without_change_is_no_progress(self) -> None:
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["same"], "text": "same"}
        same_sig = cd.structural_signature(snapshot)
        verdict = RuntimeValidator.validate(
            tool_name="scroll",
            tool_failed=False,
            snapshot=snapshot,
            previous_signature=same_sig,
            cycle_detector=cd,
        )
        assert not verdict.progress_made
        assert verdict.reason == "no_structural_change"


class TestValidateCycleDetection:
    def test_cycle_is_no_progress(self) -> None:
        cd = CycleDetector()
        sig_a = cd.structural_signature({"headings": ["a"], "text": "a"})
        sig_b = cd.structural_signature({"headings": ["b"], "text": "b"})
        # Create a toggle cycle: A → B → A → B
        cd.record_action("click", '{"element_id":"th-a"}', sig_a)
        cd.record_action("click", '{"element_id":"th-b"}', sig_b)
        cd.record_action("click", '{"element_id":"th-a"}', sig_a)
        cd.record_action("click", '{"element_id":"th-b"}', sig_b)
        assert cd.is_cycling()

        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=False,
            snapshot={"headings": ["c"], "text": "c"},
            previous_signature="some_other_sig",
            cycle_detector=cd,
        )
        assert not verdict.progress_made
        assert verdict.cycle_detected
        assert verdict.reason == "cycle_detected"

    def test_no_cycle_normal_execution(self) -> None:
        cd = _HelperCycleDetector.make(3)
        assert not cd.is_cycling()

        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=False,
            snapshot={"headings": ["new"], "text": "new"},
            previous_signature="old",
            cycle_detector=cd,
        )
        assert verdict.progress_made
        assert not verdict.cycle_detected


class TestValidationVerdict:
    def test_default_values(self) -> None:
        v = ValidationVerdict()
        assert not v.progress_made
        assert not v.cycle_detected
        assert v.structural_signature == ""
        assert v.reason == ""

    def test_custom_values(self) -> None:
        v = ValidationVerdict(
            progress_made=True,
            cycle_detected=False,
            structural_signature="sig123",
            reason="progress",
        )
        assert v.progress_made
        assert not v.cycle_detected
        assert v.structural_signature == "sig123"
        assert v.reason == "progress"


class TestValidateStructuralSignature:
    def test_signature_computed_correctly(self) -> None:
        cd = _HelperCycleDetector.make(2)
        snapshot = {"headings": ["test"], "text": "content"}
        expected_sig = cd.structural_signature(snapshot)

        verdict = RuntimeValidator.validate(
            tool_name="click",
            tool_failed=False,
            snapshot=snapshot,
            previous_signature="old",
            cycle_detector=cd,
        )
        assert verdict.structural_signature == expected_sig

    def test_signature_includes_url(self) -> None:
        """Structural signature should contain URL (different pages)."""
        cd = _HelperCycleDetector.make(2)
        snapshot1 = {"headings": ["test"], "text": "content", "url": "https://a.com"}
        snapshot2 = {"headings": ["test"], "text": "content", "url": "https://b.com"}

        sig1 = RuntimeValidator.validate(
            tool_name="click", tool_failed=False, snapshot=snapshot1,
            previous_signature="old", cycle_detector=cd,
        ).structural_signature
        sig2 = RuntimeValidator.validate(
            tool_name="click", tool_failed=False, snapshot=snapshot2,
            previous_signature=sig1, cycle_detector=cd,
        ).structural_signature

        # Same structure, different URLs → different signatures
        assert sig1 != sig2