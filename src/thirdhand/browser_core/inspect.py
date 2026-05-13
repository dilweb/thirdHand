"""Neutral page inspection for the new browser core."""

from __future__ import annotations

import json
from typing import Any

from src.thirdhand.config import settings

PageLike = Any
BrowserContextLike = Any

_INSPECT_PAGE_JS = """
({ textLimit, elementLimit }) => {
  const limit = typeof elementLimit === "number" && elementLimit > 0 ? elementLimit : 220;

  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden"
      && style.display !== "none"
      && rect.width > 0
      && rect.height > 0;
  };

  const isDisabled = (el) => {
    if (!el) return false;
    return Boolean(
      el.disabled
      || el.getAttribute("aria-disabled") === "true"
      || el.getAttribute("disabled") !== null
    );
  };

  const isFillable = (el) => {
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea" || tag === "select") return true;
    if (tag === "input") {
      const ty = (el.getAttribute("type") || "text").toLowerCase();
      const blocked = new Set([
        "button", "submit", "reset", "image", "checkbox", "radio",
        "hidden", "file", "range", "color"
      ]);
      return !blocked.has(ty);
    }
    return Boolean(el.isContentEditable);
  };

  const textOf = (el) => clean(
    el.innerText
    || el.textContent
    || el.getAttribute("aria-label")
    || el.getAttribute("placeholder")
    || el.getAttribute("title")
    || ""
  );

  const labelTextOf = (el) => {
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const joined = labelledBy
        .split(/\\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((node) => clean(node.innerText || node.textContent || ""))
        .filter(Boolean)
        .join(" ");
      if (joined) return joined;
    }

    const ariaLabel = clean(el.getAttribute("aria-label") || "");
    if (ariaLabel) return ariaLabel;

    const htmlId = el.getAttribute("id");
    if (htmlId) {
      const label = document.querySelector(`label[for="${CSS.escape(htmlId)}"]`);
      if (label) {
        const txt = clean(label.innerText || label.textContent || "");
        if (txt) return txt;
      }
    }

    const parentLabel = el.closest("label");
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      for (const nested of clone.querySelectorAll("input, textarea, select, button")) {
        nested.remove();
      }
      const txt = clean(clone.innerText || clone.textContent || "");
      if (txt) return txt;
    }

    return "";
  };

  const valuePreviewOf = (el, fillable) => {
    if (!fillable) return "";
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "select") {
      const opt = el.options[el.selectedIndex];
      return clean((opt && opt.text) || el.value || "").slice(0, 80);
    }
    if (tag === "input") {
      const ty = (el.getAttribute("type") || "text").toLowerCase();
      if (ty === "password") return "[hidden]";
      return clean(el.value || "").slice(0, 80);
    }
    if (tag === "textarea") {
      return clean(el.value || "").slice(0, 80);
    }
    if (el.isContentEditable) {
      return clean(el.innerText || el.textContent || "").slice(0, 80);
    }
    return "";
  };

  const locatorHintOf = (el) => {
    const tag = (el.tagName || "").toLowerCase();
    const role = el.getAttribute("role") || "";
    const text = textOf(el);
    const label = labelTextOf(el);
    const placeholder = clean(el.getAttribute("placeholder") || "");
    const parts = [tag];
    if (role) parts.push(`role=${role}`);
    if (label) parts.push(`label=${label.slice(0, 40)}`);
    else if (text) parts.push(`text=${text.slice(0, 40)}`);
    else if (placeholder) parts.push(`placeholder=${placeholder.slice(0, 40)}`);
    return parts.join(" | ");
  };

  const ensureId = (el) => {
    if (!el.dataset.thirdhandId) {
      el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2, 10);
    }
    return el.dataset.thirdhandId;
  };

  const selectors = [
    "a",
    "button",
    "input",
    "textarea",
    "select",
    "[role='button']",
    "[role='link']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='option']",
    "[role='tab']",
    "[contenteditable='true']",
    "[tabindex]"
  ].join(",");

  const seen = new Set();
  const elements = [];
  for (const el of document.querySelectorAll(selectors)) {
    if (!isVisible(el)) continue;
    const id = ensureId(el);
    if (seen.has(id)) continue;
    seen.add(id);

    const fillable = isFillable(el);
    const item = {
      id,
      tag: (el.tagName || "").toLowerCase(),
      role: el.getAttribute("role") || "",
      type: el.getAttribute("type") || "",
      text: textOf(el).slice(0, 120),
      name: clean(el.getAttribute("name") || "").slice(0, 120),
      placeholder: clean(el.getAttribute("placeholder") || "").slice(0, 120),
      label: labelTextOf(el).slice(0, 120),
      href: clean(el.getAttribute("href") || "").slice(0, 240),
      html_id: clean(el.getAttribute("id") || "").slice(0, 120),
      data_qa: clean(el.getAttribute("data-qa") || "").slice(0, 120),
      autocomplete: clean(el.getAttribute("autocomplete") || "").toLowerCase(),
      value_preview: valuePreviewOf(el, fillable),
      fillable,
      disabled: isDisabled(el),
      checked: Boolean(el.checked || el.getAttribute("aria-checked") === "true"),
      selected: Boolean(el.selected || el.getAttribute("aria-selected") === "true"),
      expanded: el.getAttribute("aria-expanded") === "true",
      modal: Boolean(el.closest("[role='dialog'], dialog, [aria-modal='true']")),
      locator_hint: locatorHintOf(el).slice(0, 180)
    };

    elements.push(item);
    if (elements.length >= limit) break;
  }

  const headings = [];
  for (const el of document.querySelectorAll("h1, h2, h3")) {
    if (!isVisible(el)) continue;
    const text = textOf(el);
    if (!text) continue;
    headings.push(text.slice(0, 160));
    if (headings.length >= 15) break;
  }

  const dialogs = [];
  for (const el of document.querySelectorAll("dialog, [role='dialog'], [aria-modal='true']")) {
    if (!isVisible(el)) continue;
    const text = textOf(el);
    if (!text) continue;
    dialogs.push(text.slice(0, 160));
    if (dialogs.length >= 8) break;
  }

  const actionable = elements.filter((item) => !item.fillable);
  const fillable = elements.filter((item) => item.fillable);
  const bodyText = clean(document.body?.innerText || "");

  // Categorize clickable elements for better agent guidance
  const clickable = elements.filter((item) => {
    if (item.fillable) return false;
    const tag = item.tag.toLowerCase();
    const role = (item.role || "").toLowerCase();
    const isClickable = tag === "a" || tag === "button" || role === "button" || role === "link" || role === "tab";
    return isClickable && !item.disabled;
  });

  // Extract top clickable hints (short text labels for clickable elements)
  const clickableHints = clickable.slice(0, 15).map((item) => {
    const text = item.text.slice(0, 60);
    const label = item.label.slice(0, 60);
    return text || label || item.locator_hint.slice(0, 80);
  }).filter(Boolean);

  // Extract top fillable hints
  const fillableHints = fillable.slice(0, 10).map((item) => {
    const label = item.label.slice(0, 60);
    const placeholder = item.placeholder.slice(0, 60);
    return label || placeholder || item.locator_hint.slice(0, 80);
  }).filter(Boolean);

  // Separate hints for elements INSIDE a dialog/modal — the LLM needs to
  // know which elements are in the modal vs on the background page.
  const modalClickable = clickable.filter((item) => item.modal);
  const modalFillable = fillable.filter((item) => item.modal);
  const modalActionableHints = modalClickable.slice(0, 8).map((item) => {
    const text = item.text.slice(0, 60);
    const label = item.label.slice(0, 60);
    return text || label || item.locator_hint.slice(0, 80);
  }).filter(Boolean);
  const modalFillableHints = modalFillable.slice(0, 6).map((item) => {
    const label = item.label.slice(0, 60);
    const placeholder = item.placeholder.slice(0, 60);
    return label || placeholder || item.locator_hint.slice(0, 80);
  }).filter(Boolean);

  return {
    title: document.title || "",
    url: location.href,
    headings,
    dialogs,
    actionable,
    fillable,
    elements,
    text: bodyText.slice(0, textLimit),
    clickable_hints: clickableHints,
    fillable_hints: fillableHints,
    modal_actionable_hints: modalActionableHints,
    modal_fillable_hints: modalFillableHints,
    metadata: {
      element_count: elements.length,
      actionable_count: actionable.length,
      fillable_count: fillable.length,
      clickable_count: clickable.length,
      modal_actionable_count: modalClickable.length,
      modal_fillable_count: modalFillable.length
    }
  };
}
"""

