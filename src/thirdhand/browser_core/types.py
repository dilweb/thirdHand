"""Shared neutral data types for the new browser core."""

from __future__ import annotations

from typing import Any, TypedDict


class ElementSnapshot(TypedDict, total=False):
    """Neutral snapshot of one visible interactive or fillable element."""

    id: str
    tag: str
    role: str
    type: str
    text: str
    name: str
    placeholder: str
    label: str
    href: str
    html_id: str
    autocomplete: str
    value_preview: str
    fillable: bool
    disabled: bool
    checked: bool
    selected: bool
    expanded: bool
    modal: bool
    locator_hint: str


class PageSnapshot(TypedDict, total=False):
    """Neutral snapshot of the current page."""

    title: str
    url: str
    headings: list[str]
    text: str
    dialogs: list[str]
    actionable: list[ElementSnapshot]
    fillable: list[ElementSnapshot]
    elements: list[ElementSnapshot]
    metadata: dict[str, Any]


class SessionProbe(TypedDict, total=False):
    """Small runtime diagnostic payload."""

    title: str
    url: str
    cookie_count: int
    cookie_domains: list[str]
    body_text_preview: str
    interactive_texts: list[str]
