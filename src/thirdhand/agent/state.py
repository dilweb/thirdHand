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
    intent: str = "chat"  # "reminder" | "search" | "chat" | "profile_update" | "browser_task"
    entities: dict[str, Any] = field(default_factory=dict)
    requires_web_search: bool = False
    requires_browser: bool = False
    routing_reason: str = ""
    user_goal: str = ""
    required_context: list[str] = field(default_factory=list)
    missing_context: list[str] = field(default_factory=list)
    clarification_question: str = ""

    # === Reminder flow ===
    reminder_id: int | None = None
    reminder_title: str = ""
    reminder_datetime: str = ""
    reminder_description: str = ""

    # === Search flow ===
    search_query: str = ""
    search_results: list[dict[str, Any]] = field(default_factory=list)
    search_answer: str = ""

    # === Profile flow ===
    profile_updates: dict[str, Any] = field(default_factory=dict)

    # === Browser automation flow ===
    browser_goal: str = ""
    browser_trace: list[str] = field(default_factory=list)
    browser_final_url: str = ""
    browser_needs_user_input: bool = False

    # === Output (to bot handler) ===
    response_text: str = ""
    response_type: str = "text"  # "text" | "reminder_confirm" | "search_results" | "error"

    # === Context ===
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    user_profile: dict[str, Any] = field(default_factory=dict)
