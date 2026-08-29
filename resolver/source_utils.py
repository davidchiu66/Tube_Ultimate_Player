from __future__ import annotations

from urllib.parse import urlparse

from services.site_registry import normalize_site, site_for_url, site_label


def detect_source_site(url: str = "", source_site: str = "") -> str:
    raw_url = str(url or "").strip()
    if raw_url:
        return site_for_url(raw_url, str(source_site or "youtube"))
    return normalize_site(source_site)


def source_site_label(source_site: str = "", url: str = "") -> str:
    return site_label(detect_source_site(url, source_site))
