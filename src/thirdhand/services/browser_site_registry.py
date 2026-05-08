"""Optional site adapters for browser automation.

Core runtime policy should remain site-agnostic. This registry exists only for lightweight
normalization such as aliases, known start URLs, and optional provider-label metadata.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Canonical site key -> optional adapter metadata. Credential secrets live in browser_secrets.
SITE_PROFILES: dict[str, dict[str, Any]] = {
    "hh": {
        "aliases": ("hh", "hh.ru", "headhunter"),
        "start_url": "https://hh.ru",
        "oauth_provider_labels": frozenset(
            {
                "Госуслуги",
                "ВКонтакте",
                "Google",
                "Мой Мир@mail.ru",
                "Одноклассники",
            }
        ),
    },
}


def normalize_site_name(site: str) -> str:
    """Map a user or host token to an internal site key when it matches a known profile."""
    normalized = (site or "").strip().lower()
    for site_key, profile in SITE_PROFILES.items():
        aliases = tuple(str(alias).strip().lower() for alias in profile.get("aliases", ()))
        if normalized == site_key or normalized in aliases:
            return site_key
    return normalized


def get_site_profile(site_key: str) -> dict[str, Any] | None:
    """Return the full profile dict for a canonical site key, or None if unknown."""
    key = site_key if site_key in SITE_PROFILES else normalize_site_name(site_key)
    return SITE_PROFILES.get(key)


def get_default_site_url(site: str) -> str:
    """Return the configured start URL for a known site key, or empty string."""
    key = normalize_site_name(site)
    profile = SITE_PROFILES.get(key, {})
    return str(profile.get("start_url", "") or "")


def infer_site_key_from_url(url: str) -> str:
    """Infer normalized site key from a page URL hostname (uses profile aliases)."""
    host = (urlparse(url).hostname or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return normalize_site_name(host)


def get_known_oauth_provider_labels(site_key: str) -> frozenset[str]:
    """Optional adapter metadata: known provider labels for sites with branded OAuth buttons."""
    profile = get_site_profile(site_key) or {}
    raw = profile.get("oauth_provider_labels") or ()
    return frozenset(str(t) for t in raw)
