"""Autonomous browser agent built on Playwright and tool calling."""

from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from src.thirdhand.config import settings
from src.thirdhand.services.llm import create_llm

logger = structlog.get_logger(__name__)

_BROWSER_RUN_LOCK = asyncio.Lock()


@dataclass
class BrowserRunResult:
    """Final result from a browser automation run."""

    telegram_report: str
    trace: list[str]
    final_url: str
    needs_user_input: bool = False


class BrowserSession:
    """Thin wrapper around a persistent Playwright browser context."""

    def __init__(self) -> None:
        self.playwright = None
        self.context = None
        self.page = None

    async def ensure_started(self) -> None:
        """Start a persistent browser context lazily."""
        if self.page is not None:
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

        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=settings.BROWSER_HEADLESS,
            viewport={"width": 1440, "height": 960},
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    async def open_browser(self, start_url: str = "") -> str:
        """Ensure a visible browser is available and optionally navigate."""
        await self.ensure_started()
        if start_url.strip():
            await self.goto_url(start_url)
        return await self._page_header()

    async def goto_url(self, url: str) -> str:
        """Navigate to a URL, adding a scheme if needed."""
        await self.ensure_started()
        normalized = url.strip()
        if normalized and "://" not in normalized:
            normalized = f"https://{normalized}"
        await self.page.goto(normalized, wait_until="domcontentloaded", timeout=30_000)
        await self.page.wait_for_timeout(1_000)
        return await self._page_header()

    async def inspect_page(self) -> str:
        """Return a compact snapshot of the current page for the LLM."""
        await self.ensure_started()
        snapshot = await self.page.evaluate(
            """
            ({ textLimit }) => {
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

              const interactive = [];
              for (const el of document.querySelectorAll(interactiveSelector)) {
                if (!isVisible(el)) continue;
                if (!el.dataset.thirdhandId) {
                  el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2, 10);
                }
                interactive.push({
                  id: el.dataset.thirdhandId,
                  tag: el.tagName.toLowerCase(),
                  text: textOf(el).slice(0, 120),
                  type: el.getAttribute("type") || "",
                  role: el.getAttribute("role") || "",
                  name: el.getAttribute("name") || "",
                  placeholder: el.getAttribute("placeholder") || "",
                  href: el.getAttribute("href") || ""
                });
                if (interactive.length >= 60) break;
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
            """,
            {"textLimit": settings.BROWSER_SNAPSHOT_TEXT_LIMIT},
        )
        return json.dumps(snapshot, ensure_ascii=False, indent=2)

    async def click(self, element_id: str) -> str:
        """Click an element by dynamic page-local id."""
        await self.ensure_started()
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await locator.scroll_into_view_if_needed()
        await locator.click(timeout=10_000)
        await self.page.wait_for_timeout(1_000)
        return await self._page_header()

    async def type_text(self, element_id: str, text: str, submit: bool = False) -> str:
        """Type into an input-like element, with fallback to keyboard typing."""
        await self.ensure_started()
        locator = self.page.locator(f'[data-thirdhand-id="{element_id}"]').first
        await locator.scroll_into_view_if_needed()
        await locator.click(timeout=10_000)
        try:
            await locator.fill(text, timeout=10_000)
        except Exception:
            await self.page.keyboard.press("Control+A")
            await self.page.keyboard.press("Backspace")
            await self.page.keyboard.type(text)
        if submit:
            await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(800)
        return await self._page_header()

    async def press_key(self, key: str) -> str:
        """Press a keyboard key such as Enter, Tab, or Escape."""
        await self.ensure_started()
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(800)
        return await self._page_header()

    async def scroll(self, direction: str = "down", amount: int = 900) -> str:
        """Scroll the current page."""
        await self.ensure_started()
        delta = amount if direction.lower() == "down" else -amount
        await self.page.mouse.wheel(0, delta)
        await self.page.wait_for_timeout(600)
        return await self._page_header()

    async def wait_for_page(self, seconds: float = 2.0) -> str:
        """Pause briefly for async UI updates."""
        await self.ensure_started()
        await self.page.wait_for_timeout(int(seconds * 1000))
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

    async def _page_header(self) -> str:
        return json.dumps(
            {
                "title": await self.page.title(),
                "url": self.page.url,
            },
            ensure_ascii=False,
        )


