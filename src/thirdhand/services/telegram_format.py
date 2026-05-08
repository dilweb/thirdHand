"""Normalize assistant output to plain text safe for Telegram without parse mode."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _PlainTextHTMLParser(HTMLParser):
    """Convert a small subset of HTML into readable plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.href_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "a":
            href = ""
            for key, value in attrs:
                if key.lower() == "href" and value:
                    href = value
                    break
            self.href_stack.append(href)
        elif tag in {"br"}:
            self.parts.append("\n")
        elif tag in {"p", "div", "pre"}:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a":
            href = self.href_stack.pop() if self.href_stack else ""
            if href:
                self.parts.append(f" ({href})")
        elif tag in {"p", "div", "pre"}:
            if not self.parts or not self.parts[-1].endswith("\n"):
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name}")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def get_text(self) -> str:
        return "".join(self.parts)


def format_agent_reply_for_telegram(text: str) -> str:
    """Convert mixed HTML/Markdown-ish output into plain text."""
    if not text:
        return ""

    normalized = text.replace("\u2060", "")
    normalized = _normalize_markdown_markers(normalized)
    normalized = _html_to_plain_text(normalized)
    normalized = _normalize_markdown_markers(normalized)
    normalized = _normalize_whitespace(normalized)
    return normalized.strip()


def escape_telegram_text(value: str) -> str:
    """Backward-compatible helper now returning plain text."""
    return html.unescape(value or "")


def markdown_to_telegram_html(text: str) -> str:
    """Backward-compatible helper kept for tests and older call sites."""
    return format_agent_reply_for_telegram(text)


def _html_to_plain_text(text: str) -> str:
    parser = _PlainTextHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.get_text()


def _normalize_markdown_markers(text: str) -> str:
    text = re.sub(
        r"```([\w-]*)\n?(.*?)```",
        lambda m: f"{m.group(1).strip()}\n{m.group(2).rstrip()}".strip(),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*([^\s*][^*\n]*?)\*(?!\*)", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return text


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
