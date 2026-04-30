"""Agent tools for LangGraph."""

from datetime import datetime
from typing import Any

import pytz
from langchain_core.tools import tool

from src.thirdhand.config import settings
from src.thirdhand.models import (
    InterestQueries,
    ReminderQueries,
    UserProfileQueries,
    get_session,
)


@tool
def create_reminder(
    title: str,
    remind_at: str,
    description: str = "",
) -> str:
    """Create a reminder for the current user.

    Args:
        title: Short title for the reminder.
        remind_at: When to send the reminder (ISO format or natural language).
        description: Optional detailed description.

    Returns:
        Confirmation message.
    """
    # This will be called from within the graph with injected user_id
    return f"Reminder '{title}' scheduled for {remind_at}"


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for information.

    Args:
        query: Search query.
        max_results: Maximum number of results to return.

    Returns:
        Formatted search results.
    """
    # TODO: Integrate Tavily/DuckDuckGo API
    return f"Search results for '{query}': (integration pending)"


@tool
def get_user_interests() -> str:
    """Get the current user's interests."""
    return "Interests: (will be populated from profile)"


@tool
def update_interests(topic: str, keywords: list[str] | None = None) -> str:
    """Add or update an interest for the current user.

    Args:
        topic: Topic name.
        keywords: Optional list of keywords.

    Returns:
        Confirmation message.
    """
    return f"Interest '{topic}' added/updated"


def get_all_tools() -> list:
    """Get all available tools."""
    return [create_reminder, web_search, get_user_interests, update_interests]
