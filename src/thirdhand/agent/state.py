"""Agent state definition for LangGraph."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:
    """State for the main agent graph.

    This state flows through all nodes in the graph.
    Each node reads and updates specific fields.
    """

    # === Input (from bot handler) ===
    user_id: int = 0
    message_text: str = ""

    # === Parsed data (from parse_input node) ===
    intent: str = "chat"  # "reminder" | "search" | "chat" | "profile_update"
    entities: dict[str, Any] = field(default_factory=dict)

    # === Reminder flow ===
    reminder_id: int | None = None
    reminder_title: str = ""
    reminder_datetime: str = ""
    reminder_description: str = ""

    # === Search flow ===
    search_query: str = ""
    search_results: list[dict[str, Any]] = field(default_factory=list)

    # === Profile flow ===
    profile_updates: dict[str, Any] = field(default_factory=dict)

    # === Output (to bot handler) ===
    response_text: str = ""
    response_type: str = "text"  # "text" | "reminder_confirm" | "search_results" | "error"

    # === Context ===
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    user_profile: dict[str, Any] = field(default_factory=dict)
