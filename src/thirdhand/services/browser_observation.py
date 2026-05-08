"""DOM snapshot, session probe, observational helpers, and optional vision assist."""

from __future__ import annotations

import json
from typing import Any, Final

import structlog
from langchain_core.messages import HumanMessage

from src.thirdhand.config import settings
from src.thirdhand.services.browser_recovery import (
    dom_evidence_suggests_captcha,
    explain_visual_assist_decision,
)
from src.thirdhand.services.llm import ainvoke_with_retry, create_llm, preview_for_log

logger = structlog.get_logger(__name__)

# Playwright Page / BrowserContext — avoid importing playwright at module load.
PageLike = Any
BrowserContextLike = Any
VisionSessionLike = Any

# Visible control labels merged with optional per-site registry extras (any language).
DEFAULT_LOGIN_ENTRYPOINT_LABELS: Final[tuple[str, ...]] = (
    "sign in",
    "log in",
    "login",
    "signin",
    "войти",
    "anmelden",
    "connexion",
)
DEFAULT_PASSWORD_MODE_LABELS: Final[tuple[str, ...]] = (
    "password",
    "passphrase",
    "with password",
    "using password",
    "войти с паролем",
    "вход с паролем",
    "по паролю",
    "пароль",
    "passwort",
    "contraseña",
)
DEFAULT_PHONE_FLOW_CONTINUE_LABELS: Final[tuple[str, ...]] = (
    "continue",
    "next",
    "submit",
    "далее",
    "дальше",
    "продолжить",
    "weiter",
    "siguiente",
)

_INSPECT_PAGE_JS = """
({ textLimit, interactiveLimit }) => {
  const lim = typeof interactiveLimit === "number" && interactiveLimit > 0 ? interactiveLimit : 180;
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden"
      && style.display !== "none"
      && rect.width > 0
      && rect.height > 0;
  };
  const textOf = (el) => {
    const raw = (
      el.innerText
      || el.contentText
      || el.textContent
      || el.getAttribute("aria-label")
      || el.getAttribute("placeholder")
      || el.getAttribute("title")
      || ""
    );
    return raw.replace(/\\s+/g, " ").trim();
  };
  const interactiveSelector = [
    "a",
    "button",
    "input",
    "textarea",
    "select",
    "[role='button']",
    "[contenteditable='true']",
    "[tabindex]"
  ].join(",");

  const pushInteractive = (el, interactive, seenIds) => {
    if (!isVisible(el)) return false;
    if (!el.dataset.thirdhandId) {
      el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2, 10);
    }
    const thId = el.dataset.thirdhandId;
    if (seenIds.has(thId)) return false;
    seenIds.add(thId);
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    const tagLower = el.tagName.toLowerCase();
    let fillable = false;
    if (tagLower === "textarea") {
      fillable = true;
    } else if (tagLower === "select") {
      fillable = true;
    } else if (tagLower === "input") {
      const ty = (el.getAttribute("type") || "text").toLowerCase();
      const nonText = new Set([
        "button", "submit", "reset", "image", "checkbox", "radio",
        "hidden", "file", "range", "color"
      ]);
      fillable = !nonText.has(ty);
    } else if (el.isContentEditable) {
      fillable = true;
    }
    interactive.push({
      id: thId,
      tag: tagLower,
      text: textOf(el).slice(0, 120),
      type: el.getAttribute("type") || "",
      role: el.getAttribute("role") || "",
      name: el.getAttribute("name") || "",
      placeholder: el.getAttribute("placeholder") || "",
      href: el.getAttribute("href") || "",
      autocomplete: auto,
      html_id: (el.getAttribute("id") || "").slice(0, 120),
      fillable,
      value_preview: (() => {
        if (!fillable) return "";
        if (tagLower === "select") {
          const sel = el;
          const opt = sel.options[sel.selectedIndex];
          const t = (opt?.text || sel.value || "").replace(/\\s+/g, " ").trim();
          return t.length > 80 ? t.slice(0, 77) + "..." : t;
        }
        if (tagLower === "textarea") {
          const t = (el.value || "").replace(/\\s+/g, " ").trim();
          return t.length > 80 ? t.slice(0, 77) + "..." : t;
        }
        if (tagLower === "input") {
          const ty = (el.getAttribute("type") || "text").toLowerCase();
          if (ty === "password") return "[hidden]";
          const t = (el.value || "").replace(/\\s+/g, " ").trim();
          return t.length > 80 ? t.slice(0, 77) + "..." : t;
        }
        if (el.isContentEditable) {
          const t = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
          return t.length > 80 ? t.slice(0, 77) + "..." : t;
        }
        return "";
      })()
    });
    return interactive.length >= lim;
  };

  const seenIds = new Set();
  const interactive = [];
  const roots = [];
  try {
    const byA11y = document.querySelector("#a11y-main-content");
    if (byA11y) roots.push(byA11y);
  } catch (e) {}
  for (const el of document.querySelectorAll("main, [role='main']")) {
    if (el && !roots.includes(el)) roots.push(el);
  }
  if (!roots.length && document.body) roots.push(document.body);

  outer: for (const root of roots) {
    try {
      for (const el of root.querySelectorAll(interactiveSelector)) {
        if (pushInteractive(el, interactive, seenIds)) break outer;
      }
    } catch (e) {}
  }

  if (interactive.length < lim) {
    for (const el of document.querySelectorAll(interactiveSelector)) {
      if (pushInteractive(el, interactive, seenIds)) break;
    }
  }

  const headings = [];
  for (const el of document.querySelectorAll("h1, h2, h3")) {
    if (!isVisible(el)) continue;
    const text = textOf(el);
    if (!text) continue;
    headings.push(text.slice(0, 160));
    if (headings.length >= 12) break;
  }

  const bodyText = (document.body?.innerText || "").replace(/\\s+/g, " ").trim();

  return {
    title: document.title || "",
    url: location.href,
    headings,
    interactive,
    text: bodyText.slice(0, textLimit)
  };
}
"""

