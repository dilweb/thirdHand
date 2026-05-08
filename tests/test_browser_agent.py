"""Tests for browser agent helpers and browser node wiring."""

from unittest.mock import AsyncMock, patch

import pytest

from src.thirdhand.agent.nodes.browser import run_browser_task_node
from src.thirdhand.agent.state import AgentState
from src.thirdhand.services import (
    browser_agent,
    browser_auth,
    browser_observation,
    browser_recovery,
)
from src.thirdhand.services.browser_flow import (
    _build_auth_guidance,
    _infer_start_url_from_goal,
    _make_browser_run_result,
)


class TestBrowserAgentHelpers:
    def test_extract_login_field_ids_prefers_semantic_fields(self) -> None:
        username_id, password_id = browser_observation.extract_login_field_ids(
            {
                "interactive": [
                    {
                        "id": "a1",
                        "tag": "input",
                        "type": "text",
                        "name": "q",
                        "placeholder": "Поиск",
                    },
                    {
                        "id": "a2",
                        "tag": "input",
                        "type": "email",
                        "name": "email",
                        "placeholder": "Email",
                    },
                    {
                        "id": "a3",
                        "tag": "input",
                        "type": "password",
                        "name": "password",
                        "placeholder": "Пароль",
                    },
                ]
            }
        )

        assert username_id == "a2"
        assert password_id == "a3"

    def test_extract_login_field_ids_uses_autocomplete_hints(self) -> None:
        username_id, password_id = browser_observation.extract_login_field_ids(
            {
                "interactive": [
                    {
                        "id": "x1",
                        "tag": "input",
                        "type": "text",
                        "name": "q",
                        "autocomplete": "off",
                    },
                    {
                        "id": "em",
                        "tag": "input",
                        "type": "text",
                        "name": "x",
                        "autocomplete": "username",
                    },
                    {
                        "id": "pw",
                        "tag": "input",
                        "type": "text",
                        "autocomplete": "current-password",
                        "name": "hidden-pw",
                    },
                ]
            }
        )
        assert username_id == "em"
        assert password_id == "pw"

    def test_infer_start_url_from_goal_extracts_domain(self) -> None:
        assert _infer_start_url_from_goal("зайди на hh.ru и откликнись") == "https://hh.ru"

    def test_infer_start_url_from_goal_returns_empty_without_domain(self) -> None:
        assert _infer_start_url_from_goal("откликнись на вакансии python") == ""

    def test_infer_start_url_from_goal_uses_known_site_alias(self) -> None:
        assert (
            _infer_start_url_from_goal("откликнись на hh на вакансии python разработчик")
            == "https://hh.ru"
        )

    def test_find_interactive_element_id_matches_button_text(self) -> None:
        assert (
            browser_observation.find_interactive_element_id(
                {
                    "interactive": [
                        {"id": "a1", "text": "Дальше"},
                        {"id": "a2", "text": "Войти с паролем"},
                    ]
                },
                ("Войти с паролем",),
            )
            == "a2"
        )

    def test_looks_like_saved_login_opportunity_for_hh_first_step(self) -> None:
        assert browser_auth.looks_like_saved_login_opportunity(
            "hh",
            {
                "body_text_preview": "Поиск работы Телефон Почта Обязательное поле Дальше Войти с паролем",
                "interactive_texts": ["Дальше", "Войти с паролем"],
                "auth_signals": {
                    "login_form_present": False,
                    "has_login_keyword": False,
                },
            },
        )

    def test_build_auth_guidance_for_login_page(self) -> None:
        guidance = _build_auth_guidance(
            "hh",
            """{
              "url": "https://hh.ru/account/login",
              "headings": ["Вход"],
              "interactive": [{"id": "a1", "text": "Войти"}],
              "text": "Вход Телефон Почта Войти с паролем"
            }""",
        )

        assert "sign-in" in guidance or "account steps" in guidance
        assert "inspect_page" in guidance
        assert "ask_user" in guidance

    def test_build_auth_guidance_suppressed_when_probe_indicates_logged_in_shell(self) -> None:
        """Header «Войти» on listings triggers looks_like_login_surface; probe must not push auth copy."""
        snapshot = """{
          "url": "https://hh.ru/search/vacancy?text=python&area=160",
          "headings": ["Найдено вакансий"],
          "interactive": [{"id": "x", "tag": "a", "text": "Войти"}],
          "text": "Вакансии Резюме Отклики Войти"
        }"""
        probe = {
            "auth_signals": {
                "login_form_present": False,
                "has_profile_keyword": True,
                "has_account_menu_keyword": True,
            },
            "url": "https://hh.ru/search/vacancy",
        }
        guidance = _build_auth_guidance("hh", snapshot, probe)
        assert guidance == ""

    def test_should_request_visual_assist_for_auth_page(self) -> None:
        assert browser_recovery.should_request_visual_assist(
            site_key="hh",
            snapshot_json='{"text":"Вход Телефон Почта Войти с паролем","interactive":[]}',
            auth_guidance="This page likely needs sign-in or account steps (hh).",
            recovery_attempt=0,
        )

    def test_make_browser_run_result_completed_has_no_barrier_kind(self) -> None:
        r = _make_browser_run_result(
            goal="g",
            trace=[],
            final_message="done",
            final_url="https://x.test",
            needs_user_input=False,
            blocker_type="other",
            auth_facts={"outcome": "finish_task_completed"},
        )
        assert r.stop_reason == ""
        assert r.barrier_kind == ""
        assert r.resume_strategy == "none"
        assert r.next_user_action == ""
        assert r.barrier_facts.get("page_url") == "https://x.test"

    def test_make_browser_run_result_stopped_checkpoint_resume_strategy(self) -> None:
        r = _make_browser_run_result(
            goal="g",
            trace=[],
            final_message="checkpoint",
            final_url="",
            needs_user_input=True,
            blocker_type="other",
            auth_facts={"outcome": "finish_task_stopped_checkpoint"},
        )
        assert r.barrier_kind == "other"
        assert r.resume_strategy == "continue_after_checkpoint"
        assert r.next_user_action == "checkpoint"

    def test_make_browser_run_result_ask_user_barrier_facts(self) -> None:
        r = _make_browser_run_result(
            goal="g",
            trace=[],
            final_message="Код из SMS?",
            final_url="https://a.test",
            needs_user_input=True,
            blocker_type="2fa",
            auth_facts={
                "facts_version": 1,
                "outcome": "agent_requested_user_input",
                "blocker_type": "2fa",
                "stop_reason": "agent_asked_user",
            },
        )
        assert r.barrier_kind == "2fa"
        assert r.stop_reason == "agent_asked_user"
        assert r.resume_strategy == "await_user_message"
        assert r.next_user_action == "Код из SMS?"
        assert r.barrier_facts.get("barrier_kind") == "2fa"
        assert r.barrier_facts.get("blocker_class") == "user_data_needed"
        assert r.barrier_facts.get("page_url") == "https://a.test"

    def test_make_browser_run_result_includes_screenshot(self) -> None:
        r = _make_browser_run_result(
            goal="g",
            trace=[],
            final_message="captcha",
            final_url="https://a.test",
            needs_user_input=True,
            blocker_type="captcha",
            auth_facts={"facts_version": 1},
            screenshot_png_base64="QUJD",
        )
        assert r.screenshot_png_base64 == "QUJD"


