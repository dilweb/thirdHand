"""Tests for Markdown → Telegram HTML formatting."""

from src.thirdhand.services.telegram_format import (
    escape_telegram_text,
    format_agent_reply_for_telegram,
    markdown_to_telegram_html,
)


class TestFormatAgentReplyForTelegram:
    def test_html_only_passthrough(self) -> None:
        raw = "Готово: <b>важно</b> и <i>пояснение</i>."
        assert format_agent_reply_for_telegram(raw) == raw

    def test_markdown_still_converted(self) -> None:
        raw = "Смотри **жирный** текст."
        out = format_agent_reply_for_telegram(raw)
        assert "<b>жирный</b>" in out
        assert "**" not in out


class TestEscapeTelegramText:
    def test_escapes_special_chars(self) -> None:
        assert escape_telegram_text("a < b & c") == "a &lt; b &amp; c"


class TestMarkdownToTelegramHtml:
    def test_empty(self) -> None:
        assert markdown_to_telegram_html("") == ""

    def test_plain_text_escaped(self) -> None:
        assert "1 &lt; 2" in markdown_to_telegram_html("Use 1 < 2 for compare & co")

    def test_bold_double_asterisk(self) -> None:
        out = markdown_to_telegram_html("Hello **world**!")
        assert "<b>world</b>" in out
        assert "**" not in out

    def test_dunder_preserved(self) -> None:
        """Avoid treating Python dunders like __init__ as bold."""
        out = markdown_to_telegram_html("In `__init__` call super()")
        assert "__init__" in out
        assert "<code>__init__</code>" in out

    def test_inline_code(self) -> None:
        out = markdown_to_telegram_html("Run `rm -rf /` never")
        assert "<code>rm -rf /</code>" in out
        assert "`" not in out

    def test_fenced_code(self) -> None:
        md = "```python\nx = 1 < 2\n```"
        out = markdown_to_telegram_html(md)
        assert "<pre><code>" in out
        assert "1 &lt; 2" in out
        assert "```" not in out

    def test_link(self) -> None:
        out = markdown_to_telegram_html("[Example](https://example.com/?a=1&b=2)")
        assert '<a href="https://example.com/?a=1&amp;b=2">' in out
        assert "Example" in out

    def test_strikethrough(self) -> None:
        out = markdown_to_telegram_html("~~gone~~ stays")
        assert "<s>gone</s>" in out

    def test_italic_asterisk(self) -> None:
        out = markdown_to_telegram_html("This is *fine* today")
        assert "<i>fine</i>" in out

    def test_heading_atx(self) -> None:
        out = markdown_to_telegram_html("## Section\nbody")
        assert "<b>Section</b>" in out

    def test_combined(self) -> None:
        md = "**Tip:** read `help` and see [docs](https://d.example)."
        out = markdown_to_telegram_html(md)
        assert "<b>Tip:</b>" in out
        assert "<code>help</code>" in out
        assert '<a href="https://d.example">' in out