_SESSION_PROBE_JS = """
() => {
  const bodyText = (document.body?.innerText || "").replace(/\\s+/g, " ").trim();
  const bodyLower = bodyText.toLowerCase();
  const interactiveTexts = Array.from(
    document.querySelectorAll("a, button, [role='button']")
  )
    .map((el) => (
      el.innerText
      || el.textContent
      || el.getAttribute("aria-label")
      || el.getAttribute("title")
      || ""
    ).replace(/\\s+/g, " ").trim())
    .filter(Boolean)
    .slice(0, 20);
  const interactiveLower = interactiveTexts.join(" ").toLowerCase();

  return {
    title: document.title || "",
    url: location.href,
    body_text_preview: bodyText.slice(0, 300),
    interactive_texts: interactiveTexts,
    auth_signals: {
      login_form_present: Boolean(
        document.querySelector("input[type='password'], form[action*='login'], form[action*='signin']")
      ),
      has_resume_keyword: bodyLower.includes("резюме"),
      has_logout_keyword: bodyLower.includes("выйти"),
      has_profile_keyword: bodyLower.includes("профиль") || bodyLower.includes("мой профиль"),
      has_response_keyword: bodyLower.includes("отклик"),
      has_login_keyword: bodyLower.includes("вход") || bodyLower.includes("login") || bodyLower.includes("sign in"),
      has_account_menu_keyword: interactiveLower.includes("профиль") || interactiveLower.includes("выйти"),
    }
  };
}
"""


async def inspect_page(
    page: PageLike,
    text_limit: int,
    *,
    interactive_limit: int | None = None,
) -> str:
    """Return a compact JSON snapshot of the current page for the LLM."""
    lim = interactive_limit if interactive_limit is not None else settings.BROWSER_INSPECT_INTERACTIVE_LIMIT
    snapshot = await page.evaluate(
        _INSPECT_PAGE_JS,
        {"textLimit": text_limit, "interactiveLimit": lim},
    )
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


async def session_probe(page: PageLike, context: BrowserContextLike) -> dict[str, Any]:
    """Collect lightweight diagnostics about the current browser/session state."""
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
        "auth_signals": probe.get("auth_signals", {}),
    }