def _build_tools(session: BrowserSession) -> dict[str, StructuredTool]:
    """Create the browser toolset used by the agent loop."""

    async def finish_task(summary: str) -> str:
        return summary

    async def ask_user(question: str) -> str:
        return question

    tools = [
        StructuredTool.from_function(
            coroutine=session.open_browser,
            name="open_browser",
            description="Open or focus the persistent browser. Optionally pass a site URL.",
        ),
        StructuredTool.from_function(
            coroutine=session.goto_url,
            name="goto_url",
            description="Navigate to a URL or domain when you know where to go next.",
        ),
        StructuredTool.from_function(
            coroutine=session.inspect_page,
            name="inspect_page",
            description="Read the current page. Returns title, URL, visible text, headings, and clickable/typeable elements with dynamic ids.",
        ),
        StructuredTool.from_function(
            coroutine=session.read_page,
            name="read_page",
            description="Read only the visible page text when you need more semantic context.",
        ),
        StructuredTool.from_function(
            coroutine=session.click,
            name="click",
            description="Click a visible element using its dynamic id from inspect_page.",
        ),
        StructuredTool.from_function(
            coroutine=session.type_text,
            name="type_text",
            description="Type text into an element id from inspect_page. Use submit=true when Enter should be pressed after typing.",
        ),
        StructuredTool.from_function(
            coroutine=session.press_key,
            name="press_key",
            description="Press a keyboard key such as Enter, Tab, Escape, ArrowDown, or Control+L.",
        ),
        StructuredTool.from_function(
            coroutine=session.scroll,
            name="scroll",
            description="Scroll the page up or down to reveal more content.",
        ),
        StructuredTool.from_function(
            coroutine=session.wait_for_page,
            name="wait_for_page",
            description="Wait a short time for navigation, animations, network requests, or lazy rendering.",
        ),
        StructuredTool.from_function(
            coroutine=finish_task,
            name="finish_task",
            description="Call this only when the task is fully completed or you intentionally stopped before final confirmation. Put the final report in summary.",
        ),
        StructuredTool.from_function(
            coroutine=ask_user,
            name="ask_user",
            description="Ask the user only when the task is blocked by missing information, login, captcha, 2FA, or a risky final confirmation decision.",
        ),
    ]
    return {tool.name: tool for tool in tools}


def _system_prompt(context_text: str) -> str:
    """Build the browser agent instruction prompt."""
    context_block = f"\nUser context:\n{context_text}\n" if context_text else ""
    return (
        "You are an autonomous browser agent controlling a real Chromium window.\n"
        "Your job is to complete arbitrary multi-step web tasks safely and independently.\n"
        "Use the tools to inspect pages, infer what elements mean from visible context, and decide the next action in the moment.\n"
        "Do not assume hardcoded routes, selectors, or button labels in advance.\n"
        "Inspect before acting, especially after navigation or large UI changes.\n"
        "Stop and call ask_user if you need credentials, a captcha is shown, 2FA is required, the page asks for sensitive confirmation, or the instruction is ambiguous.\n"
        "Prefer finish_task once the requested task is done, or when you intentionally stopped at a safe checkpoint such as the last payment confirmation screen.\n"
        "Keep moving; do not narrate to the user on every step. Use tools.\n"
        f"{context_block}"
    )