_SESSION_PROBE_JS = """
() => {
  const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
  const bodyText = clean(document.body?.innerText || "");
  const interactiveTexts = Array.from(
    document.querySelectorAll("a, button, [role='button'], [role='link']")
  )
    .map((el) => clean(
      el.innerText
      || el.textContent
      || el.getAttribute("aria-label")
      || el.getAttribute("title")
      || ""
    ))
    .filter(Boolean)
    .slice(0, 20);

  return {
    title: document.title || "",
    url: location.href,
    body_text_preview: bodyText.slice(0, 300),
    interactive_texts: interactiveTexts
  };
}
"""


async def inspect_page(
    page: PageLike,
    text_limit: int | None = None,
    *,
    element_limit: int | None = None,
) -> str:
    """Return a neutral JSON snapshot of the current page."""
    snapshot = await page.evaluate(
        _INSPECT_PAGE_JS,
        {
            "textLimit": text_limit or settings.BROWSER_SNAPSHOT_TEXT_LIMIT,
            "elementLimit": element_limit or settings.BROWSER_INSPECT_INTERACTIVE_LIMIT,
        },
    )
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


_COMPACT_INSPECT_JS = """
() => {
  const clean = v => (v || "").replace(/\\s+/g, " ").trim();
  const isVisible = el => {
    const s = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.visibility !== "hidden" && s.display !== "none" && r.width > 0 && r.height > 0;
  };
  const ensureId = el => {
    if (!el.dataset.thirdhandId)
      el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2, 10);
    return el.dataset.thirdhandId;
  };

  const headings = [];
  for (const el of document.querySelectorAll("h1,h2,h3")) {
    if (!isVisible(el)) continue;
    const t = clean(el.innerText || "");
    if (t) headings.push(t.slice(0, 120));
    if (headings.length >= 6) break;
  }

  const dialogs = [];
  for (const el of document.querySelectorAll("dialog,[role='dialog'],[aria-modal='true']")) {
    if (!isVisible(el)) continue;
    const t = clean(el.innerText || "");
    if (t) dialogs.push(t.slice(0, 200));
    if (dialogs.length >= 3) break;
  }

  const actionable = [];
  for (const el of document.querySelectorAll("a,button,[role='button']")) {
    if (!isVisible(el) || el.disabled) continue;
    const text = clean(el.innerText || el.getAttribute("aria-label") || "").slice(0, 60);
    if (!text) continue;
    actionable.push({ id: ensureId(el), text });
    if (actionable.length >= 15) break;
  }

  const fillable = [];
  for (const el of document.querySelectorAll("input,textarea,select,[contenteditable='true']")) {
    if (!isVisible(el)) continue;
    const label = clean(el.getAttribute("aria-label") || "")
      || clean((el.closest("label,fieldset") || { innerText: "" }).innerText || "").slice(0, 60)
      || clean(el.getAttribute("placeholder") || "").slice(0, 60);
    const inModal = Boolean(el.closest("dialog,[role='dialog'],[aria-modal='true']"));
    fillable.push({ id: ensureId(el), label, in_modal: inModal });
    if (fillable.length >= 8) break;
  }

  return {
    url: location.href,
    title: document.title || "",
    modal_open: dialogs.length > 0,
    headings,
    dialogs,
    actionable,
    fillable,
  };
}
"""


async def compact_inspect_page(page: PageLike) -> str:
    """Return a lightweight page snapshot (~1-2k tokens vs 30-50k for full inspect).

    Returns url, title, modal_open, headings, dialogs, actionable (15 items),
    fillable (8 items).  Used for automatic post-action snapshots inside the
    agent loop so the context window doesn't grow unboundedly.

    The full ``inspect_page`` remains available as an explicit tool for the LLM.
    """
    result = await page.evaluate(_COMPACT_INSPECT_JS)
    return json.dumps(result, ensure_ascii=False)


async def session_probe(page: PageLike, context: BrowserContextLike) -> dict[str, Any]:
    """Collect lightweight session diagnostics without classifying the page."""
    cookies = await context.cookies()
    cookie_domains = sorted(
        {cookie.get("domain", "") for cookie in cookies if cookie.get("domain")}
    )
    probe = await page.evaluate(_SESSION_PROBE_JS)
    return {
        "title": probe.get("title", ""),
        "url": probe.get("url", ""),
        "cookie_count": len(cookies),
        "cookie_domains": cookie_domains[:12],
        "body_text_preview": probe.get("body_text_preview", ""),
        "interactive_texts": probe.get("interactive_texts", [])[:10],
    }