@pytest.mark.asyncio
async def test_run_browser_task_node_maps_structured_barrier_fields() -> None:
    fake = browser_agent.BrowserRunResult(
        telegram_report="report",
        trace=["x"],
        final_url="https://example.com/p",
        needs_user_input=True,
        blocker_type="login",
        debug_note="dbg",
        auth_facts={"facts_version": 1},
        barrier_kind="login",
        barrier_facts={
            "facts_version": 1,
            "barrier_kind": "login",
            "page_url": "https://example.com/p",
        },
        next_user_action="sign in",
        resume_strategy="await_user_message",
        sub_intent="browser_apply_to_targets",
        screenshot_png_base64="dGVzdA==",
        stop_reason="user_must_complete_captcha",
    )
    with patch(
        "src.thirdhand.agent.nodes.browser.run_browser_task", new=AsyncMock(return_value=fake)
    ):
        out = await run_browser_task_node(
            AgentState(user_id=1, browser_goal="do thing", user_profile={}),
        )
    assert out["browser_barrier_kind"] == "login"
    assert out["browser_barrier_facts"]["page_url"] == "https://example.com/p"
    assert out["browser_next_user_action"] == "sign in"
    assert out["browser_resume_strategy"] == "await_user_message"
    assert out["browser_sub_intent"] == "browser_apply_to_targets"
    assert out["browser_screenshot_png_base64"] == "dGVzdA=="
    assert out["browser_stop_reason"] == "user_must_complete_captcha"