def extract_login_field_ids(snapshot: dict[str, Any]) -> tuple[str, str]:
    """Best-effort extract identity + password field ids from an inspect_page snapshot (site-agnostic)."""
    interactive = snapshot.get("interactive", []) or []
    username_candidates: list[tuple[int, str]] = []
    password_id = ""

    identity_tokens = (
        "login",
        "email",
        "e-mail",
        "mail",
        "phone",
        "username",
        "user",
        "account",
        "identifier",
        "mobile",
        "tel",
        "почт",
        "тел",
        "логин",
        "идентификатор",
    )

    def _haystack(item: dict[str, Any]) -> str:
        return " ".join(
            str(item.get(k, "") or "")
            for k in ("text", "name", "placeholder", "role", "autocomplete", "html_id")
        ).lower()

    skip_input_types = {"hidden", "submit", "button", "checkbox", "radio", "file", "image", "range", "color"}

    for item in interactive:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("id", "")).strip()
        tag = str(item.get("tag", "")).lower()
        if not element_id or tag != "input":
            continue
        input_type = str(item.get("type", "")).lower()
        autocomplete = str(item.get("autocomplete", "")).lower()
        hay = _haystack(item)

        if input_type in skip_input_types:
            continue

        if input_type == "password" or autocomplete in ("current-password", "new-password"):
            if not password_id:
                password_id = element_id
            continue
        if "password" in hay and input_type in {"text", "password", ""}:
            if not password_id:
                password_id = element_id
            continue

    for item in interactive:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("id", "")).strip()
        tag = str(item.get("tag", "")).lower()
        if not element_id:
            continue
        if element_id == password_id:
            continue

        if tag == "input":
            input_type = str(item.get("type", "")).lower()
            if input_type in skip_input_types:
                continue
        elif tag == "textarea":
            input_type = ""
        else:
            continue

        autocomplete = str(item.get("autocomplete", "")).lower()
        hay = _haystack(item)
        score = 0

        if autocomplete in ("username", "email", "tel"):
            score += 24
        if autocomplete in ("nickname", "organization"):
            score += 4
        if input_type == "email":
            score += 12
        if input_type == "tel":
            score += 10
        if tag == "textarea":
            score += 3
        for token in identity_tokens:
            if token in hay:
                score += 4

        if score >= 4:
            username_candidates.append((score, element_id))

    username_candidates.sort(reverse=True)
    username_id = username_candidates[0][1] if username_candidates else ""
    return username_id, password_id


def find_interactive_element_id(snapshot: dict[str, Any], text_needles: tuple[str, ...]) -> str:
    """Find an interactive element id by matching its visible text."""
    interactive = snapshot.get("interactive", []) or []
    lowered_needles = tuple(needle.lower() for needle in text_needles if needle)
    for item in interactive:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip().lower()
        if not element_id or not text:
            continue
        if any(needle in text for needle in lowered_needles):
            return element_id
    return ""


