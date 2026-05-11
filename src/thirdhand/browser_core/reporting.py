"""User-visible browser reporting derived from structured runtime facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.thirdhand.config import settings


def format_run_summary_telegram(
    *,
    goal_display: str = "",
    goal_internal: str = "",
    trace: list[str],
    final_message: str,
    final_url: str,
    needs_user_input: bool,
    blocker_type: str = "other",
) -> str:
    """Plain-text summary for the user after a browser run."""
    status = "Не удалось завершить автоматически" if needs_user_input else "Готово"
    normalized_message = final_message
    lowered_message = normalized_message.lower()
    if (
        needs_user_input
        and blocker_type == "login"
        and (
            not normalized_message
            or any(
                token in lowered_message
                for token in ("password", "парол", "логин", "credentials", "кред")
            )
        )
    ):
        normalized_message = (
            "Вход не завершён автоматически; проверь сохранённые креды для сайта или форму на странице."
        )
    elif needs_user_input and blocker_type == "2fa" and not normalized_message:
        normalized_message = "Ожидается одноразовый код или подтверждение второго фактора."
    elif needs_user_input and blocker_type == "captcha" and not normalized_message:
        normalized_message = "Страница запросила captcha; автоматический обход не выполнялся."
    title = (goal_display or "").strip()
    if not title and goal_internal:
        goal_internal_one_line = " ".join(goal_internal.split())
        title = (
            goal_internal_one_line
            if len(goal_internal_one_line) <= 240
            else f"{goal_internal_one_line[:239]}…"
        )
    if not title:
        title = "(задача не указана)"
    lines = [status, f"Задача: {title}"]
    if normalized_message:
        lines.append(f"Итог: {normalized_message}")
    if final_url:
        lines.append(f"Текущая страница: {final_url}")

    if trace and (settings.BROWSER_REPORT_VERBOSE or not needs_user_input):
        lines.append("")
        lines.append("Шаги и результаты инструментов (последние):")
        tail = trace[-15:] if settings.BROWSER_REPORT_VERBOSE else trace[-5:]
        for item in tail:
            item_s = item if len(item) <= 500 else f"{item[:499]}…"
            lines.append(f"• {item_s}")
    elif trace and needs_user_input:
        lines.append("")
        lines.append("Последние шаги (кратко): показано 3 из журнала; полный лог — при BROWSER_REPORT_VERBOSE.")
        for item in trace[-3:]:
            item_s = item if len(item) <= 320 else f"{item[:319]}…"
            lines.append(f"• {item_s}")
    return "\n".join(lines)


def format_pending_browser_diagnostic_reply(*, pending_task: Mapping[str, Any]) -> str:
    """Build plain text for short 'what blocked' questions from pending structured fields."""
    next_action = str(pending_task.get("browser_next_user_action", "") or "").strip()
    resume_strategy = str(pending_task.get("browser_resume_strategy", "") or "").strip()
    stop_reason = str(pending_task.get("browser_stop_reason", "") or "").strip()
    response_text = "\n".join(
        part
        for part in (
            next_action,
            f"Стратегия продолжения: {resume_strategy}" if resume_strategy else "",
            f"Код остановки: {stop_reason}" if stop_reason else "",
        )
        if part
    )
    if not response_text:
        return ""
    if stop_reason and "Код остановки:" not in response_text:
        response_text = f"{response_text}\nКод остановки: {stop_reason}"
    if pending_task.get("browser_final_url"):
        return f"{response_text}\nТекущая страница: {pending_task.get('browser_final_url')}"
    return response_text
