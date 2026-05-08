"""Barrier classification for browser automation (login surfaces, OTP, OAuth hints)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from urllib.parse import urlparse

import structlog

from src.thirdhand.services.browser_observation import extract_login_field_ids
from src.thirdhand.services.browser_site_registry import get_known_oauth_provider_labels

logger = structlog.get_logger(__name__)

_GENERIC_SAVED_LOGIN_BODY_MARKERS: tuple[str, ...] = (
    "телефон",
    "почта",
    "email",
    "e-mail",
    "phone",
    "mobile",
    "username",
    "логин",
)
_GENERIC_SAVED_LOGIN_CTA_MARKERS: tuple[str, ...] = (
    "войти",
    "войти с паролем",
    "дальше",
    "continue",
    "next",
    "sign in",
    "log in",
)
_GENERIC_OAUTH_PROVIDER_LABELS: frozenset[str] = frozenset(
    {
        "google",
        "apple",
        "facebook",
        "github",
        "linkedin",
        "microsoft",
        "vk",
        "вконтакте",
        "госуслуги",
        "одноклассники",
        "мой мир@mail.ru",
    }
)
_GENERIC_OAUTH_SERVICE_SELECTION_PHRASES: tuple[str, ...] = (
    "выберите сервис для входа",
    "выберите способ входа",
    "continue with",
    "sign in with",
    "choose a sign-in method",
    "choose login method",
    "выберите провайдера",
)

# URL path/query hints for “probably a login / auth surface” (any site).
_LOGIN_URL_HINTS: tuple[str, ...] = (
    "login",
    "signin",
    "sign-in",
    "oauth",
    "openid",
    "authorize",
    "authentication",
    "/auth",
    "/session",
    "/account/",
)


def url_suggests_login_page(url: str) -> bool:
    """Heuristic: URL looks like an auth/login route (works for any host)."""
    if not (url or "").strip():
        return False
    low = url.lower()
    path = (urlparse(url).path or "").lower()
    return any(h in low or h in path for h in _LOGIN_URL_HINTS)


# Backwards-compatible alias (internal callsites).
_url_suggests_login_page = url_suggests_login_page


def saved_login_auth_facts(outcome: str, **extras: Any) -> dict[str, Any]:
    """Machine-oriented auth note for saved-login automation (consumable by reporting, not chat prose)."""
    facts: dict[str, Any] = {"facts_version": 1, "source": "saved_login_auto", "outcome": outcome}
    for key, val in extras.items():
        if val is None or val == "":
            continue
        if isinstance(val, (list, tuple, dict)) and len(val) == 0:
            continue
        facts[key] = list(val) if isinstance(val, tuple) else val
    return facts


@dataclass(frozen=True)
class BrowserBarrierClassification:
    """Structured barrier signals derived from probe + snapshot (deterministic, no I/O)."""

    looks_like_login_surface: bool
    saved_login_flow_detected: bool
    looks_like_code_challenge: bool
    visible_oauth_providers: tuple[str, ...]
    oauth_service_selection_barrier: bool


def looks_like_code_challenge(snapshot: dict[str, Any]) -> bool:
    """Heuristically detect pages asking for a one-time verification code.

    Avoid matching stray «код» on home/landing pages (e.g. promos). Require SMS/OTP-ish
    phrases, explicit one-time-code inputs, or equivalent English markers.
    """
    body_text = str(snapshot.get("text", "") or "").lower()
    interactive = snapshot.get("interactive", []) or []
    parts: list[str] = []
    for item in interactive:
        if not isinstance(item, dict):
            continue
        ac = str(item.get("autocomplete") or "").lower()
        if "one-time" in ac or ac.endswith("otp"):
            return True
        parts.append(
            " ".join(
                [
                    str(item.get("text", "")),
                    str(item.get("name", "")),
                    str(item.get("placeholder", "")),
                    str(item.get("role", "")),
                ]
            )
        )
    combined = " ".join(parts).lower()
    haystack = f"{body_text} {combined}"
    return any(
        token in haystack
        for token in (
            "sms",
            "смс",
            "sms-код",
            "смс код",
            "whatsapp",
            "ватсап",
            "однораз",
            "одноразов",
            "verification code",
            "pin code",
            "код из смс",
            "код из сообщ",
            "код из письма",
            "код подтвержд",
            "из смс",
            "по смс",
            "отправили код",
            "отправили смс",
            "one-time",
        )
    )


def _input_hints_blob(item: dict[str, Any]) -> str:
    return (
        " ".join(
            [
                str(item.get("placeholder", "") or ""),
                str(item.get("name", "") or ""),
                str(item.get("html_id", "") or ""),
                str(item.get("role", "") or ""),
            ]
        )
        .lower()
        .strip()
    )


def _input_reads_as_identity_capture(item_type: str, hints: str) -> bool:
    """True for obvious phone/email/login capture — not sufficient to ask user for OTP yet."""
    t = item_type.strip().lower()
    if t == "email":
        return True
    if t == "tel":
        return True
    h = hints
    return any(
        m in h
        for m in (
            "phone",
            "+7",
            "mail",
            "@",
            "email",
            "телеф",
            "номер",
            "mobile",
            "логин",
            "login",
            "почт",
            "password",
            "парол",
        )
    )


def _interactive_has_otp_autocomplete(item: dict[str, Any]) -> bool:
    ac = str(item.get("autocomplete") or "").lower()
    return "one-time" in ac or ac.endswith("otp")


def snapshot_allows_ask_user_2fa(snapshot: dict[str, Any]) -> bool:
    """Whether ``ask_user`` with blocker_type ``2fa`` is allowed — DOM must show an OTP/code step.

    Text-only ``looks_like_code_challenge`` hits (marketing/footer copy) without a plausible
    code field are rejected so the runtime can push the loop forward instead of parking the browser.
    """
    if not isinstance(snapshot, dict) or not looks_like_code_challenge(snapshot):
        return False
    interactive = snapshot.get("interactive", []) or []

    for item in interactive:
        if isinstance(item, dict) and _interactive_has_otp_autocomplete(item):
            return True

    for item in interactive:
        if not isinstance(item, dict):
            continue
        if str(item.get("tag", "") or "").lower() != "input":
            continue
        ty = str(item.get("type", "") or "text").lower()
        if ty in ("hidden", "submit", "button", "checkbox", "radio", "image", "range", "file", "color"):
            continue
        if ty not in ("text", "tel", "number", ""):
            continue
        if item.get("fillable") is False:
            continue
        hints = _input_hints_blob(item)
        if any(tok in hints for tok in ("код", "code", "otp", "pin", "sms", "смс", "verification")):
            return True
        if _input_reads_as_identity_capture(ty or "text", hints):
            continue
        # Bare text-ish box on an OTP-labelled page (short codes often omit placeholders).
        return True

    return False


def looks_like_saved_login_opportunity(site_key: str, probe: dict[str, Any]) -> bool:
    """Decide whether the current page looks like a login flow using generic cues first."""
    probe_url = str(probe.get("url", "") or "")
    if _url_suggests_login_page(probe_url):
        return True

    auth_signals = probe.get("auth_signals", {}) or {}
    if auth_signals.get("login_form_present"):
        return True

    body_preview = str(probe.get("body_text_preview", "") or "").lower()
    interactive_texts = [str(text).lower() for text in probe.get("interactive_texts", []) or []]
    combined_interactive = " ".join(interactive_texts)

    if auth_signals.get("has_login_keyword"):
        return True

    has_identity_fields = any(marker in body_preview for marker in _GENERIC_SAVED_LOGIN_BODY_MARKERS)
    has_login_cta = any(token in combined_interactive for token in _GENERIC_SAVED_LOGIN_CTA_MARKERS)
    if has_identity_fields and has_login_cta:
        return True

    return False


def probe_suggests_authenticated_applicant_session(auth_signals: dict[str, Any] | None) -> bool:
    """True when lightweight probe signals look like a logged-in applicant workspace, not a bare login gate.

    Uses generic keywords (profile, account menu) so it applies across sites/locales that expose them.
    """
    if not isinstance(auth_signals, dict) or auth_signals.get("login_form_present"):
        return False
    return bool(
        auth_signals.get("has_profile_keyword") or auth_signals.get("has_account_menu_keyword")
    )


def should_suppress_login_navigation_guidance(
    snapshot: dict[str, Any],
    probe: dict[str, Any] | None,
) -> bool:
    """When the shell is already an authenticated applicant session, do not push generic «go sign in» copy.

    ``looks_like_login_surface`` is intentionally broad (e.g. header «Sign in» on listings); session probe
    disambiguates without site-specific DOM selectors.
    """
    if probe is None:
        return False
    auth = probe.get("auth_signals") or {}
    if not probe_suggests_authenticated_applicant_session(auth):
        return False
    url = str(snapshot.get("url") or probe.get("url") or "").strip()
    if url_suggests_login_page(url):
        return False
    if looks_like_code_challenge(snapshot):
        return False
    return True


def looks_like_login_surface(snapshot: dict[str, Any]) -> bool:
    """True when DOM/url text suggests an authentication gate (before credential orchestration)."""
    body_text = str(snapshot.get("text", "") or "").lower()
    headings = " ".join(str(item) for item in snapshot.get("headings", []) or []).lower()
    interactive = " ".join(
        str(item.get("text", ""))
        for item in snapshot.get("interactive", []) or []
        if isinstance(item, dict)
    ).lower()
    current_url = str(snapshot.get("url", "") or "").lower()
    uid, pid = extract_login_field_ids(snapshot)
    has_login_fields = bool(uid or pid)
    return (
        has_login_fields
        or "login" in current_url
        or "вход" in body_text
        or "вход" in headings
        or "войти" in interactive
        or looks_like_code_challenge(snapshot)
    )


def extract_visible_oauth_provider_labels(snapshot: dict[str, Any], site_key: str) -> list[str]:
    """Return visible OAuth/provider buttons using generic providers plus optional adapter labels."""
    labels = {label.lower() for label in _GENERIC_OAUTH_PROVIDER_LABELS}
    labels.update(label.lower() for label in get_known_oauth_provider_labels(site_key))
    options: list[str] = []
    interactive = snapshot.get("interactive", []) or []
    for item in interactive:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text", "")).split()).strip()
        if not text:
            continue
        if text.lower() in labels:
            options.append(text)
    return options


def oauth_service_selection_options_if_active(site_key: str, snapshot: dict[str, Any]) -> list[str]:
    """Detect OAuth/service-selection screens from generic copy plus visible provider buttons."""
    page_text = str(snapshot.get("text", "") or "").lower()
    options = extract_visible_oauth_provider_labels(snapshot, site_key)
    if options and any(phrase in page_text for phrase in _GENERIC_OAUTH_SERVICE_SELECTION_PHRASES):
        return options
    return []


def classify_browser_barrier(
    site_key: str,
    probe: dict[str, Any],
    snapshot: dict[str, Any],
) -> BrowserBarrierClassification:
    """Map observation/runtime facts to structured barrier signals."""
    saved = looks_like_saved_login_opportunity(site_key, probe)
    code = looks_like_code_challenge(snapshot)
    surface = looks_like_login_surface(snapshot)
    oauth_opts = tuple(extract_visible_oauth_provider_labels(snapshot, site_key))
    oauth_barrier = bool(oauth_service_selection_options_if_active(site_key, snapshot))
    return BrowserBarrierClassification(
        looks_like_login_surface=surface,
        saved_login_flow_detected=saved,
        looks_like_code_challenge=code,
        visible_oauth_providers=oauth_opts,
        oauth_service_selection_barrier=oauth_barrier,
    )
