"""Minimal browser tools for the new browser core."""

from __future__ import annotations

import json
from typing import Any, Literal

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator

from src.thirdhand.browser_core.prompts import build_visual_assist_prompt
from src.thirdhand.browser_core.session import BrowserSession
from src.thirdhand.config import settings
from src.thirdhand.services.llm import ainvoke_with_retry, create_llm, preview_for_log

logger = structlog.get_logger(__name__)


class ClickArgs(BaseModel):
    """Click target specified by intent, not only by one locator primitive."""

    element_id: str = Field(default="", description="Element id from inspect_page when available.")
    text: str = Field(default="", description="Visible text of the target button/link/tab.")
    exact: bool = Field(default=False, description="Whether text matching must be exact.")

    @model_validator(mode="after")
    def validate_target(self) -> "ClickArgs":
        if not (self.element_id or self.text):
            raise ValueError("Provide element_id or text.")
        return self


class ExtractPageItemsArgs(BaseModel):
    max_items: int = Field(
        default=20,
        description="Maximum number of listing items to extract.",
    )


class TypeTextArgs(BaseModel):
    """Fill target specified by intent, not only one locator primitive."""

    text: str = Field(description="Text to type into the target field.")
    element_id: str = Field(default="", description="Element id from inspect_page.")
    label: str = Field(default="", description="Field label text when available.")
    placeholder: str = Field(default="", description="Field placeholder text when available.")
    exact: bool = Field(default=False, description="Whether label match must be exact.")
    submit: bool = Field(default=False, description="Press Enter after typing.")

    @model_validator(mode="after")
    def validate_target(self) -> "TypeTextArgs":
        if not (self.element_id or self.label or self.placeholder):
            raise ValueError("Provide element_id or label or placeholder.")
        return self


class WaitArgs(BaseModel):
    seconds: float = Field(default=2.0, description="How long to wait for UI updates.")


class ScrollArgs(BaseModel):
    direction: Literal["up", "down"] = Field(default="down")
    amount: int = Field(default=900, description="Scroll distance in pixels.")


class AskUserArgs(BaseModel):
    request_type: Literal["credential", "otp", "file", "captcha", "confirmation", "choice", "other"] = (
        Field(
            default="other",
            description="Why the user must help now.",
        )
    )
    prompt: str = Field(description="Direct concrete request to the user.")


class FinishTaskArgs(BaseModel):
    summary: str = Field(description="Short final result.")
    status: Literal["completed", "stopped"] = Field(
        default="completed",
        description="Use completed only when the user goal was actually finished.",
    )


class VisualAssistArgs(BaseModel):
    question: str = Field(
        default="What is the best next action on this page?",
        description="Specific question about the current page screenshot.",
    )
    goal: str = Field(
        default="",
        description="The user's high-level goal for the current browser task.",
    )


