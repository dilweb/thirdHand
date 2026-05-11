"""Unified tracking state for the browser agent loop.

Replaces the individual scalar variables that were scattered across
``agent_loop.py`` with a single dataclass that also incorporates the
``CycleDetector`` and ``PageClassifier``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.thirdhand.browser_core.cycle_detector import CycleDetector
from src.thirdhand.browser_core.page_classifier import PageClassifier, PageType


@dataclass
class BrowserTrackingState:
    """Aggregate runtime state for one browser-agent loop invocation.

    Owns the ``CycleDetector``, caches the current ``page_type``, and
    keeps the scalar counters that drive the no-progress escalation logic.
    """

    # -- cycle detection ---------------------------------------------------
    cycle_detector: CycleDetector = field(default_factory=CycleDetector)

    # -- progress counters -------------------------------------------------
    no_progress_streak: int = 0
    no_tool_steps: int = 0
    visual_assist_same_page_streak: int = 0

    # -- last-action bookkeeping -------------------------------------------
    last_action_signature: str = ""
    last_visual_signature: str = ""
    last_stuck_tool_name: str = ""
    visual_assist_called_during_stuck: bool = False

    # -- page awareness ----------------------------------------------------
    page_type: PageType = PageType.GENERIC_PAGE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_action(
        self,
        tool_name: str,
        args: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        """Record one browser action and update internal state."""
        canonical_args = self._canonical_args(tool_name, args)
        structural_sig = self.cycle_detector.structural_signature(snapshot)
        self.cycle_detector.record_action(tool_name, canonical_args, structural_sig)

    def is_cycling(self) -> bool:
        """Delegate to the underlying CycleDetector."""
        return self.cycle_detector.is_cycling()

    def structural_signature(self, snapshot: dict[str, Any]) -> str:
        """Structural signature that excludes URL (delegate)."""
        return self.cycle_detector.structural_signature(snapshot)

    def classify_page(self, snapshot: dict[str, Any]) -> PageType:
        """Classify and cache the current page type."""
        self.page_type = PageClassifier.classify(snapshot)
        return self.page_type

    def check_progress(
        self,
        tool_name: str,
        tool_failed: bool,
        snapshot: dict[str, Any],
        progress_changed: bool = False,
    ) -> bool:
        """Check whether the last action made real progress.

        Parameters
        ----------
        tool_name:
            Name of the tool that was just executed.
        tool_failed:
            Whether the tool raised an error.
        snapshot:
            Current page snapshot (after the action).
        progress_changed:
            Whether the structural signature changed compared to the
            previous step.  Must be pre-computed by the caller to avoid
            the "compare-with-self" bug.

        Returns ``True`` if progress was made (resets the no-progress
        streak).  Returns ``False`` if the action should count as
        "no progress".
        """
        # A tool error is never progress
        if tool_failed:
            self.no_progress_streak += 1
            if self.no_progress_streak == 1:
                self.last_stuck_tool_name = tool_name
            return False

        # Observation tools that didn't change the page structure → no progress
        if tool_name in {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}:
            if not progress_changed:
                self.no_progress_streak += 1
                if self.no_progress_streak == 1:
                    self.last_stuck_tool_name = tool_name
                return False

        # Cycling → no progress regardless of structural changes
        if self.is_cycling():
            self.no_progress_streak += 1
            if self.no_progress_streak == 1:
                self.last_stuck_tool_name = tool_name
            return False

        # Real progress — reset everything
        self.no_progress_streak = 0
        self.last_stuck_tool_name = ""
        self.visual_assist_called_during_stuck = False
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_args(tool_name: str, args: dict[str, Any]) -> str:
        """Normalise arguments so that the same logical action produces the
        same canonical string even if minor fields differ."""
        if tool_name == "click":
            return json.dumps(
                {"element_id": args.get("element_id", ""), "text": args.get("text", "")},
                ensure_ascii=False,
                sort_keys=True,
            )
        if tool_name == "type_text":
            return json.dumps(
                {
                    "element_id": args.get("element_id", ""),
                    "label": args.get("label", ""),
                    "placeholder": args.get("placeholder", ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        # For other tools, use the full args
        return json.dumps(args, ensure_ascii=False, sort_keys=True)

    # Make last_structural_signature a property-like field so it can be
    # set from outside without breaking the dataclass pattern.
    last_structural_signature: str = ""

    def update_structural_signature(self, snapshot: dict[str, Any]) -> str:
        """Compute and store the current structural signature, return it."""
        sig = self.structural_signature(snapshot)
        self.last_structural_signature = sig
        return sig