def _shorten(value: str, limit: int = 300) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _format_report(goal: str, trace: list[str], final_message: str, final_url: str, needs_user_input: bool) -> str:
    """Format the final Telegram-safe report."""
    goal_html = html.escape(goal, quote=False)
    status = "Нужно твоё действие" if needs_user_input else "Готово"
    lines = [f"<b>{status}</b>", f"<b>Задача:</b> {goal_html}"]
    if final_message:
        lines.append(f"<b>Итог:</b> {html.escape(final_message, quote=False)}")
    if final_url:
        safe_url = html.escape(final_url, quote=True)
        lines.append(f'<b>Текущая страница:</b> <a href="{safe_url}">{safe_url}</a>')
    if trace:
        lines.append("<b>Что сделал:</b>")
        for item in trace[-6:]:
            lines.append(f"• {html.escape(item, quote=False)}")
    return "\n".join(lines)


async def run_browser_task(goal: str, user_id: int, context_text: str = "") -> BrowserRunResult:
    """Run a browser task through a generic tool-calling loop."""
    async with _BROWSER_RUN_LOCK:
        session = BrowserSession()
        tools = _build_tools(session)
        llm = create_llm(temperature=0.0).bind_tools(list(tools.values()))

        messages = [
            SystemMessage(content=_system_prompt(context_text)),
            HumanMessage(
                content=(
                    "Выполни задачу в браузере максимально автономно.\n"
                    f"Задача пользователя: {goal}"
                )
            ),
        ]
        trace: list[str] = []
        final_message = ""
        needs_user_input = False

        logger.info("browser_task_started", user_id=user_id, goal=goal)

        for step in range(settings.BROWSER_MAX_STEPS):
            ai_message = await llm.ainvoke(messages)
            messages.append(ai_message)

            tool_calls = getattr(ai_message, "tool_calls", None) or []
            if not tool_calls:
                trace.append(f"Шаг {step + 1}: модель не вызвала инструмент, завершаю.")
                final_message = (
                    "Агент остановился без явного завершения. Проверь открытую страницу и при необходимости уточни следующий шаг."
                )
                needs_user_input = True
                break

            for call in tool_calls:
                tool_name = call["name"]
                args = call.get("args", {}) or {}

                if tool_name == "finish_task":
                    final_message = str(args.get("summary", "")).strip()
                    trace.append(f"Финиш: {_shorten(final_message)}")
                    final_url = await session.current_url()
                    logger.info("browser_task_finished", user_id=user_id, url=final_url)
                    return BrowserRunResult(
                        telegram_report=_format_report(goal, trace, final_message, final_url, False),
                        trace=trace,
                        final_url=final_url,
                        needs_user_input=False,
                    )

                if tool_name == "ask_user":
                    final_message = str(args.get("question", "")).strip()
                    trace.append(f"Нужна помощь пользователя: {_shorten(final_message)}")
                    final_url = await session.current_url()
                    logger.info("browser_task_blocked", user_id=user_id, url=final_url)
                    return BrowserRunResult(
                        telegram_report=_format_report(goal, trace, final_message, final_url, True),
                        trace=trace,
                        final_url=final_url,
                        needs_user_input=True,
                    )

                tool = tools[tool_name]
                try:
                    result = await tool.ainvoke(args)
                except Exception as exc:
                    result = f"ERROR: {type(exc).__name__}: {exc}"
                    logger.warning(
                        "browser_tool_failed",
                        user_id=user_id,
                        tool_name=tool_name,
                        error=str(exc),
                    )

                trace.append(f"{tool_name}: {_shorten(json.dumps(args, ensure_ascii=False))}")
                messages.append(
                    ToolMessage(
                        content=result if isinstance(result, str) else json.dumps(result, ensure_ascii=False),
                        tool_call_id=call["id"],
                    )
                )

        final_url = ""
        try:
            final_url = await session.current_url()
        except Exception:
            final_url = ""

        final_message = final_message or "Достигнут лимит шагов. Нужна следующая инструкция или ещё один запуск."
        needs_user_input = True
        logger.info("browser_task_step_limit", user_id=user_id, goal=goal, url=final_url)
        return BrowserRunResult(
            telegram_report=_format_report(goal, trace, final_message, final_url, needs_user_input),
            trace=trace,
            final_url=final_url,
            needs_user_input=needs_user_input,
        )
