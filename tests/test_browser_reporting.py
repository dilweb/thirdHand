"""Tests for browser-core reporting (user-visible text from structured facts)."""

from src.thirdhand.browser_core.reporting import (
    format_pending_browser_diagnostic_reply,
    format_run_summary_telegram,
)


class TestFormatRunSummaryTelegram:
    def test_login_block_report_is_generic_without_remote_browser(self) -> None:
        report = format_run_summary_telegram(
            goal_display="зайти на hh.ru и откликнуться",
            trace=["open_browser: {}", 'goto_url: {"url": "hh.ru"}'],
            final_message="Please provide your hh.ru login and password.",
            final_url="https://hh.ru/account/login",
            needs_user_input=True,
            blocker_type="login",
        )

        assert "Окно браузера" not in report
        assert "сохранённые креды" in report
        assert "Не удалось завершить автоматически" in report
        assert "password" not in report

    def test_generic_block_includes_trace_not_manual_gotovo(self) -> None:
        report = format_run_summary_telegram(
            goal_display="продолжить оформление заказа",
            trace=["click: {}", "inspect_page: ok"],
            final_message="Требуется подтверждение на стороне сайта.",
            final_url="https://example.com/checkout",
            needs_user_input=True,
            blocker_type="confirmation",
        )

        assert "Последние шаги (кратко)" in report
        assert "готово" not in report.lower()

    def test_2fa_report_preserves_specific_user_instruction(self) -> None:
        report = format_run_summary_telegram(
            goal_display="войти на hh.ru",
            trace=[],
            final_message="Пришли одноразовый код из SMS или WhatsApp одним сообщением.",
            final_url="https://hh.ru/account/login",
            needs_user_input=True,
            blocker_type="2fa",
        )

        assert "одноразовый код" in report
        assert "WhatsApp" in report

    def test_report_no_longer_surfaces_legacy_debug_or_auth_facts(self) -> None:
        report = format_run_summary_telegram(
            goal_display="login",
            trace=["click: {\"id\": 1}"],
            final_message="Остановка автологина.",
            final_url="https://hh.ru/login",
            needs_user_input=True,
            blocker_type="login",
        )
        assert "Рантайм (факты)" not in report
        assert "Примечание рантайма" not in report


class TestFormatPendingBrowserDiagnosticReply:
    def test_fallback_to_next_action_and_strategy(self) -> None:
        text = format_pending_browser_diagnostic_reply(
            pending_task={
                "browser_next_user_action": "Введи код",
                "browser_resume_strategy": "await_user_message",
                "browser_final_url": "https://z",
            },
        )
        assert "Введи код" in text
        assert "Стратегия продолжения: await_user_message" in text
        assert "https://z" in text

    def test_empty_when_no_facts(self) -> None:
        assert (
            format_pending_browser_diagnostic_reply(
                pending_task={"browser_final_url": ""},
            )
            == ""
        )
