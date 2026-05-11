"""Language-agnostic page classifier for the browser agent.

Classifies a page by its *structural* HTML properties only —
no natural-language keywords, no site-specific heuristics.
Works identically on Russian, English, Spanish, Japanese, … sites.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class PageType(str, Enum):
    """Structural page type detected from the DOM snapshot."""

    SEARCH_RESULTS = "search_results"
    DETAIL_PAGE = "detail_page"
    FORM_PAGE = "form_page"
    LOGIN_PAGE = "login_page"
    GENERIC_PAGE = "generic_page"


class PageClassifier:
    """Classify a page snapshot into a PageType using structural signals only."""

    @staticmethod
    def classify(snapshot: dict[str, Any]) -> PageType:
        """Return the structural page type.

        Signals used (all language-independent):
        - ``type=password`` on any fillable element  → login
        - ``autocomplete`` attribute values (standardised)
        - Number of fillable elements
        - Number of actionable links vs total actionable
        - ``role=list`` / ``role=listitem`` on elements
        """
        fillable = snapshot.get("fillable") or []
        actionable = snapshot.get("actionable") or []
        headings = snapshot.get("headings") or []

        # ---- Login page: <input type="password"> is universal ----
        if any(
            (f.get("type") or "").lower() == "password" for f in fillable
        ):
            return PageType.LOGIN_PAGE

        # ---- Form page: many fillable fields ----
        if len(fillable) >= 4:
            return PageType.FORM_PAGE

        # ---- Search results / listing: many links, few forms ----
        link_count = sum(
            1
            for a in actionable
            if (a.get("tag") or "").lower() in ("a",)
            or (a.get("role") or "").lower() in ("link", "listitem")
        )
        if link_count >= 5 and len(fillable) <= 2:
            return PageType.SEARCH_RESULTS

        # ---- Detail page: few headings, few actions, no forms ----
        if len(headings) <= 3 and len(actionable) <= 5 and len(fillable) <= 2:
            return PageType.DETAIL_PAGE

        return PageType.GENERIC_PAGE

    @staticmethod
    def guidance_for(page_type: PageType) -> str:
        """Return a short Russian-language instruction block for the agent."""
        guidance = {
            PageType.SEARCH_RESULTS: (
                "\n---\n"
                "📋 PAGE TYPE: SEARCH RESULTS / LISTING\n"
                "You are on a page with a list of items.\n"
                "1. Look for clickable item titles or links in the list.\n"
                "2. Click on an item to open its details.\n"
                "3. Do NOT click on filters, tabs, checkboxes, or sorting controls.\n"
                "4. If you don't see items, scroll down to reveal more.\n"
                "5. After opening an item, look for the primary action button."
            ),
            PageType.DETAIL_PAGE: (
                "\n---\n"
                "📋 PAGE TYPE: DETAIL PAGE\n"
                "You are viewing details of a single item.\n"
                "1. Look for the primary action button (apply, buy, submit, etc.).\n"
                "2. Scroll to find it if not visible in the viewport.\n"
                "3. If a form is required, fill it using the user's provided data."
            ),
            PageType.FORM_PAGE: (
                "\n---\n"
                "📋 PAGE TYPE: FORM\n"
                "You need to fill in a form.\n"
                "1. Fill required fields using data from the user's goal.\n"
                "2. Use type_text with element_id from inspect_page.\n"
                "3. After filling, look for a submit/save button."
            ),
            PageType.LOGIN_PAGE: (
                "\n---\n"
                "📋 PAGE TYPE: LOGIN\n"
                "A login form is visible.\n"
                "1. If you have credentials, fill them in.\n"
                "2. If no credentials available, call ask_user."
            ),
            PageType.GENERIC_PAGE: "",
        }
        return guidance.get(page_type, "")