"""Tests for plain-text Telegram formatting."""

from src.thirdhand.services.telegram_format import (
    escape_telegram_text,
    format_agent_reply_for_telegram,
    markdown_to_telegram_html,
)


class TestFormatAgentReplyForTelegram:
    def test_html_is_stripped_to_plain_text(self) -> None:
        raw = "Готово: <b>важно</b> и <i>пояснение</i>."
        assert format_agent_reply_for_telegram(raw) == "Готово: важно и пояснение."

    def test_unsupported_html_tags_are_removed(self) -> None:
        raw = "<p>text</p><b>ok</b>"
        out = format_agent_reply_for_telegram(raw)
        assert out == "text\nok"

    def test_markdown_is_flattened_to_plain_text(self) -> None:
        raw = "Смотри **жирный** текст."
        out = format_agent_reply_for_telegram(raw)
        assert "жирный" in out
        assert "**" not in out


class TestEscapeTelegramText:
    def test_returns_plain_text(self) -> None:
        assert escape_telegram_text("a < b &amp; c") == "a < b & c"


class TestMarkdownToTelegramHtml:
    def test_empty(self) -> None:
        assert markdown_to_telegram_html("") == ""

    def test_plain_text_passthrough(self) -> None:
        assert "Use 1 < 2 for compare & co" == markdown_to_telegram_html(
            "Use 1 < 2 for compare & co"
        )

    def test_bold_double_asterisk(self) -> None:
        out = markdown_to_telegram_html("Hello **world**!")
        assert "Hello world!" == out
        assert "**" not in out

    def test_dunder_preserved(self) -> None:
        out = markdown_to_telegram_html("In `__init__` call super()")
        assert "__init__" in out

    def test_inline_code(self) -> None:
        out = markdown_to_telegram_html("Run `rm -rf /` never")
        assert "rm -rf /" in out
        assert "`" not in out

    def test_fenced_code(self) -> None:
        md = "```python\nx = 1 < 2\n```"
        out = markdown_to_telegram_html(md)
        assert "python\nx = 1 < 2" in out
        assert "```" not in out

    def test_link(self) -> None:
        out = markdown_to_telegram_html("[Example](https://example.com/?a=1&b=2)")
        assert out == "Example (https://example.com/?a=1&b=2)"

    def test_strikethrough(self) -> None:
        out = markdown_to_telegram_html("~~gone~~ stays")
        assert out == "gone stays"

    def test_italic_asterisk(self) -> None:
        out = markdown_to_telegram_html("This is *fine* today")
        assert out == "This is fine today"

    def test_heading_atx(self) -> None:
        out = markdown_to_telegram_html("## Section\nbody")
        assert "Section\nbody" == out

    def test_combined(self) -> None:
        md = "**Tip:** read `help` and see [docs](https://d.example)."
        out = markdown_to_telegram_html(md)
        assert out == "Tip: read help and see docs (https://d.example)."
