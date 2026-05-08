"""Tests for browser_reporting (user-visible text from structured facts)."""

from src.thirdhand.services.browser_reporting import (
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

    def test_auth_facts_and_debug_note_surface_in_report(self) -> None:
        report = format_run_summary_telegram(
            goal_display="login",
            trace=["click: {\"id\": 1}"],
            final_message="Остановка автологина.",
            final_url="https://hh.ru/login",
            needs_user_input=True,
            blocker_type="login",
            debug_note="DOM без полей пароля.",
            auth_facts={
                "outcome": "manual_login_assistance_required",
                "reason": "password_username_fields_not_found",
                "site_key": "hh",
                "blocker_class": "user_data_needed",
            },
        )
        assert "Рантайм (факты)" in report
        assert "manual_login_assistance_required" in report
        assert "password_username_fields_not_found" in report
        assert "hh" in report
        assert "user_data_needed" in report
        assert "Примечание рантайма: DOM без полей пароля." in report


class TestFormatPendingBrowserDiagnosticReply:
    def test_prefers_browser_debug_note(self) -> None:
        text = format_pending_browser_diagnostic_reply(
            pending_task={
                "browser_debug_note": "Сервис вернул ошибку авторизации",
                "browser_next_user_action": "ignored",
                "browser_final_url": "https://x/y",
            },
        )
        assert "Сервис вернул ошибку" in text
        assert "ignored" not in text
        assert "https://x/y" in text

    def test_fallback_to_next_action_and_strategy(self) -> None:
        text = format_pending_browser_diagnostic_reply(
            pending_task={
                "browser_debug_note": "",
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
                pending_task={"browser_debug_note": "", "browser_final_url": ""},
            )
            == ""
        )