def build_browser_core_tools(session: BrowserSession) -> dict[str, StructuredTool]:
    """Create the minimal toolset for the new browser core."""

    def _looks_like_stale_or_missing_target(exc: Exception) -> bool:
        text = str(exc).lower()
        signals = (
            "no element with data-thirdhand-id",
            "not attached",
            "element_not_found",
            "timeout",
            "waiting for locator",
            "strict mode violation",
        )
        return any(signal in text for signal in signals)

    async def click(
        element_id: str = "",
        text: str = "",
        exact: bool = False,
    ) -> str:
        last_error: Exception | None = None

        # Priority: element_id > text > auto-discovery
        if element_id.strip():
            # Href-fallback: if the element is an <a> with an absolute href AND
            # has non-empty visible text AND is NOT inside a modal dialog,
            # use goto_url instead of click.
            # Skip elements with empty text (logo links, icons, decorative images)
            # — navigating those would take the user away from the current page.
            # Skip elements inside a modal — they need JavaScript click to work
            # (e.g. "confirm" buttons in dialogs that have href but must be clicked).
            try:
                href = await session.page.evaluate(
                    """(id) => {
                        const el = document.querySelector(
                            '[data-thirdhand-id="' + id + '"]'
                        );
                        if (!el) return null;
                        const tag = (el.tagName || "").toLowerCase();
                        if (tag !== "a") return null;
                        // Skip elements inside a modal/dialog — they need JS click
                        if (el.closest("[role='dialog'], dialog, [aria-modal='true']")) return null;
                        // Only use href-fallback if the element has visible text
                        // (skip logo links, icon-only links, empty links).
                        const text = (el.innerText || el.textContent || "").trim();
                        if (!text) return null;
                        const h = el.href || el.getAttribute("href") || "";
                        if (h.startsWith("http")) return h;
                        return null;
                    }""",
                    element_id.strip(),
                )
                if href:
                    logger.info(
                        "browser_core_click_href_navigation",
                        element_id=element_id.strip(),
                        href=href[:200],
                    )
                    return await session.goto_url(href)
            except Exception:
                pass  # href check failed — fall through to normal click

            try:
                return await session.click(element_id.strip())
            except Exception as exc:
                last_error = exc
                if text.strip() and _looks_like_stale_or_missing_target(exc):
                    logger.info(
                        "browser_core_click_fallback_to_text",
                        element_id=element_id.strip(),
                        text=text.strip(),
                        exact=exact,
                        original_error=str(exc),
                    )
                    try:
                        return await session.click_by_text(text.strip(), exact=exact)
                    except Exception as text_exc:
                        last_error = text_exc

        if text.strip() and not element_id.strip():
            try:
                return await session.click_by_text(text.strip(), exact=exact)
            except Exception as exc:
                last_error = exc

        # ---- AUTO-DISCOVERY: inspect_page to find matching element ----
        logger.info(
            "browser_core_click_auto_discovery_started",
            text=text.strip()[:100],
        )
        try:
            snapshot_json = await session.inspect_page()
            snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
            all_actionable = snapshot.get("actionable") or []

            # Modal-scoped search: when a dialog is open, prefer elements inside it
            # (the overlay blocks clicks on background elements).
            modal_open = bool(snapshot.get("dialogs"))
            if modal_open:
                modal_candidates = [el for el in all_actionable if el.get("modal")]
                candidates = modal_candidates if modal_candidates else all_actionable
            else:
                candidates = all_actionable

            # Simple substring search — no scoring, no hardcoded weights
            target = _find_by_substring(candidates, "text", text)
            if not target:
                target = _find_by_substring(candidates, "label", text)
            if target and target.get("id"):
                logger.info(
                    "browser_core_click_auto_resolved",
                    original_text=text.strip()[:100],
                    found_element_id=target["id"],
                    found_text=(target.get("text") or "")[:80],
                    modal_scoped=modal_open,
                )
                found_id = target["id"]
                logger.info(
                    "browser_core_click_auto_resolved",
                    original_text=text.strip()[:100],
                    found_element_id=found_id,
                    found_text=(target.get("text") or "")[:80],
                )
                return await session.click(found_id)
        except Exception as auto_exc:
            logger.warning(
                "browser_core_click_auto_discovery_failed",
                error=str(auto_exc),
            )

        # All paths exhausted
        if last_error is not None:
            raise last_error
        raise ValueError(
            f"Cannot find target after trying element_id, text, "
            f"and auto-discovery. Text={text!r}"
        )

    async def type_text(
        text: str,
        element_id: str = "",
        label: str = "",
        placeholder: str = "",
        exact: bool = False,
        submit: bool = False,
    ) -> str:
        # Priority: element_id > label > placeholder > auto-discovery
        last_error: Exception | None = None

        if element_id.strip():
            try:
                return await session.type_text(element_id.strip(), text, submit=submit)
            except Exception as exc:
                last_error = exc
                if label.strip() and _looks_like_stale_or_missing_target(exc):
                    logger.info(
                        "browser_core_type_fallback_to_label",
                        element_id=element_id.strip(),
                        label=label.strip(),
                        exact=exact,
                        submit=submit,
                        original_error=str(exc),
                    )
                    try:
                        return await session.type_by_label(
                            label.strip(),
                            text,
                            submit=submit,
                            exact=exact,
                        )
                    except Exception as label_exc:
                        last_error = label_exc
                if placeholder.strip() and _looks_like_stale_or_missing_target(exc):
                    logger.info(
                        "browser_core_type_fallback_to_placeholder",
                        element_id=element_id.strip(),
                        placeholder=placeholder.strip(),
                        exact=exact,
                        submit=submit,
                        original_error=str(exc),
                    )
                    try:
                        return await session.type_by_placeholder(
                            placeholder.strip(),
                            text,
                            submit=submit,
                            exact=exact,
                        )
                    except Exception as placeholder_exc:
                        last_error = placeholder_exc

        if label.strip() and not element_id.strip():
            try:
                return await session.type_by_label(label.strip(), text, submit=submit, exact=exact)
            except Exception as exc:
                last_error = exc
                if placeholder.strip() and _looks_like_stale_or_missing_target(exc):
                    logger.info(
                        "browser_core_type_fallback_label_to_placeholder",
                        label=label.strip(),
                        placeholder=placeholder.strip(),
                        exact=exact,
                        submit=submit,
                        original_error=str(exc),
                    )
                    try:
                        return await session.type_by_placeholder(
                            placeholder.strip(),
                            text,
                            submit=submit,
                            exact=exact,
                        )
                    except Exception as placeholder_exc:
                        last_error = placeholder_exc

        if placeholder.strip() and not element_id.strip() and not label.strip():
            try:
                return await session.type_by_placeholder(placeholder.strip(), text, submit=submit, exact=exact)
            except Exception as exc:
                last_error = exc

        # ---- AUTO-DISCOVERY: inspect_page to find element_id ----
        # When a modal/dialog is open, limit search to fields inside it so we
        # don't accidentally fill a background field (e.g. the search "exclude
        # words" box instead of the cover letter textarea in a dialog).
        logger.info(
            "browser_core_type_auto_discovery_started",
            label=label.strip(),
            placeholder=placeholder.strip(),
        )
        try:
            snapshot_json = await session.inspect_page()
            snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
            all_fillable = snapshot.get("fillable") or []

            # Modal-scoped search: prefer fields that are inside a dialog
            modal_open = bool(snapshot.get("dialogs"))
            if modal_open:
                modal_candidates = [el for el in all_fillable if el.get("modal")]
                candidates = modal_candidates if modal_candidates else all_fillable
            else:
                candidates = all_fillable

            # Simple substring search — no scoring, no hardcoded weights
            target = _find_by_substring(candidates, "label", label)
            if not target:
                target = _find_by_substring(candidates, "placeholder", placeholder)
            if target and target.get("id"):
                found_id = target["id"]
                logger.info(
                    "browser_core_type_auto_resolved",
                    original_label=label.strip(),
                    original_placeholder=placeholder.strip(),
                    found_element_id=found_id,
                    found_label=(target.get("label") or "")[:60],
                    found_placeholder=(target.get("placeholder") or "")[:60],
                    modal_scoped=modal_open,
                )
                return await session.type_text(found_id, text, submit=submit)
        except Exception as auto_exc:
            logger.warning(
                "browser_core_type_auto_discovery_failed",
                error=str(auto_exc),
            )

        # All paths exhausted
        if last_error is not None:
            raise last_error
        raise ValueError(
            f"Cannot find target field after trying element_id, label, placeholder, "
            f"and auto-discovery. Label={label!r}, Placeholder={placeholder!r}"
        )

    async def ask_user(
        request_type: Literal[
            "credential", "otp", "file", "captcha", "confirmation", "choice", "other"
        ] = "other",
        prompt: str = "",
    ) -> str:
        return json.dumps(
            {
                "request_type": request_type,
                "prompt": prompt,
            },
            ensure_ascii=False,
        )

    async def finish_task(
        summary: str,
        status: Literal["completed", "stopped"] = "completed",
    ) -> str:
        return json.dumps(
            {
                "summary": summary,
                "status": status,
            },
            ensure_ascii=False,
        )

    async def extract_page_items(max_items: int = 20) -> str:
        """Extract a structured list of cards/rows from the current listing page.

        Uses structural DOM analysis as the primary method — finds all visible
        links with text, groups their parent elements by tag, and picks the most
        frequent repeating tag as the card container. This works on ANY site
        without hardcoded selectors.

        Falls back to CSS attribute selectors when structural analysis yields
        fewer than 2 cards.

        Returns a JSON array of objects with keys:
          - title: visible text of the card's main link/heading
          - href: absolute URL of the card's main link (empty string if none)
          - title_element_id: data-thirdhand-id of the title element to click
          - action_element_id: data-thirdhand-id of the "apply / buy / respond"
            button/link inside the card, or null if not found
        """
        _EXTRACT_JS = """
        (maxItems) => {
          const clean = v => (v || "").replace(/\\s+/g, " ").trim();
          const isVisible = el => {
            const s = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return s.visibility !== "hidden" && s.display !== "none"
                   && r.width > 0 && r.height > 0;
          };
          const ensureId = el => {
            if (!el.dataset.thirdhandId)
              el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2, 10);
            return el.dataset.thirdhandId;
          };

          // Pattern that matches common "action" button labels across languages
          const ACTION_RE = /apply|respond|отклик|купить|buy|add.to.cart|записаться|contact|hire/i;

          // ----------------------------------------------------------------
          // PRIMARY: Structural analysis — find repeating parent elements
          // of links with visible text. Works on any site because any card
          // has at least one title link.
          // ----------------------------------------------------------------
          const allLinks = [...document.querySelectorAll("a[href]")].filter(isVisible);
          const linkParents = new Set(allLinks.map(el => el.parentElement).filter(Boolean));

          // Filter out parents that don't have at least one link with text,
          // and exclude <body>/<html> level parents.
          const candidates = [...linkParents].filter(parent => {
            const tag = (parent.tagName || "").toLowerCase();
            if (tag === "body" || tag === "html") return false;
            const links = [...parent.querySelectorAll("a[href]")];
            return links.some(l => clean(l.innerText || l.textContent || "").length > 0);
          });

          // Group by tag name — pick the tag that appears most frequently
          const tagCounts = {};
          for (const el of candidates) {
            const tag = (el.tagName || "").toLowerCase();
            tagCounts[tag] = (tagCounts[tag] || 0) + 1;
          }
          let bestTag = Object.entries(tagCounts)
            .filter(([, count]) => count >= 2)
            .sort(([, a], [, b]) => b - a)[0]?.[0];

          let cardEls = [];
          if (bestTag) {
            cardEls = candidates.filter(el => (el.tagName || "").toLowerCase() === bestTag);
          }

          // ----------------------------------------------------------------
          // FALLBACK: CSS attribute selectors when structural analysis fails
          // (e.g. when links are nested deeper than one parent level).
          // ----------------------------------------------------------------
          if (cardEls.length < 2) {
            const CARD_SELECTORS = [
              "article",
              "[role='article']",
              "[role='listitem']",
              "li[class*='vacancy'], li[class*='job'], li[class*='item'], li[class*='product']",
              "[data-qa*='vacancy'], [data-qa*='serp-item'], [data-qa*='card']",
              "li[data-id], li[data-item-id]",
              "tr[data-id], tr[data-item]",
              "div[class*='card'], div[class*='vacancy'], div[class*='item']",
            ];
            for (const sel of CARD_SELECTORS) {
              try {
                const found = [...document.querySelectorAll(sel)].filter(isVisible);
                if (found.length >= 2) {
                  // Filter out elements that are descendants of other matched elements.
                  cardEls = found.filter(el =>
                    !found.some(other => other !== el && other.contains(el))
                  );
                  if (cardEls.length >= 2) break;
                  cardEls = [];
                }
              } catch (e) { /* skip invalid selectors */ }
            }
          }

          const items = [];
          for (const card of cardEls.slice(0, maxItems)) {
            // Find the main heading link (most specific first)
            const titleEl =
              card.querySelector("h1 a, h2 a, h3 a, h4 a")
              || card.querySelector("a[data-qa*='title'], a[class*='title'], a[class*='name']")
              || card.querySelector("a[href]");

            // Find an action button/link (apply, buy, respond …)
            const actionEl = [...card.querySelectorAll("button, a[href]")]
              .find(el => ACTION_RE.test(
                el.innerText || el.getAttribute("aria-label") || ""
              ));

            const title = titleEl
              ? clean(titleEl.innerText || titleEl.getAttribute("aria-label") || "").slice(0, 120)
              : "";
            const href = titleEl ? (titleEl.href || titleEl.getAttribute("href") || "") : "";

            items.push({
              title,
              href: href.slice(0, 300),
              title_element_id: titleEl ? ensureId(titleEl) : null,
              action_element_id: actionEl ? ensureId(actionEl) : null,
            });
          }
          return items;
        }
        """
        try:
            items = await session.page.evaluate(_EXTRACT_JS, max_items)
            return json.dumps(items, ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"ERROR: extract_page_items failed: {exc}"

    async def use_visual_assist(
        question: str = "What is the best next action on this page?",
        goal: str = "",
    ) -> str:
        model_name = (
            (settings.PICTURE_RECOGNITION_MODEL or "").strip()
            or (settings.BROWSER_MODEL or "").strip()
            or settings.DEFAULT_MODEL
        )
        screenshot_data_url = await session.capture_screenshot_data_url()
        current_url = await session.current_url()

        # ---- Collect structured DOM hints via inspect_page ----
        hints = "{}"
        try:
            snapshot_json = await session.inspect_page()
            snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json

            clickable = [
                {
                    "text": (el.get("text") or "")[:80],
                    "element_id": el.get("id", ""),
                }
                for el in (snapshot.get("actionable") or [])[:20]
                if not el.get("disabled")
                and not el.get("fillable")
                and (el.get("text") or "").strip()
            ]
            fillable = [
                {
                    "label": (el.get("label") or "")[:60],
                    "placeholder": (el.get("placeholder") or "")[:60],
                    "element_id": el.get("id", ""),
                    "value": (el.get("value_preview") or "")[:40],
                }
                for el in (snapshot.get("fillable") or [])[:10]
            ]

            # Also collect modal-scoped elements for the vision model
            modal_clickable = [
                {
                    "text": (el.get("text") or "")[:80],
                    "element_id": el.get("id", ""),
                }
                for el in (snapshot.get("actionable") or [])[:10]
                if not el.get("disabled")
                and not el.get("fillable")
                and el.get("modal")
                and (el.get("text") or "").strip()
            ]
            modal_fillable = [
                {
                    "label": (el.get("label") or "")[:60],
                    "placeholder": (el.get("placeholder") or "")[:60],
                    "element_id": el.get("id", ""),
                    "value": (el.get("value_preview") or "")[:40],
                }
                for el in (snapshot.get("fillable") or [])[:6]
                if el.get("modal")
            ]

            hints = json.dumps(
                {
                    "url": current_url,
                    "title": snapshot.get("title", ""),
                    "headings": snapshot.get("headings", [])[:5],
                    "dialogs": snapshot.get("dialogs", [])[:3],
                    "clickable": clickable,
                    "fillable": fillable,
                    "modal_clickable": modal_clickable,
                    "modal_fillable": modal_fillable,
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception:
            pass  # если inspect_page упал — работаем без hints

        logger.info(
            "browser_core_visual_assist_started",
            model=model_name,
            current_url=current_url,
            question=question.strip(),
            screenshot_chars=len(screenshot_data_url or ""),
        )
        llm = create_llm(model=model_name, temperature=0.0)
        response = await ainvoke_with_retry(
            llm,
            [
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": build_visual_assist_prompt(
                                goal=goal.strip(),
                                question=question.strip(),
                                hints=hints,
                                current_url=current_url,
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": screenshot_data_url},
                        },
                    ]
                )
            ],
        )
        content = getattr(response, "content", "")
        answer = str(content).strip() if not isinstance(content, list) else str(content)
        logger.info(
            "browser_core_visual_assist_completed",
            model=model_name,
            current_url=current_url,
            question=question.strip(),
            answer_preview=preview_for_log(answer, limit=3000),
        )
        return answer

    tools = [
        StructuredTool.from_function(
            coroutine=session.open_browser,
            name="open_browser",
            description="Ensure the browser is open and optionally navigate to a start URL.",
        ),
        StructuredTool.from_function(
            coroutine=session.goto_url,
            name="goto_url",
            description="Navigate to a known URL when the next destination is explicit.",
        ),
        StructuredTool.from_function(
            coroutine=session.inspect_page,
            name="inspect_page",
            description=(
                "Read the live page. Returns neutral JSON with title, URL, headings, dialogs, "
                "visible text, actionable elements, fillable elements, and locator hints."
            ),
        ),
        StructuredTool.from_function(
            coroutine=click,
            name="click",
            args_schema=ClickArgs,
            description=(
                "Click a visible target. Prefer element_id from inspect_page. If not available, "
                "use visible text."
            ),
        ),
        StructuredTool.from_function(
            coroutine=type_text,
            name="type_text",
            args_schema=TypeTextArgs,
            description=(
                "Type into a field. Prefer element_id from inspect_page. If not available, use label or placeholder."
            ),
        ),
        StructuredTool.from_function(
            coroutine=session.press_key,
            name="press_key",
            description="Press a keyboard key such as Enter, Tab, Escape, ArrowDown, or Control+L.",
        ),
        StructuredTool.from_function(
            coroutine=extract_page_items,
            name="extract_page_items",
            args_schema=ExtractPageItemsArgs,
            description=(
                "Extract a structured list of cards/rows from the current listing page "
                "(vacancies, products, search results, table rows). "
                "Returns [{title, href, title_element_id, action_element_id}]. "
                "Use this BEFORE clicking individual items in a listing to get reliable "
                "element_ids instead of guessing text."
            ),
        ),
        StructuredTool.from_function(
            coroutine=session.scroll,
            name="scroll",
            args_schema=ScrollArgs,
            description="Scroll the current page to reveal more visible content.",
        ),
        StructuredTool.from_function(
            coroutine=session.wait,
            name="wait",
            args_schema=WaitArgs,
            description="Pause briefly for navigation, async UI work, or lazy rendering.",
        ),
        StructuredTool.from_function(
            coroutine=use_visual_assist,
            name="use_visual_assist",
            args_schema=VisualAssistArgs,
            description=(
                "Ask a vision-capable model to interpret the current page screenshot when the DOM is "
                "not enough or the visible layout is ambiguous."
            ),
        ),
        StructuredTool.from_function(
            coroutine=ask_user,
            name="ask_user",
            args_schema=AskUserArgs,
            description=(
                "Use only when real user help is required: credentials, OTP, file, captcha, "
                "manual confirmation, or a business choice that cannot be safely inferred."
            ),
        ),
        StructuredTool.from_function(
            coroutine=finish_task,
            name="finish_task",
            args_schema=FinishTaskArgs,
            description="Finish the task with a short result summary.",
        ),
    ]
    return {tool.name: tool for tool in tools}


# ---------------------------------------------------------------------------
# Auto-discovery helpers — no scoring, no hardcoded weights, just substring
# ---------------------------------------------------------------------------

def _find_by_substring(
    candidates: list[dict[str, Any]],
    field: str,
    needle: str,
) -> dict[str, Any] | None:
    """Find an element whose *field* contains *needle* as a substring.

    This is the only matching primitive.  No scoring, no weights, no
    thresholds — just ``needle in value``.  Works on any language and
    any site because it relies on the text that the page itself exposes.
    """
    if not candidates:
        return None
    if not needle:
        return candidates[0] if candidates else None
    needle_lower = needle.lower().strip()
    for el in candidates:
        value = (el.get(field) or "").lower().strip()
        if needle_lower in value:
            return el
    return None