async def maybe_build_visual_guidance(
    *,
    session: VisionSessionLike,
    user_id: int,
    goal: str,
    site_key: str,
    snapshot_json: str,
    auth_guidance: str,
    recovery_attempt: int,
    dom_evidence_weak: bool = False,
    goal_text_for_vision: str | None = None,
    page_state: "BrowserPageState" | None = None,
) -> str:
    """Ask a vision model for help interpreting the current page when DOM guidance is not enough."""
    pic = (settings.PICTURE_RECOGNITION_MODEL or "").strip()
    br = (settings.BROWSER_MODEL or "").strip()
    default_m = (settings.DEFAULT_MODEL or "").strip()

    use_visual, decision_code = explain_visual_assist_decision(
        site_key=site_key,
        snapshot_json=snapshot_json,
        auth_guidance=auth_guidance,
        recovery_attempt=recovery_attempt,
        dom_evidence_weak=dom_evidence_weak,
        page_state=page_state,
    )
    if not use_visual:
        logger.info(
            "browser_visual_analysis_skipped",
            user_id=user_id,
            skip_reason=decision_code,
            picture_recognition_model=pic,
            browser_model=br,
            default_model=default_m,
            recovery_attempt=recovery_attempt,
            site_key=site_key,
        )
        return ""

    model_name = pic or br or default_m or None
    if not model_name:
        logger.info(
            "browser_visual_analysis_skipped",
            user_id=user_id,
            skip_reason="no_resolved_model",
            picture_recognition_model=pic,
            browser_model=br,
            default_model=default_m,
            recovery_attempt=recovery_attempt,
            site_key=site_key,
        )
        return ""

    model_source = "PICTURE_RECOGNITION_MODEL" if pic else ("BROWSER_MODEL" if br else "DEFAULT_MODEL")
    vision_goal = (goal_text_for_vision or "").strip() or goal

    try:
        current_url = await session.current_url()
        screenshot_data_url = await session.capture_screenshot_data_url()
        logger.info(
            "browser_visual_analysis_started",
            user_id=user_id,
            resolved_vision_model=model_name,
            vision_model_source=model_source,
            picture_recognition_model=pic,
            browser_model_for_tools=br,
            default_model=default_m,
            site=site_key,
            current_url=current_url,
            recovery_attempt=recovery_attempt,
        )
        vision_llm = create_llm(model=model_name, temperature=0.0)
        captcha_mode = dom_evidence_suggests_captcha(snapshot_json)
        if captcha_mode:
            instruction = (
                "This screenshot shows a CAPTCHA, bot check, or human-verification step on a job/accounts site.\n"
                f"User goal (context only): {vision_goal}\n"
                f"Site: {site_key or 'unknown'}  URL: {current_url}\n"
                "Answer in plain text, under 140 words:\n"
                "1) Challenge type (image text, reCAPTCHA-style checkbox, puzzle/slider, SMS gate, other).\n"
                "2) If distorted letters/numbers are meant to be typed by a human, transcribe ONLY what you can "
                "read clearly; if unreadable, say UNREADABLE.\n"
                "3) List visible primary actions (exact button/link labels).\n"
                "4) State clearly that automated solvers must not be used — the legitimate user completes this step; "
                "the runtime will ask the user to continue after they finish.\n"
                "Start line 2 with CAPTCHA_TRANSCRIPT: when you have a best-effort transcript (or CAPTCHA_TRANSCRIPT: UNREADABLE)."
            )
        else:
            page_state_hint = ""
            if page_state is not None:
                page_state_hint = (
                    f"Runtime page-state guess: screen_kind={page_state.screen_kind}, "
                    f"candidate_actions={', '.join(page_state.candidate_actions[:3]) or '(none)'}, "
                    f"required_inputs={', '.join(page_state.required_inputs) or '(none)'}, "
                    f"confidence={page_state.confidence:.2f}.\n"
                )
            instruction = (
                "You are helping a browser automation agent interpret a page screenshot.\n"
                f"User goal (short, trust the screenshot if this disagrees): {vision_goal}\n"
                f"Current site: {site_key or 'unknown'}\n"
                f"Current URL: {current_url}\n"
                f"{page_state_hint}"
                "Look at the screenshot and answer very concisely in plain text with:\n"
                "1. What screen this is.\n"
                "2. The 1-3 most important visible buttons/fields.\n"
                "3. The best next action for the automation agent.\n"
                "4. Whether user help is needed now for a code/manual confirmation.\n"
                "Keep it under 120 words."
            )
        vision_message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": instruction,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": screenshot_data_url},
                },
            ]
        )
        response = await ainvoke_with_retry(vision_llm, [vision_message])
        content = getattr(response, "content", "")
        guidance = (
            str(content).strip()
            if not isinstance(content, list)
            else preview_for_log(content, limit=1200)
        )
        logger.info(
            "browser_visual_analysis_completed",
            user_id=user_id,
            resolved_vision_model=model_name,
            vision_model_source=model_source,
            site=site_key,
            current_url=current_url,
            guidance_preview=preview_for_log(guidance, limit=800),
        )
        return f"Visual page assistance:\n{guidance}" if guidance else ""
    except Exception as exc:
        logger.warning(
            "browser_visual_analysis_failed",
            user_id=user_id,
            resolved_vision_model=model_name,
            vision_model_source=model_source,
            site=site_key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return ""
