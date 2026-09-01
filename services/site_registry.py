from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class SiteDefinition:
    key: str
    label: str
    compact_label: str
    hosts: tuple[str, ...]
    supports_home: bool = True
    supports_search: bool = True
    supports_playlist: bool = True

    def matches_host(self, host: str) -> bool:
        normalized = str(host or "").lower().split(":", 1)[0]
        return any(normalized == suffix.lstrip(".") or normalized.endswith(suffix) for suffix in self.hosts)


SITE_DEFINITIONS: tuple[SiteDefinition, ...] = (
    SiteDefinition("bilibili", "Bilibili", "B", ("bilibili.com", "b23.tv")),
    SiteDefinition("youtube", "YouTube", "Y", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    SiteDefinition("douyin", "抖音", "D", ("douyin.com", "iesdouyin.com")),
    SiteDefinition("tiktok", "TikTok", "T", ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com")),
    SiteDefinition("xiaohongshu", "小红书", "X", ("xiaohongshu.com", "xhslink.com"), supports_playlist=False),
)
SITE_KEYS = tuple(item.key for item in SITE_DEFINITIONS)
SITE_BY_KEY = {item.key: item for item in SITE_DEFINITIONS}
SITE_LABELS = {item.key: item.label for item in SITE_DEFINITIONS}


def normalize_site(site: str, default: str = "youtube") -> str:
    value = str(site or "").strip().lower()
    return value if value in SITE_BY_KEY else (default if default in SITE_BY_KEY else "youtube")


def site_for_url(url: str, fallback: str = "youtube") -> str:
    host = (urlparse(str(url or "").strip()).hostname or "").lower()
    for definition in SITE_DEFINITIONS:
        if definition.matches_host(host):
            return definition.key
    return normalize_site(fallback)


def site_label(site: str, fallback: str = "youtube") -> str:
    return SITE_BY_KEY[normalize_site(site, fallback)].label


def compact_site_label(site: str, fallback: str = "youtube") -> str:
    return SITE_BY_KEY[normalize_site(site, fallback)].compact_label
