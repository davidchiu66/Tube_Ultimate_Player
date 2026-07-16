from __future__ import annotations

from urllib.parse import urlparse


def detect_source_site(url: str = "", source_site: str = "") -> str:
    host = urlparse(str(url or "").strip()).netloc.lower()
    if host.endswith("bilibili.com") or host.endswith("b23.tv"):
        return "bilibili"
    site = str(source_site or "").strip().lower()
    if site in {"youtube", "bilibili"}:
        return site
    return "youtube"


def source_site_label(source_site: str = "", url: str = "") -> str:
    return "Bilibili" if detect_source_site(url, source_site) == "bilibili" else "YouTube"
