"""Unified tracking state for the browser agent loop.

Tracks runtime counters, page classification, and action history.
Validation logic moved to ``RuntimeValidator`` (see ``validator.py``).
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
    keeps the scalar counters that drive escalation logic.

    **No longer has a ``check_progress()`` method** — use
    ``RuntimeValidator.validate()`` from ``validator.py`` instead.
    """

    # -- cycle detection ---------------------------------------------------
    cycle_detector: CycleDetector = field(default_factory=CycleDetector)

    # -- progress counters -------------------------------------------------
    no_progress_streak: int = 0
    no_tool_steps: int = 0
    visual_assist_same_page_streak: int = 0

    # -- multi-item tracking -----------------------------------------------
    items_total: int = 0
    items_completed: int = 0

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

    def increment_completed(self) -> int:
        """Increment the completed-items counter. Returns the new count."""
        self.items_completed += 1
        return self.items_completed

    def progress_summary(self) -> str:
        """Return a compact progress string for injection into the system prompt."""
        if self.items_total <= 0:
            return ""
        return f"Прогресс: выполнено {self.items_completed}/{self.items_total}"

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