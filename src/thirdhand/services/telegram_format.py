"""Convert Markdown-style LLM output to Telegram HTML (parse_mode=HTML)."""

from __future__ import annotations

import html
import re


def format_agent_reply_for_telegram(text: str) -> str:
    """Format assistant text for Telegram HTML mode.

    If the message looks like Markdown, convert it. Otherwise return it
    unchanged so prompts that ask for native HTML tags still work.
    """
    if not text:
        return ""
    if _looks_like_markdown(text):
        return markdown_to_telegram_html(text)
    return text


def _looks_like_markdown(text: str) -> bool:
    """Heuristic: avoid mangling replies that are already HTML-only."""
    if "```" in text or "**" in text or "~~" in text:
        return True
    if re.search(r"(?<![\\`])`[^`\n]+`", text):
        return True
    if re.search(r"(?m)^#{1,6}\s+\S", text):
        return True
    if re.search(r"\[[^\]\n]+\]\([^)\n]+\)", text):
        return True
    if re.search(r"(?<!\*)\*[^\s*][^*\n]*?\*(?!\*)", text):
        return True
    return False


def escape_telegram_text(value: str) -> str:
    """Escape plain text for safe inclusion inside Telegram HTML messages."""
    return html.escape(value, quote=False)


def markdown_to_telegram_html(text: str) -> str:
    """Turn common Markdown patterns into Telegram-supported HTML.

    Escapes raw ``<``, ``>``, ``&`` first, then applies formatting so injected
    markup from the model cannot break parsing. Handles fenced and inline
    code, ``**bold**`` (not ``__dunder__``), italic, strikethrough, links, and
    ATX headings.
    """
    if not text:
        return ""

    chunks: list[str] = []
    pos = 0
    fence = re.compile(r"```([\w-]*)\n?(.*?)```", re.DOTALL)

    for m in fence.finditer(text):
        if m.start() > pos:
            chunks.append(_format_raw_segment(text[pos : m.start()]))
        code_body = m.group(2)
        if code_body.endswith("\n"):
            code_body = code_body[:-1]
        escaped = html.escape(code_body, quote=False)
        chunks.append(f"<pre><code>{escaped}</code></pre>")
        pos = m.end()

    if pos < len(text):
        chunks.append(_format_raw_segment(text[pos:]))

    return "".join(chunks)


def _format_raw_segment(segment: str) -> str:
    """Format one segment outside fenced code blocks."""
    parts: list[str] = []
    pos = 0
    inline = re.compile(r"`([^`\n]+)`")

    for m in inline.finditer(segment):
        if m.start() > pos:
            parts.append(_markdown_on_escaped(segment[pos : m.start()]))
        inner = html.escape(m.group(1), quote=False)
        parts.append(f"<code>{inner}</code>")
        pos = m.end()

    if pos < len(segment):
        parts.append(_markdown_on_escaped(segment[pos:]))

    return "".join(parts)


def _markdown_on_escaped(segment: str) -> str:
    """Apply Markdown replacements to a segment that may still contain MD syntax."""
    s = html.escape(segment, quote=False)
    s = _atx_headers(s)
    s = _replace_links(s)
    s = _replace_bold_double_asterisk(s)
    s = _replace_strikethrough(s)
    s = _replace_italic_asterisk(s)
    return s


def _atx_headers(s: str) -> str:
    """Turn ATX headings (# .. ######) into bold lines."""

    def repl(m: re.Match[str]) -> str:
        title = m.group(1)
        return f"<b>{title}</b>"

    return re.sub(r"(?m)^#{1,6}\s+(.+)$", repl, s)


def _replace_links(s: str) -> str:
    """[label](url) -> anchor; href and label are already entity-safe except brackets."""

    def repl(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        label_esc = html.escape(html.unescape(label), quote=False)
        url_clean = html.unescape(url).strip()
        url_esc = html.escape(url_clean, quote=True)
        return f'<a href="{url_esc}">{label_esc}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, s)


def _replace_bold_double_asterisk(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        return f"<b>{inner}</b>"

    return re.sub(r"\*\*(.+?)\*\*", repl, s, flags=re.DOTALL)


def _replace_strikethrough(s: str) -> str:
    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        return f"<s>{inner}</s>"

    return re.sub(r"~~(.+?)~~", repl, s, flags=re.DOTALL)


def _replace_italic_asterisk(s: str) -> str:
    """Single *pair*; inner must not start with whitespace or * (skip list markers)."""

    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        return f"<i>{inner}</i>"

    return re.sub(r"\*([^\s*][^*]*?)\*", repl, s, flags=re.DOTALL)
