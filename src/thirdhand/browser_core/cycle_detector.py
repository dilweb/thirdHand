"""Universal cycle detector for browser agent progress tracking.

Detects behavioral cycles across any website using only structural
page signatures (no URL, no language-specific keywords).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any


def _deque_last_n(d: deque, n: int) -> list:
    """Return the last *n* items of a deque as a list.

    ``deque`` does not support slice notation, so we convert manually.
    """
    if n <= 0:
        return []
    total = len(d)
    if total == 0:
        return []
    start = max(0, total - n)
    return [d[i] for i in range(start, total)]


@dataclass
class CycleDetector:
    """Detects when the browser agent is repeating actions without real progress.

    Three detection patterns:
    1. **Repeat**: Same tool + same canonical args 3+ times in a row.
    2. **Toggle cycle**: A→B→A→B pattern in the last 4 actions.
    3. **Structural stagnation**: Structural signature unchanged for 3+ steps
       (even if URL keeps changing — e.g. toggling a filter).
    """

    structural_history: deque[dict] = field(
        default_factory=lambda: deque(maxlen=5)
    )
    action_history: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=6)
    )

    @staticmethod
    def structural_signature(snapshot: dict[str, Any]) -> str:
        """Stable structural signature that excludes URL.

        Only uses page structure: headings, dialogs, element counts,
        a text hash, and whether a modal/dialog is open.
        A filter toggle that only changes the URL will NOT change this
        signature, but opening/closing a dialog overlay will.
        """
        dialogs = [str(d) for d in (snapshot.get("dialogs") or [])[:3]]
        data = {
            "headings": [str(h) for h in (snapshot.get("headings") or [])[:4]],
            "dialogs": dialogs,
            "fillable_count": len(snapshot.get("fillable") or []),
            "actionable_count": len(snapshot.get("actionable") or []),
            "text_hash": hash((snapshot.get("text") or "")[:1000]),
            # Tracks opening/closing of modal overlays on SPAs — these don't
            # change actionable_count or text_hash but ARE meaningful progress.
            "modal_open": len(dialogs) > 0,
        }
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    def record_action(
        self,
        tool_name: str,
        canonical_args: str,
        structural_sig: str,
    ) -> None:
        """Record one browser action together with the structural signature."""
        self.action_history.append((tool_name, canonical_args))
        self.structural_history.append(structural_sig)

    def is_cycling(self) -> bool:
        """Return True when a behavioural cycle is detected."""
        # Pattern 1: same action repeated 3+ times
        if self._same_action_repeated(threshold=3):
            return True

        # Pattern 2: toggle cycle A→B→A→B
        if self._toggle_cycle_detected():
            return True

        # Pattern 3: structural stagnation (page structure unchanged)
        if self._structural_stagnation(threshold=3):
            return True

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _same_action_repeated(self, threshold: int = 3) -> bool:
        if len(self.action_history) < threshold:
            return False
        last_actions = _deque_last_n(self.action_history, threshold)
        # Compare full (tool_name, canonical_args) tuple
        actions_normalised = [f"{tool}:{args}" for tool, args in last_actions]
        return len(set(actions_normalised)) == 1

    def _toggle_cycle_detected(self) -> bool:
        if len(self.action_history) < 4:
            return False
        last_4 = _deque_last_n(self.action_history, 4)
        actions_normalised = [f"{tool}:{args}" for tool, args in last_4]
        # A→B→A→B  where A != B
        return (
            actions_normalised[0] == actions_normalised[2]
            and actions_normalised[1] == actions_normalised[3]
            and actions_normalised[0] != actions_normalised[1]
        )

    def _structural_stagnation(self, threshold: int = 3) -> bool:
        if len(self.structural_history) < threshold:
            return False
        recent = _deque_last_n(self.structural_history, threshold)
        unique = {json.dumps(s, sort_keys=True) for s in recent}
        if len(unique) != 1:
            return False
        # Structural stagnation alone is NOT enough — the agent might be
        # clicking different items on a list page that looks the same
        # (e.g. clicking "Откликнуться" on different vacancies).
        # Only flag as a cycle if the actions are ALSO the same.
        recent_actions = _deque_last_n(self.action_history, threshold)
        actions_normalised = [f"{tool}:{args}" for tool, args in recent_actions]
        return len(set(actions_normalised)) == 1