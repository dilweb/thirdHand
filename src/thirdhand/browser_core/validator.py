"""Stateless runtime validator for the browser agent.

Replaces the ``check_progress()`` method that was previously mixed into
``BrowserTrackingState``.  The validator computes whether an action made
real progress without mutating any state — the caller is responsible for
updating counters based on the returned verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.thirdhand.browser_core.cycle_detector import CycleDetector


# Tools that change the page state (observation tools).
_OBSERVATION_TOOLS: frozenset[str] = frozenset(
    {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}
)


@dataclass
class ValidationVerdict:
    """Pure validation result — no state mutations, just facts."""

    # Whether the last action made real progress toward the goal.
    progress_made: bool = False
    # Whether a behavioural cycle was detected.
    cycle_detected: bool = False
    # Structural signature of the current page (computed by the validator).
    structural_signature: str = ""
    # Machine-readable reason string for logging / debugging.
    reason: str = ""


class RuntimeValidator:
    """Stateless validator that checks whether a browser action made progress.

    Typical usage::

        verdict = RuntimeValidator.validate(
            tool_name=tool_name,
            tool_failed=tool_failed,
            snapshot=latest_snapshot,
            previous_signature=previous_sig,
            cycle_detector=tracking.cycle_detector,
        )
        # Caller manages counters based on verdict:
        if not verdict.progress_made:
            tracking.no_progress_streak += 1
        else:
            tracking.no_progress_streak = 0
    """

    @staticmethod
    def validate(
        *,
        tool_name: str,
        tool_failed: bool,
        snapshot: dict[str, Any],
        previous_signature: str,
        cycle_detector: CycleDetector,
    ) -> ValidationVerdict:
        """Determine whether the last action made real progress.

        Parameters
        ----------
        tool_name:
            Name of the tool that was just executed.
        tool_failed:
            Whether the tool raised an error.
        snapshot:
            Current page snapshot (after the action).
        previous_signature:
            Structural signature from the **previous** step (pre-computed
            and stored by the caller).  Used to detect structural changes.
        cycle_detector:
            Shared ``CycleDetector`` instance (already populated with
            action history).

        Returns
        -------
        ``ValidationVerdict`` — a pure fact record with no side effects.
        """
        # Compute the new structural signature
        new_signature = cycle_detector.structural_signature(snapshot)

        # 1. Tool error is never progress
        if tool_failed:
            return ValidationVerdict(
                progress_made=False,
                cycle_detected=False,
                structural_signature=new_signature,
                reason="tool_error",
            )

        # 2. Observation tools that didn't change the page → no progress
        if tool_name in _OBSERVATION_TOOLS:
            progress_changed = new_signature != previous_signature
            if not progress_changed:
                return ValidationVerdict(
                    progress_made=False,
                    cycle_detected=False,
                    structural_signature=new_signature,
                    reason="no_structural_change",
                )

        # 3. Cycling → no progress regardless of structural changes
        if cycle_detector.is_cycling():
            return ValidationVerdict(
                progress_made=False,
                cycle_detected=True,
                structural_signature=new_signature,
                reason="cycle_detected",
            )

        # 4. Real progress
        return ValidationVerdict(
            progress_made=True,
            cycle_detected=False,
            structural_signature=new_signature,
            reason="progress",
        )