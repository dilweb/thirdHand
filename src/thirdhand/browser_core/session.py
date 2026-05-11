"""Low-level Playwright session for the new browser core."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import structlog

from src.thirdhand.browser_core import inspect as browser_inspect
from src.thirdhand.config import settings
from src.thirdhand.services.llm import preview_for_log, redact_sensitive_text_for_log

logger = structlog.get_logger(__name__)

_FILLABLE_TARGET_JS = """
(el) => {
  if (!el) {
    return { ok: false, reason: "element_not_found" };
  }
  const t = el.tagName.toLowerCase();
  if (t === "textarea" || t === "select") {
    return { ok: true };
  }
  if (t === "input") {
    const ty = (el.getAttribute("type") || "text").toLowerCase();
    const blocked = new Set([
      "button", "submit", "reset", "image", "checkbox", "radio",
      "hidden", "file", "range", "color"
    ]);
    if (blocked.has(ty)) {
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
    """Persistent Playwright session and neutral browser actions."""

    def __init__(self) -> None:
        self.playwright = None
        self.context = None
        self.page = None

    async def _await_phase(
        self, phase_name: str, awaitable: Any, timeout_seconds: float, **log_fields: Any
    ) -> Any:
        logger.info(f"{phase_name}_started", timeout_seconds=timeout_seconds, **log_fields)
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
        """Start the persistent Chromium context lazily."""
        if self.page is not None:
            logger.info(
                "browser_core_session_reused",
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

        self.playwright = await self._await_phase(
            "browser_core_playwright_runtime",
            async_playwright().start(),
            timeout_seconds=20,
            profile_dir=str(profile_dir),
        )
        self.context = await self._await_phase(
            "browser_core_context_launch",
            self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=settings.BROWSER_HEADLESS,
                viewport={"width": 1440, "height": 960},
            ),
            timeout_seconds=45,
            profile_dir=str(profile_dir),
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        logger.info(
            "browser_core_session_started",
            page_count=len(self.context.pages),
            page_urls=[page.url for page in self.context.pages],
            active_url=self.page.url,
        )

    async def open_browser(self, start_url: str = "") -> str:
        """Ensure browser exists and optionally navigate."""
        await self.ensure_started()
        if start_url.strip():
            await self.goto_url(start_url)
        return await self.page_header()

    async def goto_url(self, url: str) -> str:
        """Navigate to a URL, adding https when scheme is omitted."""
        await self.ensure_started()
        normalized = url.strip()
        if normalized and "://" not in normalized:
            normalized = f"https://{normalized}"
        logger.info(
            "browser_core_goto_started",
            requested_url=url,
            normalized_url=normalized,
            current_url=self.page.url if self.page else "",
        )
        await self.page.goto(normalized, wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_timeout(1_000)
        logger.info(
            "browser_core_goto_completed",
            final_url=self.page.url,
            title=await self.page.title(),
        )
        return await self.page_header()

    async def inspect_page(self) -> str:
        """Return the neutral page snapshot used by the new browser core."""
        await self.ensure_started()
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return await browser_inspect.inspect_page(self.page)
            except Exception as exc:
                last_exc = exc
                text = str(exc)
                if "Execution context was destroyed" not in text:
                    raise
                logger.warning(
                    "browser_core_inspect_retry_after_navigation",
                    attempt=attempt + 1,
                    current_url=self.page.url if self.page else "",
                    error=text,
                )
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except Exception:
                    pass
                await self.page.wait_for_timeout(500)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("inspect_page failed without a captured exception")

    async def wait(self, seconds: float = 2.0) -> str:
        """Pause for async UI updates and return page header."""
        await self.ensure_started()
        await self.page.wait_for_timeout(int(seconds * 1000))
        return await self.page_header()

    async def current_url(self) -> str:
        await self.ensure_started()
        return self.page.url

    async def session_probe(self) -> dict[str, Any]:
        await self.ensure_started()
        return await browser_inspect.session_probe(self.page, self.context)

    async def page_header(self) -> str:
        return json.dumps(
            {"title": await self.page.title(), "url": self.page.url},
            ensure_ascii=False,
        )

    async def _assert_text_input_target(self, locator: Any, *, element_id: str) -> None:
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
            f"Refusing to type into non-text element (id={element_id}, {reason})."
        )

    async def _locator_click_resilient(self, locator: Any) -> None:
        """Click through minor UI overlays before giving up."""
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
        """Click an element by page-local id."""
        await self.ensure_started()
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._locator_click_resilient(locator)
        await self.page.wait_for_timeout(1_000)
        header = await self.page_header()
        logger.info(
            "browser_core_click_completed",
            element_id=element_id,
            final_url=self.page.url,
            title=await self.page.title(),
            page_header_preview=preview_for_log(header, limit=1000),
        )
        return header

    async def click_by_text(self, text_needle: str, exact: bool = False) -> str:
        """Click an element by visible text."""
        await self.ensure_started()
        locator = (
            self.page.get_by_text(text_needle, exact=True).first
            if exact
            else self.page.get_by_text(text_needle).first
        )
        await self._locator_click_resilient(locator)
        await self.page.wait_for_timeout(1_000)
        return await self.page_header()

    async def type_text(self, element_id: str, text: str, submit: bool = False) -> str:
        """Type into an input-like element."""
        await self.ensure_started()
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._assert_text_input_target(locator, element_id=element_id)
        await self._locator_click_resilient(locator)
        logger.info(
            "browser_core_type_started",
            element_id=element_id,
            submit=submit,
            text_char_count=len(text or ""),
            text_redacted=redact_sensitive_text_for_log(text),
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
        return await self.page_header()

    async def type_by_label(
        self, label_needle: str, text: str, submit: bool = False, exact: bool = False
    ) -> str:
        """Type into a field using its associated label."""
        await self.ensure_started()
        locator = (
            self.page.get_by_label(label_needle, exact=True)
            if exact
            else self.page.get_by_label(label_needle)
        )
        await locator.scroll_into_view_if_needed(timeout=5_000)
        await locator.clear(timeout=5_000)
        await locator.fill(text, timeout=10_000)
        if submit:
            await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(1_000)
        return await self.page_header()

    async def type_by_placeholder(
        self, placeholder_needle: str, text: str, submit: bool = False, exact: bool = False
    ) -> str:
        """Type into a field using its placeholder text."""
        await self.ensure_started()
        locator = (
            self.page.get_by_placeholder(placeholder_needle, exact=True)
            if exact
            else self.page.get_by_placeholder(placeholder_needle)
        )
        await locator.scroll_into_view_if_needed(timeout=5_000)
        await locator.clear(timeout=5_000)
        await locator.fill(text, timeout=10_000)
        if submit:
            await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(1_000)
        return await self.page_header()

    async def type_secret(
        self, element_id: str, secret_label: str, secret_value: str, submit: bool = False
    ) -> str:
        """Type a secret value without logging plaintext."""
        await self.ensure_started()
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await self._assert_text_input_target(locator, element_id=element_id)
        await self._locator_click_resilient(locator)
        logger.info(
            "browser_core_secret_type_started",
            element_id=element_id,
            secret_label=secret_label,
            submit=submit,
            char_count=len(secret_value or ""),
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
        return await self.page_header()

    async def press_key(self, key: str) -> str:
        """Press a keyboard key and return page header."""
        await self.ensure_started()
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(800)
        return await self.page_header()

    async def scroll(self, direction: str = "down", amount: int = 900) -> str:
        """Scroll the current page."""
        await self.ensure_started()
        delta = amount if direction.lower() == "down" else -amount
        await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(600)
        return await self.page_header()

    async def capture_screenshot_data_url(self) -> str:
        """Capture viewport screenshot as a data URL."""
        await self.ensure_started()
        png_bytes = await self.page.screenshot(type="png", full_page=False)
        encoded = base64.b64encode(png_bytes).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    async def close(self) -> None:
        """Close the current Playwright session if started."""
        try:
            if self.context is not None:
                await self.context.close()
        finally:
            self.context = None
            self.page = None
            if self.playwright is not None:
                await self.playwright.stop()
                self.playwright = None
