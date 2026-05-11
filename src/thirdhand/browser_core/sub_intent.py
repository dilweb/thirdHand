"""Neutral browser sub-intent definitions shared across browser entrypoints."""

from __future__ import annotations

from enum import Enum


class BrowserSubIntent(str, Enum):
    """Internal browser sub-goal for broad execution mode."""

    DISCOVER_CANDIDATES = "browser_discover_candidates"
    SELECT_TARGETS = "browser_select_targets"
    APPLY_TO_TARGETS = "browser_apply_to_targets"


def infer_browser_sub_intent(_goal: str) -> BrowserSubIntent:
    """Default sub-intent when nothing more specific was persisted by the graph."""
    return BrowserSubIntent.APPLY_TO_TARGETS


def resolve_browser_sub_intent(initial: str | None) -> BrowserSubIntent:
    """Prefer a persisted / graph-provided sub-intent when it is valid."""
    raw = (initial or "").strip()
    if raw:
        try:
            return BrowserSubIntent(raw)
        except ValueError:
            pass
    return BrowserSubIntent.APPLY_TO_TARGETS


def sub_intent_execution_brief(sub: BrowserSubIntent) -> str:
    """Stable execution summary for prompts and tool descriptions."""
    if sub is BrowserSubIntent.DISCOVER_CANDIDATES:
        return (
            "Mode: DISCOVERY — gather and summarize matching listings or options from the site.\n"
            "- Prefer search/listing pages, filters, and pagination until you have a useful candidate set.\n"
            "- Use login only if the visible listing data truly requires it.\n"
            "- Do NOT submit applications, send résumés, or finalize purchases.\n"
        )
    if sub is BrowserSubIntent.SELECT_TARGETS:
        return (
            "Mode: SELECTION — choose from options already visible or after minimal navigation.\n"
            "- Compare visible options carefully before wandering away.\n"
            "- Finish with the selected option(s), not with a fake application outcome.\n"
        )
    return (
        "Mode: APPLY / ACT — complete the user’s requested action end-to-end.\n"
        "- Follow through with the live UI until the requested action is done or real user help is required.\n"
    )


def sub_intent_user_task_message(goal: str, sub: BrowserSubIntent) -> str:
    """Compact user-task line for the browser model."""
    mode_line = {
        BrowserSubIntent.DISCOVER_CANDIDATES: (
            "Режим: только поиск и сбор кандидатов — без откликов и отправки заявок."
        ),
        BrowserSubIntent.SELECT_TARGETS: (
            "Режим: выбор из уже видимых вариантов; минимум лишней навигации."
        ),
        BrowserSubIntent.APPLY_TO_TARGETS: (
            "Режим: выполнить действие до конца."
        ),
    }[sub]
    return (
        "Выполни задачу в браузере максимально автономно.\n"
        f"{mode_line}\n"
        f"Задача пользователя: {goal}"
    )
