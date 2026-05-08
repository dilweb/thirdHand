"""Context builder service - combines profile, sessions, and history into prompt."""

from typing import Any

import structlog

from src.thirdhand.config import settings

logger = structlog.get_logger(__name__)


def estimate_tokens(text: str) -> int:
    """Rough estimate of tokens (1 token ≈ 4 chars for English, ~1.5 for Russian).

    Args:
        text: Text to estimate tokens for.

    Returns:
        Approximate token count.
    """
    # Conservative estimate: average 3 chars per token
    return max(1, len(text) // 3)


def format_profile_section(profile: dict[str, Any]) -> str:
    """Format context_summary into a readable section.

    Args:
        profile: The context_summary dict.

    Returns:
        Formatted string for the prompt.
    """
    lines = ["=== USER PROFILE ==="]

    # Identity
    identity = profile.get("identity", {})
    if identity:
        if identity.get("name"):
            lines.append(f"Name: {identity['name']}")
        if identity.get("occupation"):
            lines.append(f"Role: {identity['occupation']}")
        if identity.get("location"):
            lines.append(f"Location: {identity['location']}")

    # Tech stack
    stack = profile.get("tech_stack", [])
    if stack:
        lines.append(f"Stack: {', '.join(stack[:10])}")

    # Interests
    interests = profile.get("interests", [])
    if interests:
        if isinstance(interests[0], dict):
            topics = [i.get("topic", str(i)) for i in interests[:10]]
        else:
            topics = [str(i) for i in interests[:10]]
        lines.append(f"Interests: {', '.join(topics)}")

    # Preferences
    prefs = profile.get("preferences", {})
    if prefs:
        if prefs.get("communication_style"):
            lines.append(f"Style: {prefs['communication_style']}")
        if prefs.get("language"):
            lines.append(f"Language: {prefs['language']}")
        if prefs.get("timezone"):
            lines.append(f"Timezone: {prefs['timezone']}")

    # Patterns
    patterns = profile.get("patterns", {})
    if patterns.get("active_hours"):
        lines.append(f"Active hours: {', '.join(patterns['active_hours'])}")

    # Current project
    if profile.get("current_project"):
        lines.append(f"Current project: {profile['current_project']}")

    return "\n".join(lines) if len(lines) > 1 else ""


def format_sessions_section(sessions: list[dict[str, Any]], max_items: int = 5) -> str:
    """Format session summaries into a readable section.

    Args:
        sessions: List of session summary dicts.
        max_items: Maximum number of sessions to include.

    Returns:
        Formatted string for the prompt.
    """
    if not sessions:
        return ""

    lines = ["=== RECENT SESSIONS ==="]
    for s in sessions[-max_items:]:
        date = s.get("date", "unknown")
        topics = s.get("topics", [])
        actions = s.get("actions_taken", [])

        line = f"{date}: {', '.join(topics[:3])}"
        if actions:
            line += f" → {', '.join(actions[:2])}"
        lines.append(line)

    return "\n".join(lines)


def format_history_section(history: list[dict[str, Any]], max_messages: int = 10) -> str:
    """Format conversation history into a readable section.

    Args:
        history: List of message dicts with role and content.
        max_messages: Maximum number of messages to include.

    Returns:
        Formatted string for the prompt.
    """
    if not history:
        return ""

    lines = ["=== CONVERSATION HISTORY ==="]
    for msg in history[-max_messages:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:200]  # Truncate long messages
        lines.append(f"{role}: {content}")

    return "\n".join(lines)


def build_context_prompt(
    profile: dict[str, Any] | None = None,
    session_summaries: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Build the full context prompt for LLM injection.

    Args:
        profile: context_summary from user_profiles.
        session_summaries: session_summaries array from user_profiles.
        history: Recent messages from Redis.

    Returns:
        Full context string to inject into the system prompt.
    """
    sections = []

    # Base system message
    sections.append("Ты — thirdHand, персональный AI-ассистент пользователя.")

    # Profile section
    if profile:
        profile_text = format_profile_section(profile)
        if profile_text:
            sections.append(profile_text)

    # Session summaries
    if session_summaries:
        sessions_text = format_sessions_section(session_summaries)
        if sessions_text:
            sections.append(sessions_text)

    # Conversation history
    if history:
        history_text = format_history_section(history)
        if history_text:
            sections.append(history_text)

    result = "\n\n".join(sections)
    tokens = estimate_tokens(result)

    logger.debug(
        "context_built",
        sections=len(sections),
        estimated_tokens=tokens,
        has_profile=bool(profile),
        has_sessions=bool(session_summaries),
        has_history=bool(history),
    )

    return result


def compress_if_needed(
    profile: dict[str, Any],
    session_summaries: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compress context if it exceeds token limits.

    Args:
        profile: context_summary.
        session_summaries: session summaries array.
        history: Recent messages.

    Returns:
        (profile, session_summaries, history) — possibly compressed.
    """
    max_sessions = settings.MAX_SESSION_SUMMARIES
    max_history = settings.MAX_HISTORY_MESSAGES

    # Trim session summaries
    if len(session_summaries) > max_sessions:
        # Keep last max_sessions, summarize the rest into profile
        old_sessions = session_summaries[:-max_sessions]
        session_summaries = session_summaries[-max_sessions:]

        # Merge old session facts into profile
        for s in old_sessions:
            for topic in s.get("topics", []):
                if "interests" not in profile:
                    profile["interests"] = []
                if topic not in profile["interests"]:
                    profile["interests"].append(topic)

    # Trim history
    if len(history) > max_history:
        history = history[-max_history:]

    return profile, session_summaries, history
