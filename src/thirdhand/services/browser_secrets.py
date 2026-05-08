"""Helpers for browser automation secrets stored in environment/settings."""

from __future__ import annotations

import json
from typing import Any

from src.thirdhand.config import settings
from src.thirdhand.services.browser_site_registry import normalize_site_name

_LOGIN_FIELD_ALIASES = {
    "username": "username",
    "login": "username",
    "email": "username",
    "phone": "username",
    "password": "password",
    "pass": "password",
}


def _load_json_credentials() -> dict[str, dict[str, str]]:
    """Parse optional credentials JSON from settings."""
    raw = (settings.BROWSER_SITE_CREDENTIALS_JSON or "").strip()
    if not raw:
        return {}

    try:
        parsed: Any = json.loads(raw)
    except Exception as exc:
        raise ValueError("Invalid BROWSER_SITE_CREDENTIALS_JSON value") from exc

    if not isinstance(parsed, dict):
        raise ValueError("BROWSER_SITE_CREDENTIALS_JSON must be a JSON object")

    normalized: dict[str, dict[str, str]] = {}
    for site, secrets in parsed.items():
        if not isinstance(secrets, dict):
            continue
        site_key = normalize_site_name(str(site))
        normalized[site_key] = {
            _LOGIN_FIELD_ALIASES.get(str(field).strip().lower(), str(field).strip().lower()): str(
                value
            )
            for field, value in secrets.items()
            if value is not None
        }
    return normalized


def get_site_credentials_registry() -> dict[str, dict[str, str]]:
    """Return all configured login credentials by normalized site key."""
    return _load_json_credentials()


def list_saved_login_sites() -> list[str]:
    """List normalized site keys that have both username and password configured."""
    sites: list[str] = []
    for site, creds in get_site_credentials_registry().items():
        if creds.get("username") and creds.get("password"):
            sites.append(site)
    return sorted(sites)


def get_site_login_credentials(site: str) -> dict[str, str]:
    """Return login credentials for a site or raise if unavailable."""
    normalized_site = normalize_site_name(site)
    creds = get_site_credentials_registry().get(normalized_site, {})
    if not creds.get("username") or not creds.get("password"):
        raise KeyError(f"No saved login credentials configured for site '{normalized_site}'")
    return {
        "username": creds["username"],
        "password": creds["password"],
    }
