"""Unit tests for deterministic browser barrier classification."""

from src.thirdhand.services import browser_auth
from src.thirdhand.services.browser_site_registry import infer_site_key_from_url


def test_classify_barrier_hh_service_selection_snapshot() -> None:
    snapshot = {
        "text": "Выберите сервис для входа вот кнопки",
        "interactive": [
            {"id": "g1", "text": "Госуслуги"},
            {"id": "g2", "text": "Google"},
        ],
    }
    probe = {
        "body_text_preview": snapshot["text"],
        "interactive_texts": ["Госуслуги"],
        "auth_signals": {"login_form_present": False, "has_login_keyword": False},
    }
    c = browser_auth.classify_browser_barrier("hh", probe, snapshot)
    assert c.oauth_service_selection_barrier is True
    assert "Госуслуги" in c.visible_oauth_providers


def test_oauth_service_selection_inactive_without_phrase() -> None:
    snapshot = {
        "text": "Просто страница входа",
        "interactive": [{"id": "g1", "text": "Google"}],
    }
    assert browser_auth.oauth_service_selection_options_if_active("hh", snapshot) == []


def test_oauth_service_selection_generic_unknown_site() -> None:
    snapshot = {
        "text": "Choose a sign-in method",
        "interactive": [{"id": "g1", "text": "Google"}, {"id": "a1", "text": "Apple"}],
    }
    options = browser_auth.oauth_service_selection_options_if_active("unknown.example", snapshot)
    assert "Google" in options
    assert "Apple" in options


def test_looks_like_login_surface_via_password_field() -> None:
    snap = {
        "url": "",
        "text": "",
        "interactive": [{"id": "p1", "tag": "input", "type": "password", "name": "pwd"}],
    }
    assert browser_auth.looks_like_login_surface(snap) is True


def test_looks_like_code_challenge_tokens() -> None:
    snap = {"text": "Введите код из смс", "interactive": []}
    assert browser_auth.looks_like_code_challenge(snap) is True


def test_looks_like_code_challenge_rejects_lone_kod_token() -> None:
    snap = {"text": "Получите код скидки на главной странице", "interactive": []}
    assert browser_auth.looks_like_code_challenge(snap) is False


def test_looks_like_code_challenge_accepts_one_time_autocomplete() -> None:
    snap = {
        "text": "",
        "interactive": [{"id": "x", "autocomplete": "one-time-code", "tag": "input", "type": "text"}],
    }
    assert browser_auth.looks_like_code_challenge(snap) is True


def test_snapshot_allows_ask_user_2fa_requires_code_field() -> None:
    """Text that matches code-challenge copy is not enough without a plausible OTP input."""
    snap = {"text": "Введите код из смс", "interactive": []}
    assert browser_auth.looks_like_code_challenge(snap) is True
    assert browser_auth.snapshot_allows_ask_user_2fa(snap) is False


def test_snapshot_allows_ask_user_2fa_hh_applicant_choice_screen() -> None:
    snap = {
        "text": "Вход Я ищу работу Профиль соискателя Я ищу сотрудников Профиль работодателя Войти",
        "headings": ["Вход"],
        "interactive": [
            {"id": "b1", "tag": "button", "text": "Войти", "type": "submit", "fillable": False},
            {"id": "r1", "tag": "input", "type": "radio", "name": "account-type", "fillable": False},
        ],
    }
    assert browser_auth.snapshot_allows_ask_user_2fa(snap) is False


def test_snapshot_allows_ask_user_2fa_accepts_one_time_autocomplete() -> None:
    snap = {
        "text": "Код отправлен",
        "interactive": [
            {
                "id": "x",
                "tag": "input",
                "type": "text",
                "autocomplete": "one-time-code",
                "fillable": True,
            }
        ],
    }
    assert browser_auth.snapshot_allows_ask_user_2fa(snap) is True


def test_snapshot_allows_ask_user_2fa_accepts_code_placeholder() -> None:
    snap = {
        "text": "Подтверждение",
        "interactive": [
            {
                "id": "c1",
                "tag": "input",
                "type": "text",
                "placeholder": "Код из смс",
                "fillable": True,
            }
        ],
    }
    assert browser_auth.snapshot_allows_ask_user_2fa(snap) is True


def test_infer_site_key_from_url_uses_aliases() -> None:
    assert infer_site_key_from_url("https://www.hh.ru/account/login") == "hh"


def test_unknown_site_no_saved_login_probe_match() -> None:
    probe = {
        "url": "https://unknown.example/",
        "body_text_preview": "foo",
        "interactive_texts": [],
        "auth_signals": {"login_form_present": False, "has_login_keyword": False},
    }
    assert browser_auth.looks_like_saved_login_opportunity("unknown.example", probe) is False


def test_unknown_site_generic_saved_login_probe_match() -> None:
    probe = {
        "url": "https://vendor.example/",
        "body_text_preview": "Sign in with your email or phone number",
        "interactive_texts": ["Continue", "Log in"],
        "auth_signals": {"login_form_present": False, "has_login_keyword": False},
    }
    assert browser_auth.looks_like_saved_login_opportunity("vendor.example", probe) is True


def test_saved_login_opportunity_login_url_without_probe_hints() -> None:
    probe = {
        "url": "https://vendor.example/account/login?next=/",
        "body_text_preview": "hello",
        "interactive_texts": [],
        "auth_signals": {"login_form_present": False, "has_login_keyword": False},
    }
    assert browser_auth.looks_like_saved_login_opportunity("vendor.example", probe) is True


def test_saved_login_auth_facts_stable_shape() -> None:
    facts = browser_auth.saved_login_auth_facts(
        "oauth_provider_selection_required",
        site_key="hh",
        oauth_providers_visible=["Google", "Госуслуги"],
        empty_should_drop=None,
        empty_list=[],
    )
    assert facts["facts_version"] == 1
    assert facts["source"] == "saved_login_auto"
    assert facts["outcome"] == "oauth_provider_selection_required"
    assert facts["site_key"] == "hh"
    assert facts["oauth_providers_visible"] == ["Google", "Госуслуги"]
    assert "empty_should_drop" not in facts
    assert "empty_list" not in facts
