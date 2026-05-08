"""Low-level Playwright browser session lifecycle (persistent context)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import structlog

from src.thirdhand.config import settings
from src.thirdhand.services import browser_observation
from src.thirdhand.services.llm import preview_for_log, redact_sensitive_text_for_log

logger = structlog.get_logger(__name__)

# Reject typing into buttons, links, and non-text inputs (matches inspect_page fillable flag).
_FILLABLE_TARGET_JS = """
(el) => {
  if (!el) {
    return { ok: false, reason: "element_not_found" };
  }
  const t = el.tagName.toLowerCase();
  if (t === "textarea") {
    return { ok: true };
  }
  if (t === "select") {
    return { ok: true };
  }
  if (t === "input") {
    const ty = (el.getAttribute("type") || "text").toLowerCase();
    const bad = new Set(
      ["button", "submit", "reset", "image", "checkbox", "radio",
       "hidden", "file", "range", "color"]
    );
    if (bad.has(ty)) {
      return { ok: false, reason: "input_type:" + ty };
    }
    return { ok: true };
  }
  if (el.isContentEditable) {
    return { ok: true };
  }
  return { ok: false, reason: "tag:" + t };
}
"""


class BrowserSession:
    """Persistent Playwright context: lifecycle, navigation, reads, input actions, and screenshots."""

    def __init__(self) -> None:
        self.playwright = None
        self.context = None
        self.page = None

    async def _await_phase(
        self, phase_name: str, awaitable, timeout_seconds: float, **log_fields: Any
    ):
        """Await one Playwright startup phase with explicit timeout diagnostics."""
        logger.info(
            f"{phase_name}_started",
            timeout_seconds=timeout_seconds,
            **log_fields,
        )
        started_at = time.monotonic()
        try:
            result = await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        except Exception as exc:
            logger.error(
                f"{phase_name}_failed",
                elapsed_seconds=round(time.monotonic() - started_at, 2),
                error_type=type(exc).__name__,
                error=str(exc),
                **log_fields,
            )
            raise
        logger.info(
            f"{phase_name}_completed",
            elapsed_seconds=round(time.monotonic() - started_at, 2),
            **log_fields,
        )
        return result

    async def ensure_started(self) -> None:
        """Start a persistent browser context lazily."""
        if self.page is not None:
            logger.info(
                "playwright_session_reused",
                current_url=self.page.url if self.page else "",
                page_count=len(self.context.pages) if self.context else 0,
            )
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Run `poetry install` and "
                "`poetry run playwright install chromium`."
            ) from exc

        profile_dir = Path(settings.BROWSER_PROFILE_DIR).expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "playwright_session_starting",
            profile_dir=str(profile_dir),
            headless=settings.BROWSER_HEADLESS,
            snapshot_limit=settings.BROWSER_SNAPSHOT_TEXT_LIMIT,
        )

        self.playwright = await self._await_phase(
            "playwright_runtime",
            async_playwright().start(),
            timeout_seconds=20,
            profile_dir=str(profile_dir),
        )
        self.context = await self._await_phase(
            "playwright_context_launch",
            self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=settings.BROWSER_HEADLESS,
                viewport={"width": 1440, "height": 960},
            ),
            timeout_seconds=45,
            profile_dir=str(profile_dir),
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        page_urls = [page.url for page in self.context.pages]
        logger.info(
            "playwright_session_started",
            page_count=len(self.context.pages),
            page_urls=page_urls,
            active_url=self.page.url,
        )

    async def goto_url(self, url: str) -> str:
        """Navigate to a URL, adding a scheme if needed."""
        await self.ensure_started()
        normalized = url.strip()
        if normalized and "://" not in normalized:
            normalized = f"https://{normalized}"
        logger.info(
            "playwright_goto_started",
            requested_url=url,
            normalized_url=normalized,
            current_url=self.page.url if self.page else "",
        )
        await self.page.goto(normalized, wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_timeout(1_000)
        logger.info(
            "playwright_goto_completed",
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self._page_header()

    async def open_browser(self, start_url: str = "") -> str:
        """Ensure a visible browser is available and optionally navigate."""
        await self.ensure_started()
        logger.info(
            "playwright_open_browser",
            start_url=start_url,
            current_url=self.page.url if self.page else "",
        )
        if start_url.strip():
            await self.goto_url(start_url)
        return await self._page_header()

    async def inspect_page(self) -> str:
        """Return a compact snapshot of the current page for the LLM."""
        await self.ensure_started()
        return await browser_observation.inspect_page(
            self.page,
            settings.BROWSER_SNAPSHOT_TEXT_LIMIT,
            interactive_limit=settings.BROWSER_INSPECT_INTERACTIVE_LIMIT,
        )

    async def wait_for_page(self, seconds: float = 2.0) -> str:
        """Pause briefly for async UI updates."""
        await self.ensure_started()
        logger.info(
            "playwright_wait_started",
            seconds=seconds,
            current_url=self.page.url if self.page else "",
        )
        await self.page.wait_for_timeout(int(seconds * 1000))
        logger.info(
            "playwright_wait_completed",
            seconds=seconds,
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self._page_header()

    async def read_page(self) -> str:
        """Return visible page text only."""
        await self.ensure_started()
        content = await self.page.evaluate(
            """
            ({ textLimit }) => {
              const bodyText = (document.body?.innerText || "").replace(/\\s+/g, " ").trim();
              return JSON.stringify({
                title: document.title || "",
                url: location.href,
                text: bodyText.slice(0, textLimit)
              }, null, 2);
            }
            """,
            {"textLimit": settings.BROWSER_SNAPSHOT_TEXT_LIMIT},
        )
        return content

    async def current_url(self) -> str:
        """Return the current URL."""
        await self.ensure_started()
        return self.page.url

    async def session_probe(self) -> dict[str, Any]:
        """Collect lightweight diagnostics about the current browser/session state."""
        await self.ensure_started()
        return await browser_observation.session_probe(self.page, self.context)

    async def _assert_text_input_target(self, locator: Any, *, element_id: str) -> None:
        """Ensure we only fill real text fields, not buttons or chrome controls."""
        try:
            result = await locator.evaluate(_FILLABLE_TARGET_JS)
        except Exception as exc:
            raise ValueError(
                f"No element with data-thirdhand-id={element_id!r} or it is not attached."
            ) from exc
        if isinstance(result, dict) and result.get("ok"):
            return
        reason = (
            (result.get("reason") if isinstance(result, dict) else None) or "not_fillable"
        )
        raise ValueError(
            f"Refusing to type into non-text element (id={element_id}, {reason}). "
            "Re-run inspect_page and use a row with fillable=true (input/textarea/select, not button)."
        )

    async def _locator_click_resilient(self, locator: Any) -> None:
        """Click through dropdown/modal overlays: retry after Escape, then force-click."""
        try:
            await locator.scroll_into_view_if_needed(timeout=5_000)
        except Exception:
            pass
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                await locator.click(timeout=10_000)
                return
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    try:
                        await self.page.keyboard.press("Escape")
                        await self.page.wait_for_timeout(280)
                    except Exception:
                        pass
                    continue
                break
        try:
            await locator.click(force=True, timeout=10_000)
            return
        except Exception:
            if last_exc is not None:
                raise last_exc
            raise

    async def click(self, element_id: str) -> str:
        """Click an element by dynamic page-local id."""
        await self.ensure_started()
        logger.info(
            "playwright_click_started",
            element_id=element_id,
            current_url=self.page.url if self.page else "",
        )
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._locator_click_resilient(locator)
        await self.page.wait_for_timeout(1_000)
        header = await self._page_header()
        logger.info(
            "playwright_click_completed",
            element_id=element_id,
            final_url=self.page.url,
            title=await self.page.title(),
            action="click",
            page_header_preview=preview_for_log(header, limit=400),
        )
        return header

    async def type_text(self, element_id: str, text: str, submit: bool = False) -> str:
        """Type into an input-like element, with fallback to keyboard typing."""
        await self.ensure_started()
        logger.info(
            "playwright_type_started",
            element_id=element_id,
            submit=submit,
            text_char_count=len(text or ""),
            text_redacted=redact_sensitive_text_for_log(text),
            current_url=self.page.url if self.page else "",
        )
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._assert_text_input_target(locator, element_id=element_id)
        await self._locator_click_resilient(locator)
        logger.info(
            "playwright_input_applied",
            action="type_text",
            element_id=element_id,
            char_count=len(text or ""),
            text_redacted=redact_sensitive_text_for_log(text),
            current_url=self.page.url if self.page else "",
        )
        try:
            await locator.fill(text, timeout=10_000)
        except Exception:
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(text)
        if submit:
            await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(800)
        header = await self._page_header()
        logger.info(
            "playwright_type_completed",
            element_id=element_id,
            submit=submit,
            final_url=self.page.url,
            title=await self.page.title(),
            page_header_preview=preview_for_log(header, limit=400),
            action="type_text",
        )
        return header

    async def type_secret(
        self, element_id: str, secret_label: str, secret_value: str, submit: bool = False
    ) -> str:
        """Type a secret without logging its plaintext value."""
        await self.ensure_started()
        logger.info(
            "playwright_secret_type_started",
            element_id=element_id,
            secret_label=secret_label,
            submit=submit,
            current_url=self.page.url if self.page else "",
        )
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._assert_text_input_target(locator, element_id=element_id)
        await self._locator_click_resilient(locator)
        logger.info(
            "playwright_input_applied",
            action="type_secret",
            element_id=element_id,
            secret_label=secret_label,
            char_count=len(secret_value or ""),
            current_url=self.page.url if self.page else "",
        )
        try:
            await locator.fill(secret_value, timeout=10_000)
        except Exception:
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(secret_value)
        if submit:
            await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(800)
        logger.info(
            "playwright_secret_type_completed",
            element_id=element_id,
            secret_label=secret_label,
            submit=submit,
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self._page_header()

    async def press_key(self, key: str) -> str:
        """Press a keyboard key such as Enter, Tab, or Escape."""
        await self.ensure_started()
        logger.info(
            "playwright_keypress_started",
            key=key,
            current_url=self.page.url if self.page else "",
        )
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(800)
        logger.info(
            "playwright_keypress_completed",
            key=key,
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self._page_header()

    async def scroll(self, direction: str = "down", amount: int = 900) -> str:
        """Scroll the current page."""
        await self.ensure_started()
        delta = amount if direction.lower() == "down" else -amount
        logger.info(
            "playwright_scroll_started",
            direction=direction,
            amount=amount,
            delta=delta,
            current_url=self.page.url if self.page else "",
        )
        await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(600)
        logger.info(
            "playwright_scroll_completed",
            direction=direction,
            amount=amount,
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self._page_header()

    async def capture_screenshot_data_url(self) -> str:
        """Capture a viewport screenshot and return it as a data URL for multimodal models."""
        await self.ensure_started()
        png_bytes = await self.page.screenshot(type="png", full_page=False)
        encoded = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    async def _page_header(self) -> str:
        return json.dumps(
            {
                "title": await self.page.title(),
                "url": self.page.url,
            },
            ensure_ascii=False,
        )

    async def close(self) -> None:
        """Close the Playwright context and runtime if they were started."""
        try:
            if self.context is not None:
                logger.info(
                    "playwright_session_closing",
                    current_url=self.page.url if self.page else "",
                    page_count=len(self.context.pages),
                )
                await self.context.close()
        finally:
            self.context = None
            self.page = None
            if self.playwright is not None:
                await self.playwright.stop()
                self.playwright = None
            logger.info("playwright_session_closed")
