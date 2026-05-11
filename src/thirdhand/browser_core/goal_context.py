"""Sanitize and compose browser task goals for the new browser core."""

from __future__ import annotations

_CONTINUE_MARKERS = (
    "\n\nContinue the same browser task",
    "\nContinue the same browser task",
)


def strip_continuation_slab(text: str) -> str:
    """Remove appended continuation blocks and everything after them."""
    if not text:
        return ""
    raw = text
    for marker in _CONTINUE_MARKERS:
        if marker in raw:
            raw = raw.split(marker)[0]
    return raw.strip()


def derive_canonical_objective_from_pending(pending: dict) -> str:
    """Pick a stable high-level objective from pending task data."""
    canon = str(pending.get("canonical_user_objective") or "").strip()
    if canon:
        return canon
    for key in ("original_user_request", "user_goal"):
        value = str(pending.get(key) or "").strip()
        if value and "Continue the same browser task" not in value:
            return value
    browser_goal = str(pending.get("browser_goal") or "").strip()
    return strip_continuation_slab(browser_goal)


def truncate_display_title(text: str, *, max_chars: int = 240) -> str:
    """Short single-line title for Telegram and logs."""
    if not text:
        return ""
    one_line = " ".join(text.split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1].rstrip() + "…"


def build_operational_browser_goal(
    *,
    canonical_objective: str,
    latest_user_message: str,
    resume_url: str = "",
) -> str:
    """Build a compact instruction bundle for the browser agent."""
    url_part = (
        f"Resume tab / last known URL: {resume_url.strip()}\n"
        if (resume_url or "").strip()
        else "Continue from the live page in the session (see snapshots).\n"
    )
    return (
        "USER_OBJECTIVE (stable high-level intent):\n"
        f"{canonical_objective.strip() or '(not specified)'}\n\n"
        "LATEST_USER_MESSAGE (if this conflicts with older wording, trust this and the live page):\n"
        f"{latest_user_message.strip() or '(none)'}\n\n"
        f"{url_part}\n"
        "Rules: inspect_page and the current viewport are ground truth. Earlier assistant steps or "
        "wording may be wrong. Do not assume SMS/OTP/password unless the visible page clearly asks for it."
    )
