"""Pydantic contracts shared between agent nodes and services."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskAnalysis(BaseModel):
    """Structured task analysis returned by the LLM."""

    intent: str = Field(
        description="The primary intent: 'reminder', 'chat', 'profile_update', 'search', or 'browser_task'"
    )
    title: str = Field(default="", description="Title for reminders")
    remind_at: str = Field(default="", description="When to remind (for reminders)")
    description: str = Field(default="", description="Reminder description")
    search_query: str = Field(default="", description="Search query")
    topic: str = Field(default="", description="Topic for interests")
    keywords: list[str] = Field(default_factory=list, description="Keywords for interests")
    browser_goal: str = Field(
        default="", description="Browser automation goal in the user's own words"
    )
    user_goal: str = Field(
        default="", description="Short normalized description of the user's actual goal."
    )
    requires_web_search: bool = Field(
        default=False,
        description="True when the assistant needs fresh information from the open web to answer well.",
    )
    requires_browser: bool = Field(
        default=False,
        description="True when the assistant needs to actively operate a website or browser UI.",
    )
    routing_reason: str = Field(
        default="",
        description="A short reason explaining why web search or browser control is required.",
    )
    required_context: list[str] = Field(
        default_factory=list,
        description="Required slots or context needed to complete the task, such as location, date, site, or item.",
    )
    missing_context: list[str] = Field(
        default_factory=list,
        description="Required context that is still missing after considering the user's message and saved profile.",
    )
    clarification_question: str = Field(
        default="",
        description="Ask this only if missing_context is non-empty and the assistant cannot proceed safely.",
    )
    response_needed: bool = Field(
        default=True,
        description="Whether a response to the user is needed",
    )


class ContextResolutionResult(BaseModel):
    """Result of deciding whether we can proceed or must clarify first."""

    requires_web_search: bool | None = None
    requires_browser: bool | None = None
    response_text: str = ""
    response_type: str = "text"
    ambiguous_request: bool | None = None


class SearchResult(BaseModel):
    """Normalized single search hit."""

    title: str = "Без названия"
    url: str = ""
    snippet: str = ""


class SearchProviderResponse(BaseModel):
    """Normalized response from an external web search provider."""

    answer: str = ""
    results: list[SearchResult] = Field(default_factory=list)


class SearchExecutionResult(BaseModel):
    """Validated result passed from search execution to downstream nodes."""

    search_results: list[SearchResult] = Field(default_factory=list)
    search_answer: str = ""
    search_query: str = ""
    response_text: str = ""
    response_type: str = "search_results"


class SearchEvidenceItem(BaseModel):
    """Compact search evidence kept for final synthesis."""

    title: str = "Без названия"
    url: str = ""
    snippet: str = ""


class SearchEvidencePack(BaseModel):
    """Selected evidence passed into final search answer synthesis."""

    search_query: str = ""
    answer_hint: str = ""
    evidence: list[SearchEvidenceItem] = Field(default_factory=list)


class PendingTask(BaseModel):
    """Persisted unresolved task awaiting more user input."""

    task_id: str = ""
    created_at: str = ""
    intent: str = "chat"
    user_goal: str = ""
    original_user_request: str = ""
    search_query: str = ""
    browser_goal: str = ""
    canonical_user_objective: str = Field(
        default="",
        description="Stable user intent for this browser task; survives resume without concatenated runtime text.",
    )
    requires_web_search: bool = False
    requires_browser: bool = False
    routing_reason: str = ""
    required_context: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    clarification_question: str = ""
    ambiguous_request: bool = False
    blocker_type: str = ""
    browser_final_url: str = ""
    awaiting_user_step: bool = False
    browser_debug_note: str = ""
    browser_auth_facts: dict[str, Any] = Field(default_factory=dict)
    browser_barrier_kind: str = ""
    browser_barrier_facts: dict[str, Any] = Field(default_factory=dict)
    browser_next_user_action: str = ""
    browser_resume_strategy: str = ""
    browser_sub_intent: str = ""
    browser_stop_reason: str = ""
