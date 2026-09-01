from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import datetime as _dt
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from resolver.models import HomeVideo, PlaylistEntry, PlaylistInfo, PlaylistSection, VideoInfo
from resolver.quality_selector import QualitySelector
from resolver.youtube_resolver import YoutubeResolver
from services.config_service import (
    ConfigService,
    detect_browser_cookie_sources,
    rank_cookie_sources,
)
from services.cookie_service import load_browser_cookie_header, load_cookie_header
from services.site_registry import SITE_KEYS, site_for_url, site_label


logger = logging.getLogger("tube_player.resolver")

_WBI_MIXIN_KEY = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_INVALID_WBI_CHARS = str.maketrans("", "", "!'()*")
_BILIBILI_HOME_PAGE_LIMIT = 30
_BILIBILI_SEARCH_PAGE_LIMIT = 45
_HOME_CACHE_TTL_SECONDS = 300.0
_SEARCH_CACHE_TTL_SECONDS = 1800.0
_CREATOR_CACHE_TTL_SECONDS = 600.0
_COLLECTION_CACHE_TTL_SECONDS = 600.0
_MAX_PAGE_CACHE_ITEMS = 48
_MAX_CREATOR_CACHE_ITEMS = 32
_MAX_COLLECTION_CACHE_ITEMS = 32
_SHORT_VIDEO_ITEM_CACHE_TTL_SECONDS = 600.0
_MAX_SHORT_VIDEO_ITEM_CACHE_ITEMS = 256
_DOUYIN_BROWSE_MIN_INTERVAL_SECONDS = 2.5
_DOUYIN_VERIFY_COOLDOWN_SECONDS = 30.0
_MAX_DOUYIN_SEARCH_SESSIONS = 16
_MAX_DOUYIN_HOME_POOL_ITEMS = 200
_XIAOHONGSHU_ITEM_CACHE_TTL_SECONDS = 900.0
_MAX_XIAOHONGSHU_ITEM_CACHE_ITEMS = 300


class SiteResolver:
    def __init__(self, config: ConfigService) -> None:
        self.config = config
        self.youtube = YoutubeResolver(config)
        self.bilibili = BilibiliResolver(config, self.youtube)
        self._page_cache: OrderedDict[str, tuple[float, list[HomeVideo], bool]] = OrderedDict()
        self._creator_cache: OrderedDict[str, tuple[float, PlaylistInfo | None]] = OrderedDict()
        self._collection_cache: OrderedDict[str, tuple[float, PlaylistInfo | None]] = OrderedDict()
        # 首页/搜索接口返回的短视频条目包含带签名的直链。TikTok 网页二次解析
        # 经常被风控拦截，因此在卡片生命周期内保留原始条目供播放/下载复用。
        self._short_video_item_cache: OrderedDict[str, tuple[float, str, dict]] = OrderedDict()
        self._douyin_search_sessions: OrderedDict[str, dict] = OrderedDict()
        self._douyin_home_sessions: OrderedDict[str, dict] = OrderedDict()
        # 首页/搜索与作者列表都可能被多个 worker 线程同时读写，各自加锁保护。
        self._page_cache_lock = threading.Lock()
        self._creator_cache_lock = threading.Lock()
        self._collection_cache_lock = threading.Lock()
        self._short_video_item_cache_lock = threading.Lock()
        self._douyin_search_session_lock = threading.Lock()
        self._douyin_home_session_lock = threading.Lock()
        self._douyin_browse_request_lock = threading.Lock()
        self._douyin_browse_last_request_at = 0.0
        self._douyin_browser_client = None
        self._xiaohongshu_browser_client = None
        self._xiaohongshu_item_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._xiaohongshu_item_cache_lock = threading.Lock()

    def set_douyin_browser_client(self, client) -> None:
        self._douyin_browser_client = client

    def set_xiaohongshu_browser_client(self, client) -> None:
        self._xiaohongshu_browser_client = client

    def home_source(self) -> str:
        return self.config.default_home_source()

    @staticmethod
    def normalize_source(source: str) -> str:
        """把任意站点标识收敛成 'youtube' / 'bilibili'，无法识别时返回空串。"""
        normalized = str(source or "").strip().lower()
        return normalized if normalized in SITE_KEYS else ""

    def home_source_label(self, source: str = "") -> str:
        effective = self.normalize_source(source) or self.home_source()
        return site_label(effective)

    def resolve(self, url: str) -> VideoInfo:
        original_url = str(url or "").strip()
        normalized_url = self._normalize_short_video_url(original_url)
        collection_id = self._douyin_collection_id_from_url(original_url)
        source = site_for_url(normalized_url)
        if source in {"douyin", "tiktok"}:
            if normalized_url != original_url:
                logger.info("short video URL normalized source=%s from=%s to=%s", source, self._redact_short_video_url(original_url), normalized_url)
            cached = self._short_video_item_lookup(normalized_url)
            if cached is not None:
                logger.info("short video cache hit source=%s url=%s", source, self._redact_short_video_url(normalized_url))
                try:
                    info = self._video_info_from_short_video_item(cached[1], source, normalized_url)
                    return self._attach_douyin_collection_context(info, collection_id, original_url)
                except Exception as exc:
                    logger.warning("cached %s item could not be converted; attempting recovery: %s", source, exc)
            if source == "tiktok":
                logger.info("short video cache miss source=tiktok url=%s; attempting item recovery", self._redact_short_video_url(normalized_url))
                recovered = self._recover_tiktok_item(normalized_url)
                if recovered is not None:
                    try:
                        info = self._video_info_from_short_video_item(recovered, source, normalized_url)
                        return self._attach_douyin_collection_context(info, collection_id, original_url)
                    except Exception:
                        logger.warning("recovered TikTok item could not be converted; falling back to yt-dlp", exc_info=True)
        try:
            info = self.youtube.resolve(normalized_url)
        except Exception as primary_error:
            if source != "xiaohongshu":
                raise
            parsed_xhs = urllib.parse.urlparse(original_url)
            xhs_query = urllib.parse.parse_qs(parsed_xhs.query)
            if parsed_xhs.hostname and parsed_xhs.hostname.endswith("xiaohongshu.com") and not str(
                (xhs_query.get("xsec_token") or [""])[0]
            ).strip():
                raise RuntimeError(
                    "该小红书链接缺少页面安全参数，平台会拒绝裸 note ID 详情页。"
                    "请在 Firefox 中打开该视频，使用“分享/复制链接”取得包含 xsec_token 的完整地址，"
                    "或直接从应用的小红书首页、搜索结果中播放。"
                ) from primary_error
            client = getattr(self, "_xiaohongshu_browser_client", None)
            if client is None or not hasattr(client, "request_note_detail"):
                raise
            logger.warning("xiaohongshu yt-dlp resolve failed; using browser detail fallback: %s", primary_error)
            try:
                payload = client.request_note_detail(original_url)
                info = self._video_info_from_xiaohongshu_detail(payload, original_url)
            except Exception as fallback_error:
                raise RuntimeError(
                    f"小红书视频解析失败。请确认该链接是视频笔记、Firefox 已登录小红书，"
                    f"并完成可能出现的安全验证。\n{fallback_error}"
                ) from fallback_error
        if source == "xiaohongshu":
            info = self._enrich_xiaohongshu_video(info, original_url)
            info.http_headers.setdefault("Referer", original_url)
            info.http_headers.setdefault("Origin", "https://www.xiaohongshu.com")
            self._normalize_xiaohongshu_media_urls(info)
        return self._attach_douyin_collection_context(info, collection_id, original_url)

    @staticmethod
    def _normalize_xiaohongshu_media_urls(video: VideoInfo) -> None:
        """小红书 CDN 同时返回 HTTP/HTTPS，播放器统一使用 HTTPS。"""
        for quality in (video.qualities or {}).values():
            for attribute in ("video_url", "audio_url", "muxed_video_url"):
                value = getattr(quality, attribute, None)
                if isinstance(value, str) and value.startswith("http://"):
                    host = urllib.parse.urlparse(value).hostname or ""
                    if host.lower().endswith("xhscdn.com"):
                        setattr(quality, attribute, "https://" + value[7:])

    @staticmethod
    def _douyin_collection_id_from_url(url: str) -> str:
        if site_for_url(url) != "douyin":
            return ""
        parsed = urllib.parse.urlparse(str(url or ""))
        query = urllib.parse.parse_qs(parsed.query)
        # 用户页弹窗链接中 vid 是 compilation/mix ID；modal_id 是当前视频 ID。
        if not str((query.get("modal_id") or [""])[0]).strip():
            return ""
        value = str((query.get("vid") or [""])[0]).strip()
        return value if re.fullmatch(r"[0-9]+", value) else ""

    @staticmethod
    def _attach_douyin_collection_context(video: VideoInfo, collection_id: str, source_url: str = "") -> VideoInfo:
        if not collection_id or getattr(video, "source_site", "") != "douyin":
            return video
        raw_info = dict(video.raw_info) if isinstance(video.raw_info, dict) else {}
        raw_info["_tube_player_collection_id"] = collection_id
        if source_url:
            raw_info["_tube_player_collection_url"] = source_url
        video.raw_info = raw_info
        return video

    @staticmethod
    def _normalize_short_video_url(url: str) -> str:
        """Normalize Douyin user-page links that carry the active video in query params."""
        raw = str(url or "").strip()
        # Chat/Markdown escaping can leave backslashes before URL punctuation.
        raw = raw.replace("\\_", "_").replace("\\&", "&")
        parsed = urllib.parse.urlparse(raw)
        if site_for_url(raw) != "douyin":
            return raw
        if parsed.path.lower().rstrip("/").startswith("/video/"):
            return raw
        query = urllib.parse.parse_qs(parsed.query)
        video_id = ""
        for key in (("modal_id", "vid") if "modal_id" in query else ("vid", "modal_id")):
            candidate = str((query.get(key) or [""])[0]).strip()
            if re.fullmatch(r"[0-9]+", candidate):
                video_id = candidate
                break
        if video_id:
            return f"https://www.douyin.com/video/{video_id}"
        return raw

    @staticmethod
    def _redact_short_video_url(url: str) -> str:
        parsed = urllib.parse.urlparse(str(url or ""))
        return parsed._replace(query="", fragment="").geturl()

    def resolve_cached_short_video(self, url: str) -> VideoInfo | None:
        """返回首页/搜索/作者列表中已经解析过的短视频，绝不发起网页请求。"""
        normalized_url = self._normalize_short_video_url(url)
        source = site_for_url(normalized_url)
        if source not in {"douyin", "tiktok"}:
            return None
        cached = self._short_video_item_lookup(normalized_url)
        if cached is None:
            return None
        try:
            return self._video_info_from_short_video_item(cached[1], source, normalized_url)
        except Exception:
            logger.warning("cached %s item could not be converted for download", source, exc_info=True)
            return None

    def detect_url_kind(self, url: str) -> str:
        if _is_bilibili_url(url):
            return self.bilibili.detect_url_kind(url)
        source = site_for_url(url)
        if source == "tiktok":
            path = urllib.parse.urlparse(str(url or "")).path.lower()
            if "/@" in path and "/video/" not in path:
                return "playlist"
            if "/collection/" in path:
                return "playlist"
            return "video"
        if source == "douyin":
            normalized = self._normalize_short_video_url(url)
            parsed = urllib.parse.urlparse(normalized)
            if parsed.path.lower().rstrip("/").startswith("/user/"):
                return "playlist"
            return "video"
        if source == "xiaohongshu":
            path = urllib.parse.urlparse(str(url or "")).path.lower()
            if re.fullmatch(r"/user/profile/[0-9a-f]+/?", path):
                return "playlist"
            path = urllib.parse.urlparse(str(url or "")).path.lower()
            if re.search(r"/(?:explore|discovery/item)/[\da-f]+", path):
                return "video"
            if urllib.parse.urlparse(str(url or "")).hostname and str(url).lower().find("xhslink.com") >= 0:
                return "video"
            return "unknown"
        return self.youtube.detect_url_kind(url)

    def resolve_playlist(self, url: str) -> PlaylistInfo:
        if _is_bilibili_url(url):
            return self.bilibili.resolve_playlist(url)
        if site_for_url(url) in {"douyin", "tiktok"}:
            return self.youtube.resolve_playlist_generic(url)
        if site_for_url(url) == "xiaohongshu":
            return self._resolve_xiaohongshu_creator_playlist(url)
        return self.youtube.resolve_playlist(url)

    def _resolve_xiaohongshu_creator_playlist(self, url: str) -> PlaylistInfo:
        parsed = urllib.parse.urlparse(str(url or ""))
        match = re.search(r"/user/profile/([0-9a-f]+)", parsed.path, re.IGNORECASE)
        if not match:
            raise RuntimeError("当前链接不是有效的小红书作者主页")
        user_id = match.group(1)
        query = urllib.parse.parse_qs(parsed.query)
        token = str((query.get("xsec_token") or [""])[0]).strip()
        source = str((query.get("xsec_source") or ["pc_user"])[0]).strip() or "pc_user"
        creator_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        client = getattr(self, "_xiaohongshu_browser_client", None)
        if client is None or not hasattr(client, "request_creator"):
            raise RuntimeError("小红书作者列表浏览器服务尚未初始化")
        payload = client.request_creator(
            creator_url,
            user_id=user_id,
            token=token,
            source=source,
            limit=50,
        )
        entries: list[PlaylistEntry] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            mapped = self._xiaohongshu_home_item(item, context="creator")
            if mapped is None:
                continue
            self._xiaohongshu_item_store(item, mapped.webpage_url)
            entries.append(
                PlaylistEntry(
                    playlist_id=f"xiaohongshu:creator:{user_id}",
                    video_id=mapped.video_id,
                    title=mapped.title,
                    webpage_url=mapped.webpage_url,
                    source_site="xiaohongshu",
                    uploader=mapped.uploader,
                    duration=mapped.duration,
                    thumbnail=mapped.thumbnail,
                    position=len(entries) + 1,
                )
            )
        if not entries:
            raise RuntimeError("小红书作者主页没有返回可播放的视频")
        uploader = next((entry.uploader for entry in entries if entry.uploader), "小红书作者")
        return PlaylistInfo(
            playlist_id=f"xiaohongshu:creator:{user_id}",
            title=f"{uploader} 的视频",
            webpage_url=creator_url,
            source_site="xiaohongshu",
            uploader=uploader,
            thumbnail=entries[0].thumbnail,
            entry_count=len(entries),
            source_type="creator",
            entries=entries,
        )

    def resolve_creator_playlist(self, video: VideoInfo, limit: int = 50) -> PlaylistInfo | None:
        if video.source_site not in {"youtube", "bilibili", "douyin", "tiktok", "xiaohongshu"}:
            return None
        creator_key = str(video.creator_id or video.channel_id or video.creator_url).strip()
        if not creator_key:
            return None

        limit = max(1, min(50, int(limit)))
        cache_key = f"creator|{video.source_site}|{creator_key}|{limit}|{self._config_fingerprint(video.source_site)}"
        cached = self._creator_cache_lookup(cache_key)
        if cached is not None:
            logger.info("creator playlist cache hit site=%s creator=%s", video.source_site, creator_key)
            return cached

        if video.source_site == "bilibili":
            creator_url, fetched_entries = self.bilibili.fetch_creator_videos(video, limit)
        elif video.source_site == "douyin":
            creator_url, fetched_entries = self._fetch_douyin_creator_videos(video, limit)
        elif video.source_site == "tiktok":
            creator_url, fetched_entries = self._fetch_tiktok_creator_videos(video, limit)
        elif video.source_site == "xiaohongshu":
            creator_url, fetched_entries = self._fetch_xiaohongshu_creator_videos(video, limit)
        else:
            creator_url, fetched_entries = self.youtube.fetch_creator_videos(video, limit)

        playlist_id = f"{video.source_site}:creator:{creator_key}"
        current_key = _creator_entry_key(video.source_site, video.video_id, video.webpage_url)
        entries = [
            PlaylistEntry(
                playlist_id=playlist_id,
                video_id=video.video_id,
                title=video.title,
                webpage_url=video.webpage_url,
                source_site=video.source_site,
                uploader=video.uploader,
                duration=video.duration,
                thumbnail=video.thumbnail,
                position=1,
                availability="",
            )
        ]
        seen = {current_key}
        for fetched in fetched_entries:
            key = _creator_entry_key(fetched.source_site, fetched.video_id, fetched.webpage_url)
            if not key or key in seen or fetched.availability in {"private", "deleted", "unavailable"}:
                continue
            seen.add(key)
            entry = deepcopy(fetched)
            entry.playlist_id = playlist_id
            entry.position = len(entries) + 1
            entries.append(entry)
            if len(entries) - 1 >= limit:
                break

        playlist = None
        if len(entries) > 1:
            creator_name = self._creator_display_name(video, entries, creator_key)
            if creator_name:
                entries[0].uploader = creator_name
            playlist = PlaylistInfo(
                playlist_id=playlist_id,
                title=f"{creator_name or video.uploader or '制作者'} 的视频",
                webpage_url=creator_url or video.creator_url,
                source_site=video.source_site,
                uploader=video.uploader,
                thumbnail=video.thumbnail,
                entry_count=len(entries),
                source_type="creator",
                current_video_id=video.video_id,
                entries=entries,
            )
        # 小红书作者接口受页面安全上下文和风控影响，空响应可能只是瞬时壳页；
        # 不把一次空结果缓存 10 分钟，允许下次播放时恢复。
        if playlist is not None or video.source_site != "xiaohongshu":
            self._creator_cache_store(cache_key, playlist)
        logger.info(
            "creator playlist resolved site=%s creator=%s count=%s",
            video.source_site,
            creator_key,
            len(entries) if playlist else 0,
        )
        return deepcopy(playlist)

    def _fetch_xiaohongshu_creator_videos(
        self, video: VideoInfo, limit: int
    ) -> tuple[str, list[PlaylistEntry]]:
        user_id = str(video.creator_id or video.channel_id or "").strip()
        if not user_id:
            return str(video.creator_url or ""), []
        creator_url = str(
            video.creator_url or f"https://www.xiaohongshu.com/user/profile/{user_id}"
        ).strip()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(str(video.webpage_url or "")).query)
        token = str((query.get("xsec_token") or [""])[0]).strip()
        source = str((query.get("xsec_source") or ["pc_feed"])[0]).strip() or "pc_feed"
        # 卡片播放地址的 token 绑定 pc_feed，不能跨上下文用于 user_posted。
        # 只有本身来自作者主页的 pc_user token 才继续传递。
        if source != "pc_user":
            token = ""
        client = getattr(self, "_xiaohongshu_browser_client", None)
        if client is None or not hasattr(client, "request_creator"):
            return creator_url, []
        request_kwargs = {
            "user_id": user_id,
            "token": token,
            "source": "pc_user" if source != "pc_user" else source,
            "limit": max(1, min(50, int(limit))),
        }
        try:
            payload = client.request_creator(creator_url, **request_kwargs)
            # user_posted 偶尔首个响应只包含页面壳，给同一安全上下文一次
            # 轻量重试，避免把暂时空响应固化成“作者没有其它视频”。
            if not (payload.get("items") or {}):
                time.sleep(0.35)
                payload = client.request_creator(creator_url, **request_kwargs)
        except Exception as exc:
            logger.warning("xiaohongshu creator request failed user=%s: %s", user_id, type(exc).__name__)
            return creator_url, []
        entries: list[PlaylistEntry] = []
        seen: set[str] = set()
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            mapped = self._xiaohongshu_home_item(item, context="creator")
            if mapped is None or mapped.video_id in seen:
                continue
            seen.add(mapped.video_id)
            self._xiaohongshu_item_store(item, mapped.webpage_url)
            entries.append(
                PlaylistEntry(
                    playlist_id=f"xiaohongshu:creator:{user_id}",
                    video_id=mapped.video_id,
                    title=mapped.title,
                    webpage_url=mapped.webpage_url,
                    source_site="xiaohongshu",
                    uploader=mapped.uploader,
                    duration=mapped.duration,
                    thumbnail=mapped.thumbnail,
                    position=len(entries) + 1,
                )
            )
            if len(entries) >= limit:
                break
        return creator_url, entries

    @staticmethod
    def _creator_display_name(video: VideoInfo, entries: list[PlaylistEntry], creator_key: str) -> str:
        """Prefer a creator's display nickname over a login/user ID.

        Some Douyin responses expose ``uploader`` as the account slug while
        neighboring feed entries contain the human-readable nickname. Reuse
        that nickname for the playlist title and current entry label.
        """
        raw = video.raw_info if isinstance(video.raw_info, dict) else {}
        candidates = [
            (raw.get("author") or {}).get("nickname") if isinstance(raw.get("author"), dict) else "",
        ]
        # Neighboring creator-feed entries are often the only place where the
        # human-readable nickname is present; put them before yt-dlp's slug-like
        # uploader/channel fields.
        candidates.extend(entry.uploader for entry in entries)
        candidates.extend([raw.get("uploader"), raw.get("channel"), raw.get("creator"), video.uploader])
        key = str(creator_key or "").strip().casefold()
        current = str(video.uploader or "").strip().casefold()
        for value in candidates:
            name = str(value or "").strip()
            if name and name.casefold() not in {key, current, "_", "unknown"}:
                return name
        return ""

    def _fetch_douyin_creator_videos(self, video: VideoInfo, limit: int) -> tuple[str, list[PlaylistEntry]]:
        sec_uid = str(video.channel_id or "").strip()
        if not sec_uid:
            raise RuntimeError("当前抖音视频缺少作者 sec_uid")
        creator_url = str(video.creator_url or f"https://www.douyin.com/user/{sec_uid}")
        cursor = 0
        entries: list[PlaylistEntry] = []
        seen: set[str] = set()
        while len(entries) < limit:
            payload = self._request_short_video_json(
                "douyin", "https://www.douyin.com/aweme/v1/web/aweme/post/",
                {"device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
                 "sec_user_id": sec_uid, "max_cursor": str(cursor), "count": str(min(50, limit - len(entries))),
                 "publish_video_strategy_type": "2", "pc_client_type": "1", "version_code": "190500",
                 "version_name": "19.5.0"}, creator_url,
            )
            raw_items = payload.get("aweme_list") or []
            for item in raw_items:
                if isinstance(item, dict):
                    self._short_video_item_store("douyin", item)
                home = self._short_video_home_item(item, "douyin") if isinstance(item, dict) else None
                if home is None or home.video_id in seen:
                    continue
                seen.add(home.video_id)
                entries.append(PlaylistEntry(
                    playlist_id=f"douyin:creator:{sec_uid}", video_id=home.video_id, title=home.title,
                    webpage_url=home.webpage_url, source_site="douyin", uploader=home.uploader,
                    duration=home.duration, thumbnail=home.thumbnail, position=len(entries) + 1,
                ))
                if len(entries) >= limit:
                    break
            if not payload.get("has_more") or not raw_items:
                break
            next_cursor = int(payload.get("max_cursor") or 0)
            if next_cursor == cursor:
                break
            cursor = next_cursor
        return creator_url, entries

    def _fetch_tiktok_creator_videos(self, video: VideoInfo, limit: int) -> tuple[str, list[PlaylistEntry]]:
        sec_uid = str(video.channel_id or video.creator_id or "").strip()
        creator_url = str(video.creator_url or "").strip()
        cached_entries = self._cached_short_video_creator_entries("tiktok", video, limit)
        # 搜索结果通常一次就含有该作者的多条视频。优先使用这些已通过风控且带可播
        # 直链的条目，避免再打目前经常返回空对象的作者接口。
        if len(cached_entries) > 1:
            return creator_url, cached_entries
        raw_author = (video.raw_info or {}).get("author") if isinstance(video.raw_info, dict) else {}
        raw_author = raw_author if isinstance(raw_author, dict) else {}
        creator_handle = str(raw_author.get("uniqueId") or raw_author.get("unique_id") or "").strip()
        if not creator_handle and "/@" in urllib.parse.urlparse(creator_url).path:
            creator_handle = urllib.parse.urlparse(creator_url).path.split("/@", 1)[1].split("/", 1)[0]
        if creator_handle and creator_handle != "_":
            try:
                # TikTok 的作者动态接口经常对非浏览器请求返回空对象，而同一登录会话下
                # 搜索接口可用。以用户名搜索后仍按 secUid/uniqueId 精确过滤，不会把
                # 名称相似的其它账号混进播放列表。
                self._search_tiktok(creator_handle, 1, min(50, max(2, int(limit))))
                searched_entries = self._cached_short_video_creator_entries("tiktok", video, limit)
                if len(searched_entries) > 1:
                    return creator_url, searched_entries
                if len(searched_entries) > len(cached_entries):
                    cached_entries = searched_entries
            except Exception as exc:
                logger.warning("TikTok creator search fallback unavailable handle=%s: %s", creator_handle, exc)
        if not sec_uid:
            return creator_url, cached_entries
        count = min(30, max(1, int(limit)))
        params = {
            "aid": "1988", "app_language": "en", "app_name": "tiktok_web",
            "browser_language": "en-US", "browser_name": "Mozilla", "browser_online": "true",
            "browser_platform": "Win32", "browser_version": "5.0 (Windows)", "channel": "tiktok_web",
            "cookie_enabled": "true", "count": str(count), "cursor": "0", "device_platform": "web_pc",
            "focus_state": "true", "from_page": "user", "history_len": "2", "is_fullscreen": "false",
            "is_page_visible": "true", "language": "en", "os": "windows", "priority_region": "",
            "referer": "", "region": "US", "screen_height": "1080", "screen_width": "1920",
            "secUid": sec_uid, "type": "1", "tz_name": "Asia/Shanghai", "webcast_language": "en",
        }
        try:
            payload = self._request_short_video_json(
                "tiktok", "https://www.tiktok.com/api/post/item_list/", params,
                creator_url or "https://www.tiktok.com/",
            )
        except Exception as exc:
            logger.warning("TikTok creator endpoint unavailable; using cached entries: %s", exc)
            return creator_url, cached_entries
        entries: list[PlaylistEntry] = list(cached_entries)
        seen: set[str] = set()
        seen.update(entry.video_id for entry in entries)
        for item in payload.get("itemList") or payload.get("item_list") or []:
            if not isinstance(item, dict):
                continue
            self._short_video_item_store("tiktok", item)
            home = self._short_video_home_item(item, "tiktok")
            if home is None or home.video_id in seen:
                continue
            seen.add(home.video_id)
            entries.append(PlaylistEntry(
                playlist_id=f"tiktok:creator:{sec_uid}", video_id=home.video_id, title=home.title,
                webpage_url=home.webpage_url, source_site="tiktok", uploader=home.uploader,
                duration=home.duration, thumbnail=home.thumbnail, position=len(entries) + 1,
            ))
            if len(entries) >= limit:
                break
        return creator_url, entries

    def _cached_short_video_creator_entries(
        self, source: str, video: VideoInfo, limit: int
    ) -> list[PlaylistEntry]:
        raw_author = (video.raw_info or {}).get("author") if isinstance(video.raw_info, dict) else {}
        raw_author = raw_author if isinstance(raw_author, dict) else {}
        wanted_ids = {
            str(video.channel_id or "").strip(),
            str(video.creator_id or "").strip(),
            str(raw_author.get("secUid") or raw_author.get("sec_uid") or "").strip(),
            str(raw_author.get("uniqueId") or raw_author.get("unique_id") or "").strip(),
        }
        creator_path = urllib.parse.urlparse(str(video.creator_url or "")).path
        if source == "tiktok" and "/@" in creator_path:
            wanted_ids.add(creator_path.split("/@", 1)[1].split("/", 1)[0])
        wanted_ids.discard("")
        if not wanted_ids:
            return []

        lock = getattr(self, "_short_video_item_cache_lock", None)
        cache = getattr(self, "_short_video_item_cache", None)
        if lock is None or cache is None:
            return []
        now = time.time()
        items: list[dict] = []
        seen_raw: set[str] = set()
        with lock:
            for cached_at, cached_source, item in cache.values():
                if cached_source != source or now - cached_at > _SHORT_VIDEO_ITEM_CACHE_TTL_SECONDS:
                    continue
                raw_value = item.get("id") if source == "tiktok" else item.get("aweme_id")
                raw_id = str(raw_value or "").strip()
                if not raw_id or raw_id in seen_raw:
                    continue
                author = item.get("author") or {}
                if not isinstance(author, dict):
                    continue
                author_ids = {
                    str(author.get("secUid") or author.get("sec_uid") or "").strip(),
                    str(author.get("uniqueId") or author.get("unique_id") or "").strip(),
                }
                if wanted_ids.isdisjoint(author_ids):
                    continue
                seen_raw.add(raw_id)
                items.append(deepcopy(item))

        playlist_id = f"{source}:creator:{next(iter(wanted_ids))}"
        entries: list[PlaylistEntry] = []
        for item in items:
            home = self._short_video_home_item(item, source)
            if home is None:
                continue
            entries.append(PlaylistEntry(
                playlist_id=playlist_id, video_id=home.video_id, title=home.title,
                webpage_url=home.webpage_url, source_site=source, uploader=home.uploader,
                duration=home.duration, thumbnail=home.thumbnail, position=len(entries) + 1,
            ))
            if len(entries) >= limit:
                break
        return entries

    def resolve_collection_playlist(self, video: VideoInfo) -> PlaylistInfo | None:
        """当前视频所属合集。返回 None 表示"不属于任何合集"，是常态而非错误。"""
        site = self.normalize_source(video.source_site)
        if not site:
            return None
        identity = str(video.webpage_url or video.video_id or "").strip()
        if not identity:
            return None

        fingerprint = self._config_fingerprint(site)
        cache_key = f"collection|{site}|{identity}|{fingerprint}"
        cached = self._collection_cache_lookup(cache_key)
        if cached is not None:
            logger.info("collection cache hit site=%s video=%s", site, video.video_id)
            return cached[0]

        if site == "bilibili":
            playlist = self.bilibili.resolve_collection(video)
        elif site == "douyin":
            collection_id = ""
            raw_info = video.raw_info if isinstance(video.raw_info, dict) else {}
            collection_id = str(raw_info.get("_tube_player_collection_id") or "").strip()
            playlist = self._fetch_douyin_collection_playlist(video, collection_id) if collection_id else None
        else:
            playlist = self.youtube.resolve_collection(video)
        if playlist is not None and not playlist.entries:
            playlist = None
        # 「不属于合集」也进缓存：否则每次切集都要重新打一次探测请求。
        self._collection_cache_store(cache_key, playlist)
        if playlist is not None:
            # 同一合集换集时直接复用刚解析出的完整层级，不再按每个视频重复请求接口。
            current_index = next(
                (index for index, entry in enumerate(playlist.entries) if entry.video_id == playlist.current_video_id),
                0,
            )
            start = max(0, current_index - 4)
            nearby_entries = playlist.entries[start : start + 12]
            for entry in nearby_entries:
                for alias in (entry.webpage_url, entry.video_id):
                    alias = str(alias or "").strip()
                    if alias:
                        self._collection_cache_store(f"collection|{site}|{alias}|{fingerprint}", playlist)
        logger.info(
            "collection resolved site=%s video=%s count=%s",
            site,
            video.video_id,
            len(playlist.entries) if playlist else 0,
        )
        return deepcopy(playlist)

    def _fetch_douyin_collection_playlist(
        self, video: VideoInfo, collection_id: str, limit: int = 50
    ) -> PlaylistInfo | None:
        if not collection_id or not re.fullmatch(r"[0-9]+", collection_id):
            return None
        endpoint = "https://www.douyin.com/aweme/v1/web/series/list/"
        raw_info = video.raw_info if isinstance(video.raw_info, dict) else {}
        source_url = str(raw_info.get("_tube_player_collection_url") or video.creator_url or "").strip()
        collection_url = f"https://www.douyin.com/collection/{collection_id}/1"
        browser_client = getattr(self, "_douyin_browser_client", None)
        if browser_client is not None and hasattr(browser_client, "request_collection_json"):
            try:
                payload = browser_client.request_collection_json(
                    collection_url,
                    target_count=limit,
                    timeout=30.0,
                )
                raw_items = [item for item in (payload.get("aweme_list") or []) if isinstance(item, dict)]
                entries: list[PlaylistEntry] = []
                seen: set[str] = set()
                for item in raw_items:
                    self._short_video_item_store("douyin", item)
                    home = self._short_video_home_item(item, "douyin")
                    if home is None or home.video_id in seen:
                        continue
                    seen.add(home.video_id)
                    entries.append(PlaylistEntry(
                        playlist_id=f"douyin:collection:{collection_id}", video_id=home.video_id,
                        title=home.title, webpage_url=home.webpage_url, source_site="douyin",
                        uploader=home.uploader, duration=home.duration, thumbnail=home.thumbnail,
                        position=len(entries) + 1,
                    ))
                if entries:
                    current_id = video.video_id
                    if current_id not in {entry.video_id for entry in entries}:
                        entries.insert(0, PlaylistEntry(
                            playlist_id=f"douyin:collection:{collection_id}", video_id=current_id,
                            title=video.title, webpage_url=video.webpage_url, source_site="douyin",
                            uploader=video.uploader, duration=video.duration, thumbnail=video.thumbnail,
                            position=1,
                        ))
                    for index, entry in enumerate(entries, 1):
                        entry.position = index
                    return PlaylistInfo(
                        playlist_id=f"douyin:collection:{collection_id}",
                        title=f"抖音合集 {collection_id}", webpage_url=collection_url,
                        source_site="douyin", uploader=video.uploader, entry_count=len(entries),
                        current_video_id=current_id, source_type="collection", entries=entries,
                    )
            except Exception as exc:
                logger.warning("Douyin collection page unavailable id=%s: %s", collection_id, exc)
            # 浏览器合集页未返回分集时立即结束探测，不能再串行尝试旧接口，
            # 否则一次播放会被合集探测拖延数分钟。
            return None

        # 无浏览器服务时保留旧 API 兜底，供单元测试和受限运行环境使用。
        if not source_url:
            source_url = collection_url
        cursor = 0
        entries: list[PlaylistEntry] = []
        seen: set[str] = set()
        while len(entries) < max(1, min(50, int(limit))):
            params = {
                "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
                "sec_user_id": str((raw_info.get("_tube_player_collection_url") or "").split("/user/")[-1].split("?", 1)[0]),
                "req_from": "channel_pc_web", "cursor": str(cursor),
                "count": str(min(18, limit - len(entries))), "read_new_mix": "true", "pc_client_type": "1",
                "version_code": "190500", "version_name": "19.5.0",
            }
            payload = self._request_douyin_browse_json(endpoint, params, source_url)
            raw_items = [item for item in (payload.get("aweme_list") or []) if isinstance(item, dict)]
            for item in raw_items:
                self._short_video_item_store("douyin", item)
                home = self._short_video_home_item(item, "douyin")
                if home is None or home.video_id in seen:
                    continue
                seen.add(home.video_id)
                entries.append(PlaylistEntry(
                    playlist_id=f"douyin:collection:{collection_id}", video_id=home.video_id,
                    title=home.title, webpage_url=home.webpage_url, source_site="douyin",
                    uploader=home.uploader, duration=home.duration, thumbnail=home.thumbnail,
                    position=len(entries) + 1,
                ))
                if len(entries) >= limit:
                    break
            if not payload.get("has_more") or not raw_items:
                break
            next_cursor = int(payload.get("cursor") or payload.get("max_cursor") or cursor)
            if next_cursor == cursor:
                break
            cursor = next_cursor
        if not entries:
            return None
        current_id = video.video_id
        if current_id and current_id not in {entry.video_id for entry in entries}:
            entries.insert(0, PlaylistEntry(
                playlist_id=f"douyin:collection:{collection_id}", video_id=current_id,
                title=video.title, webpage_url=video.webpage_url, source_site="douyin",
                uploader=video.uploader, duration=video.duration, thumbnail=video.thumbnail,
                position=1,
            ))
            for index, entry in enumerate(entries, 1):
                entry.position = index
        return PlaylistInfo(
            playlist_id=f"douyin:collection:{collection_id}",
            title=f"抖音合集 {collection_id}", webpage_url=source_url,
            source_site="douyin", uploader=video.uploader,
            entry_count=len(entries), current_video_id=current_id,
            source_type="collection", entries=entries,
        )

    def fetch_home_videos(
        self,
        page: int = 1,
        page_size: int = 56,
        *,
        force_refresh: bool = False,
        source: str = "",
    ) -> tuple[list[HomeVideo], bool]:
        source = self.normalize_source(source) or self.home_source()
        key = self._cache_key("home", source, "", page, page_size)
        if not force_refresh and (cached := self._cache_lookup(key, _HOME_CACHE_TTL_SECONDS)):
            return cached
        if source == "bilibili":
            result = self.bilibili.fetch_home_videos(page, page_size)
        elif source == "douyin":
            result = self._fetch_short_video_home("douyin", page, page_size, force_refresh=force_refresh)
        elif source == "tiktok":
            result = self._fetch_short_video_home("tiktok", page, page_size)
        elif source == "xiaohongshu":
            result = self._fetch_xiaohongshu_home(page, page_size, force_refresh=force_refresh)
        else:
            result = self.youtube.fetch_home_videos(page, page_size)
        self._cache_store(key, result)
        return result

    def _fetch_short_video_home(
        self,
        source: str,
        page: int,
        page_size: int,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[HomeVideo], bool]:
        """Fetch recommendation feeds through the sites' web JSON endpoints.

        yt-dlp intentionally does not support the site root URLs; the browser
        pages call these endpoints after loading. Reuse the configured browser
        Cookie and proxy, then map the response into the normal HomeVideo model.
        """
        page = max(1, int(page))
        target_count = max(1, min(56, int(page_size)))
        count = min(20, target_count)
        if source == "douyin":
            cookie_browser = ""
            if getattr(self, "config", None) is not None:
                cookie_browser = str(self.config.cookie_browser_for_site("douyin") or "")
            use_firefox_identity = (
                getattr(self, "_douyin_browser_client", None) is None
                and cookie_browser.split(":", 1)[0].strip().lower() == "firefox"
            )
            browser_name = "Firefox" if use_firefox_identity else "Chrome"
            browser_version = "142.0" if use_firefox_identity else "131"
            endpoint = "https://www.douyin.com/aweme/v1/web/tab/feed/"
            params = {
                "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
                "count": str(count), "pc_client_type": "1", "version_code": "190500",
                "version_name": "19.5.0", "cookie_enabled": "true", "screen_width": "1920",
                "screen_height": "1080", "browser_language": "zh-CN", "browser_platform": "Win32",
                "browser_name": browser_name, "browser_version": browser_version, "browser_online": "true",
                "engine_name": "Gecko" if use_firefox_identity else "Blink",
                "engine_version": browser_version, "os_name": "Windows", "os_version": "10",
                "platform": "PC", "cursor": "0", "refresh_index": "1",
                "video_type_select": "1", "aweme_pc_rec_raw_data": '{"is_client":false}',
                "tag_id": "", "share_aweme_id": "", "live_insert_type": "",
                "globalwid": "", "pull_type": "", "min_window": "",
                "free_right": "", "ug_source": "", "creative_id": "",
            }
        else:
            endpoint = "https://www.tiktok.com/api/recommend/item_list/"
            params = {
                "aid": "1988", "app_language": "en", "app_name": "tiktok_web",
                "browser_language": "en-US", "browser_name": "Mozilla", "browser_online": "true",
                "browser_platform": "Win32", "channel": "tiktok_web", "cookie_enabled": "true",
                "count": str(count), "cursor": str((page - 1) * count), "device_platform": "web_pc",
                "focus_state": "true", "from_page": "fyp", "history_len": "4", "is_fullscreen": "false",
                "is_page_visible": "true", "language": "en", "os": "windows", "region": "US",
                "screen_height": "1080", "screen_width": "1920", "tz_name": "Asia/Shanghai",
                "webcast_language": "en",
            }
        root = "https://www.douyin.com/jingxuan" if source == "douyin" else "https://www.tiktok.com/explore"
        videos: list[HomeVideo] = []
        seen: set[str] = set()
        if source == "douyin":
            # 抖音推荐流按 refresh_index 刷新批次。首页会话维护统一唯一池，
            # UI 页码只对池按固定 20 条切片，不再按页独立扫描 cursor。
            try:
                fingerprint = self._config_fingerprint("douyin")
            except (AttributeError, TypeError):
                fingerprint = "default"
            session_lock = getattr(self, "_douyin_home_session_lock", None)
            if session_lock is None:
                session_lock = self._douyin_home_session_lock = threading.Lock()
            sessions = getattr(self, "_douyin_home_sessions", None)
            if sessions is None:
                sessions = self._douyin_home_sessions = OrderedDict()
            with session_lock:
                previous = sessions.get(fingerprint)
                if previous is None or (
                    force_refresh
                    and page == 1
                    and 1 in previous.get("pages", {})
                ):
                    session = {
                        "items": [],
                        "seen_ids": set(),
                        "pages": {},
                        "next_refresh_index": 1,
                        "has_more": True,
                    }
                else:
                    session = previous
                cached_range = session["pages"].get(page)
                if cached_range is not None and not force_refresh:
                    start, end = cached_range
                    batch_items = deepcopy(session["items"][start:end])
                    has_more = bool(session["has_more"])
                    request_count = 0
                else:
                    if page * count > _MAX_DOUYIN_HOME_POOL_ITEMS:
                        raise RuntimeError("抖音首页当前会话最多浏览 10 页，请刷新首页建立新的推荐内容")
                    if page > 1 and len(session["items"]) < (page - 1) * count:
                        raise RuntimeError("请先加载抖音首页的上一页，以便建立连续内容池")
                    required_end = page * count
                    request_count = 0
                    has_more = True
                    browser_client = getattr(self, "_douyin_browser_client", None)
                    try:
                        while len(session["items"]) < required_end and request_count < 2:
                            refresh_index = int(session["next_refresh_index"])
                            request_params = dict(
                                params,
                                cursor="0",
                                count="10",
                                refresh_index=str(refresh_index),
                            )
                            if browser_client is not None and hasattr(browser_client, "request_home_json"):
                                payload = browser_client.request_home_json(
                                    endpoint,
                                    request_params,
                                    root,
                                    page=page,
                                    target_count=max(count, required_end - len(session["items"])),
                                    refresh_index=refresh_index,
                                )
                            else:
                                payload = self._request_douyin_browse_json(endpoint, request_params, root)
                            request_count += 1
                            raw_batch = [
                                item for item in (payload.get("aweme_list") or [])
                                if isinstance(item, dict)
                            ]
                            added = 0
                            for item in raw_batch:
                                video_id = str(item.get("aweme_id") or "").strip()
                                if (
                                    video_id
                                    and self._douyin_card_is_displayable(item)
                                    and video_id not in session["seen_ids"]
                                    and len(session["items"]) < _MAX_DOUYIN_HOME_POOL_ITEMS
                                ):
                                    session["seen_ids"].add(video_id)
                                    session["items"].append(item)
                                    added += 1
                            has_more = bool(payload.get("has_more")) or bool(raw_batch)
                            next_refresh_index = int(
                                payload.get("next_refresh_index")
                                or refresh_index + 1
                            )
                            session["next_refresh_index"] = max(refresh_index + 1, next_refresh_index)
                            logger.info(
                                "douyin home pool refill page=%s refresh=%s raw=%s added=%s pool=%s",
                                page,
                                refresh_index,
                                len(raw_batch),
                                added,
                                len(session["items"]),
                            )
                            if not has_more or not added:
                                break
                    except Exception:
                        if len(session["items"]) >= page * count:
                            has_more = True
                            logger.warning(
                                "douyin home refill interrupted page=%s pool=%s requests=%s",
                                page,
                                len(session["items"]),
                                request_count,
                            )
                        elif previous is not None and page in previous["pages"]:
                            start, end = previous["pages"][page]
                            batch_items = deepcopy(previous["items"][start:end])
                            has_more = True
                            request_count = 0
                        else:
                            raise
                    if session["items"]:
                        session["has_more"] = has_more
                        sessions[fingerprint] = session
                        sessions.move_to_end(fingerprint)
                        while len(sessions) > 4:
                            sessions.popitem(last=False)
                    if len(session["items"]) >= page * count:
                        start = (page - 1) * count
                        batch_items = deepcopy(session["items"][start : start + count])
                        session["pages"][page] = (start, start + count)
                    elif previous is None or page not in previous["pages"]:
                        raise RuntimeError(
                            f"抖音首页正在补充推荐内容，当前收集到 {len(session['items'])} 条，尚未达到完整 20 条"
                        )
            for item in batch_items:
                self._short_video_item_store(source, item)
                parsed = self._short_video_home_item(item, source)
                if parsed is not None and parsed.video_id not in seen:
                    seen.add(parsed.video_id)
                    videos.append(parsed)
            logger.info(
                "douyin home browse completed page=%s count=%s requests=%s has_next=%s",
                page,
                len(videos),
                request_count,
                has_more,
            )
            has_next = bool(has_more and page * count < _MAX_DOUYIN_HOME_POOL_ITEMS)
            return videos[:count], has_next
        cursor = (page - 1) * count if source != "douyin" else 0
        has_more = False
        max_rounds = 32 if source == "douyin" else 8
        for _ in range(max_rounds):
            params["cursor"] = str(cursor)
            payload = self._request_short_video_json(source, endpoint, params, root)
            raw_items = payload.get("aweme_list") if source == "douyin" else payload.get("itemList")
            raw_items = [item for item in (raw_items or []) if isinstance(item, dict)]
            for raw_item in raw_items:
                self._short_video_item_store(source, raw_item)
            batch = [self._short_video_home_item(item, source) for item in raw_items]
            added = 0
            for item in batch:
                if item is not None and item.video_id not in seen:
                    seen.add(item.video_id)
                    videos.append(item)
                    added += 1
            has_more = bool(payload.get("has_more") or payload.get("hasMore"))
            if len(videos) >= page * target_count or not has_more:
                break
            cursor += max(1, len(raw_items or []))
        # TikTok 的 cursor 已经在请求阶段定位到 page；与 YouTube 的累计
        # 条目接口不同，不能再次按 page 偏移切片，否则第二页会被切成空表。
        start = 0 if source == "tiktok" else (page - 1) * target_count
        return videos[start : start + target_count], has_more

    def _fetch_xiaohongshu_home(
        self,
        page: int,
        page_size: int,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[HomeVideo], bool]:
        client = getattr(self, "_xiaohongshu_browser_client", None)
        if client is None or not hasattr(client, "request_home"):
            raise RuntimeError("小红书首页浏览器服务尚未初始化")
        page = max(1, int(page))
        count = min(20, max(1, int(page_size)))
        payload = client.request_home(page, count, force_refresh=force_refresh)
        raw_items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        videos = self._xiaohongshu_videos(raw_items, context="home")
        start = (page - 1) * count
        page_videos = videos[start : start + count]
        if not page_videos:
            raise RuntimeError("小红书首页没有返回可播放的视频，请检查 Cookie 或完成安全验证")
        has_next = bool(payload.get("has_more")) or len(videos) > start + count
        logger.info(
            "xiaohongshu home completed page=%s raw=%s videos=%s page_count=%s has_next=%s",
            page,
            len(raw_items),
            len(videos),
            len(page_videos),
            has_next,
        )
        return page_videos, has_next

    def _xiaohongshu_videos(self, raw_items: list[dict], *, context: str) -> list[HomeVideo]:
        videos: list[HomeVideo] = []
        seen: set[str] = set()
        for item in raw_items:
            video = self._xiaohongshu_home_item(item, context=context)
            if video is None or video.video_id in seen:
                continue
            seen.add(video.video_id)
            videos.append(video)
            self._xiaohongshu_item_store(item, video.webpage_url)
        return videos

    @staticmethod
    def _xiaohongshu_home_item(item: dict, *, context: str) -> HomeVideo | None:
        card = item.get("note_card") or item.get("noteCard") or item
        if not isinstance(card, dict):
            return None
        item_type = str(card.get("type") or item.get("type") or "").strip().lower()
        if item_type and item_type != "video":
            return None
        note_id = str(
            item.get("id") or card.get("note_id") or card.get("noteId") or card.get("id") or ""
        ).strip()
        if not re.fullmatch(r"[0-9a-fA-F]+", note_id):
            return None
        title = str(
            card.get("display_title") or card.get("title") or item.get("title") or
            card.get("desc") or item.get("desc") or note_id
        ).strip()
        user = card.get("user") or item.get("user") or {}
        user = user if isinstance(user, dict) else {}
        uploader = str(
            user.get("nickname") or user.get("nick_name") or user.get("name") or
            user.get("user_name") or "小红书用户"
        ).strip()
        cover = card.get("cover") or item.get("cover") or {}
        cover = cover if isinstance(cover, dict) else {}
        thumbnail = str(
            cover.get("url_default") or cover.get("urlDefault") or cover.get("url_pre") or
            cover.get("urlPre") or cover.get("url") or ""
        ).strip()
        if not thumbnail:
            images = card.get("image_list") or card.get("imageList") or item.get("images_list") or []
            if isinstance(images, list) and images:
                image = images[0] if isinstance(images[0], dict) else {}
                thumbnail = str(
                    image.get("url_default") or image.get("urlDefault") or image.get("url_pre") or
                    image.get("urlPre") or image.get("url") or ""
                ).strip()
        token = str(item.get("xsec_token") or card.get("xsec_token") or "").strip()
        query = {
            "xsec_source": (
                "pc_user" if context == "creator" else
                "pc_search" if context == "search" else "pc_feed"
            )
        }
        if token:
            query["xsec_token"] = token
        webpage_url = f"https://www.xiaohongshu.com/explore/{note_id}?{urllib.parse.urlencode(query)}"
        duration_raw = (
            (card.get("video") or {}).get("duration")
            if isinstance(card.get("video"), dict)
            else card.get("duration")
        )
        try:
            duration = int(float(duration_raw or 0))
            if duration > 10000:
                duration //= 1000
        except (TypeError, ValueError):
            duration = 0
        return HomeVideo(
            video_id=f"xiaohongshu:{note_id}",
            title=title,
            webpage_url=webpage_url,
            source_site="xiaohongshu",
            uploader=uploader,
            duration=duration,
            thumbnail=thumbnail,
        )

    def _xiaohongshu_item_store(self, item: dict, url: str) -> None:
        card = item.get("note_card")
        card = card if isinstance(card, dict) else item
        note_id = str(card.get("note_id") or card.get("noteId") or item.get("id") or "").strip()
        if not note_id:
            match = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", str(url or ""), re.IGNORECASE)
            note_id = match.group(1) if match else ""
        if not note_id:
            return
        with self._xiaohongshu_item_cache_lock:
            self._xiaohongshu_item_cache[note_id.lower()] = (time.time(), deepcopy(item))
            self._xiaohongshu_item_cache.move_to_end(note_id.lower())
            while len(self._xiaohongshu_item_cache) > _MAX_XIAOHONGSHU_ITEM_CACHE_ITEMS:
                self._xiaohongshu_item_cache.popitem(last=False)

    def _xiaohongshu_item_lookup(self, url: str) -> dict | None:
        match = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", str(url or ""), re.IGNORECASE)
        if not match:
            return None
        key = match.group(1).lower()
        with self._xiaohongshu_item_cache_lock:
            cached = self._xiaohongshu_item_cache.get(key)
            if cached is None:
                return None
            cached_at, item = cached
            if time.time() - cached_at > _XIAOHONGSHU_ITEM_CACHE_TTL_SECONDS:
                self._xiaohongshu_item_cache.pop(key, None)
                return None
            self._xiaohongshu_item_cache.move_to_end(key)
            return deepcopy(item)

    def _enrich_xiaohongshu_video(self, video: VideoInfo, requested_url: str) -> VideoInfo:
        raw_id = str(video.video_id or "").split(":", 1)[-1]
        if not raw_id:
            match = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", requested_url, re.IGNORECASE)
            raw_id = match.group(1) if match else ""
        video.source_site = "xiaohongshu"
        video.video_id = f"xiaohongshu:{raw_id}" if raw_id else video.video_id
        video.webpage_url = requested_url or video.webpage_url
        if video.creator_id or video.channel_id:
            creator_id = str(video.creator_id or video.channel_id).strip()
            video.creator_id = creator_id
            video.channel_id = creator_id
            video.creator_url = video.creator_url or f"https://www.xiaohongshu.com/user/profile/{creator_id}"
        cached = self._xiaohongshu_item_lookup(requested_url)
        if cached is None:
            return video
        home = self._xiaohongshu_home_item(cached, context="home")
        if home is not None:
            if home.uploader and home.uploader != "小红书用户":
                video.uploader = home.uploader
            if not video.thumbnail:
                video.thumbnail = home.thumbnail
            if not video.title:
                video.title = home.title
            card = cached.get("note_card") if isinstance(cached.get("note_card"), dict) else {}
            user = cached.get("user") or card.get("user") or {}
            if not isinstance(user, dict):
                user = {}
            user_id = str(user.get("userId") or user.get("user_id") or "").strip()
            if user_id:
                video.creator_id = user_id
                video.channel_id = user_id
                video.creator_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        return video

    def _video_info_from_xiaohongshu_detail(self, payload: dict, requested_url: str) -> VideoInfo:
        note = payload.get("note") if isinstance(payload, dict) else None
        if not isinstance(note, dict):
            raise RuntimeError("小红书详情页没有返回笔记数据")
        note_id = str(note.get("noteId") or note.get("note_id") or note.get("id") or "").strip()
        if not note_id:
            match = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", requested_url, re.IGNORECASE)
            note_id = match.group(1) if match else ""
        video_data = note.get("video") if isinstance(note.get("video"), dict) else {}
        media = video_data.get("media") if isinstance(video_data.get("media"), dict) else {}
        stream = media.get("stream") or {}
        stream_rows = self._xiaohongshu_stream_rows(stream)
        formats: list[dict] = []
        for index, row in enumerate(stream_rows):
            urls: list[str] = []
            master = str(row.get("masterUrl") or row.get("master_url") or "").strip()
            if master:
                urls.append(master)
            backups = row.get("backupUrls") or row.get("backup_urls") or []
            if isinstance(backups, list):
                urls.extend(str(value or "").strip() for value in backups)
            urls = [value for value in urls if value.startswith(("http://", "https://"))]
            if not urls:
                continue
            try:
                duration_ms = float(row.get("duration") or video_data.get("duration") or 0)
            except (TypeError, ValueError):
                duration_ms = 0.0
            for url_index, media_url in enumerate(dict.fromkeys(urls)):
                formats.append(
                    {
                        "format_id": f"xhs-{index}-{url_index}",
                        "url": media_url,
                        "ext": "mp4",
                        "protocol": "https" if media_url.startswith("https://") else "http",
                        "fps": int(row.get("fps") or 0),
                        "width": int(row.get("width") or 0),
                        "height": int(row.get("height") or 0),
                        "vcodec": str(row.get("videoCodec") or row.get("video_codec") or "h264"),
                        "acodec": str(row.get("audioCodec") or row.get("audio_codec") or "aac"),
                        "abr": float(row.get("audioBitrate") or 0) / 1000,
                        "vbr": float(row.get("videoBitrate") or 0) / 1000,
                        "tbr": float(row.get("avgBitrate") or 0) / 1000,
                        "filesize": int(row.get("size") or 0) or None,
                        "duration": duration_ms / 1000 if duration_ms > 1000 else duration_ms,
                        "format_note": str(row.get("qualityType") or ""),
                    }
                )
        if not formats:
            raise RuntimeError("该小红书笔记不是视频，或页面没有提供可播放媒体")
        images = note.get("imageList") or note.get("image_list") or []
        thumbnails: list[dict] = []
        if isinstance(images, list):
            for image in images:
                if not isinstance(image, dict):
                    continue
                for key in ("urlDefault", "urlPre", "url_default", "url_pre", "url"):
                    url = str(image.get(key) or "").strip()
                    if url:
                        thumbnails.append(
                            {"url": url, "width": image.get("width"), "height": image.get("height")}
                        )
                        break
        user = note.get("user") if isinstance(note.get("user"), dict) else {}
        uploader_id = str(user.get("userId") or user.get("user_id") or "").strip()
        uploader = str(user.get("nickname") or user.get("nick_name") or "小红书用户").strip()
        webpage_url = str(payload.get("url") or requested_url).strip()
        raw_info = {
            "id": note_id,
            "title": str(note.get("title") or note.get("displayTitle") or note.get("desc") or note_id),
            "description": str(note.get("desc") or ""),
            "uploader": uploader,
            "uploader_id": uploader_id,
            "uploader_url": f"https://www.xiaohongshu.com/user/profile/{uploader_id}" if uploader_id else "",
            "webpage_url": webpage_url,
            "formats": formats,
            "thumbnails": thumbnails,
            "thumbnail": str((thumbnails[-1] if thumbnails else {}).get("url") or ""),
            "duration": max(int(float(item.get("duration") or 0)) for item in formats),
            "http_headers": {
                "Referer": webpage_url,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
            },
            "_tube_player_browser_fallback": True,
        }
        return self.youtube._parse_info(raw_info)

    @classmethod
    def _xiaohongshu_stream_rows(cls, value: object) -> list[dict]:
        rows: list[dict] = []

        def visit(node: object) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, dict):
                return
            if node.get("masterUrl") or node.get("master_url") or node.get("backupUrls"):
                rows.append(node)
                return
            for child in node.values():
                visit(child)

        visit(value)
        return rows

    def _request_douyin_browse_json(
        self, endpoint: str, params: dict[str, str], referer: str
    ) -> dict:
        """Serialize and pace Douyin feed/search requests for one browser session."""
        browser_client = getattr(self, "_douyin_browser_client", None)
        if browser_client is not None:
            timeout = 80.0 if "/series/list/" in endpoint else 60.0
            try:
                return browser_client.request_json(endpoint, params, referer, timeout=timeout)
            except RuntimeError as exc:
                if "API 请求超时" not in str(exc):
                    raise
                # 页面签名函数或 fetch 可能被 Chromium 暂时卡住。浏览器服务在超时
                # 时已熔断当前运行时；给前台搜索一次延迟重试，避免用户手动重启应用。
                logger.warning(
                    "douyin browser request timeout; retrying once endpoint=%s",
                    urllib.parse.urlparse(endpoint).path,
                )
                return browser_client.request_json(endpoint, params, referer, timeout=60.0)
        lock = getattr(self, "_douyin_browse_request_lock", None)
        if lock is None:
            lock = self._douyin_browse_request_lock = threading.Lock()
        interval = max(
            0.0,
            float(getattr(self, "_douyin_browse_min_interval_seconds", _DOUYIN_BROWSE_MIN_INTERVAL_SECONDS)),
        )
        with lock:
            last_request_at = float(getattr(self, "_douyin_browse_last_request_at", 0.0))
            delay = interval - (time.monotonic() - last_request_at)
            if delay > 0:
                time.sleep(delay)
            try:
                return self._request_short_video_json("douyin", endpoint, params, referer)
            finally:
                self._douyin_browse_last_request_at = time.monotonic()

    def _request_short_video_json(self, source: str, endpoint: str, params: dict[str, str], referer: str) -> dict:
        query = urllib.parse.urlencode(params)
        url = f"{endpoint}?{query}"
        browser = self.config.cookie_browser_for_site(source) if getattr(self, "config", None) is not None else ""
        if str(browser or "").split(":", 1)[0].strip().lower() == "firefox":
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) "
                "Gecko/20100101 Firefox/142.0"
            )
        else:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        headers = {
            "User-Agent": user_agent,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if source in {"douyin", "tiktok"}:
            headers.update({
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            })
        if source == "tiktok":
            headers["Origin"] = "https://www.tiktok.com"
        if browser:
            cookie = load_browser_cookie_header(browser, referer)
            if cookie:
                headers["Cookie"] = cookie
        _label, proxy = self.config.effective_proxy()
        opener = urllib.request.build_opener()
        if proxy:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
                data = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            if source == "tiktok" and exc.code in {400, 403}:
                # TikTok rejects some optional browser telemetry parameters for
                # particular sessions. Warm the Explore page and retry with the
                # minimal stable parameter set used by its web client.
                try:
                    with opener.open(urllib.request.Request(referer, headers=headers), timeout=30):
                        pass
                    minimal = {key: value for key, value in params.items() if key in {
                        "aid", "app_name", "channel", "count", "cursor", "device_platform",
                        "from_page", "region", "language", "webcast_language",
                    }}
                    # Search requests need the full browser-shaped query. For
                    # 403 responses retry the original query after warming the
                    # page; for 400 retain the known-good minimal retry.
                    if exc.code == 403:
                        minimal = dict(params)
                    retry_url = f"{endpoint}?{urllib.parse.urlencode(minimal)}"
                    with opener.open(urllib.request.Request(retry_url, headers=headers), timeout=30) as response:
                        data = json.loads(response.read().decode("utf-8", "replace") or "{}")
                except Exception as retry_exc:
                    raise RuntimeError(f"TIKTOK 首页暂时无法获取。请检查 Cookie、地区或代理后重试。\n{retry_exc}") from retry_exc
            else:
                raise RuntimeError(f"{source.upper()} 首页暂时无法获取。请检查 Cookie、地区或代理后重试。\n{exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"{source.upper()} 首页暂时无法获取。请检查 Cookie、地区或代理后重试。\n{exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{source.upper()} 首页返回数据格式无效")
        return data

    def _short_video_item_store(self, source: str, item: dict, url: str = "") -> None:
        if not isinstance(item, dict):
            return
        raw_value = item.get("aweme_id") if source == "douyin" else item.get("id")
        raw_id = str(raw_value or "").strip()
        if not raw_id:
            return
        author = item.get("author") or {}
        unique_id = str(
            author.get("uniqueId") or author.get("unique_id") or author.get("uniqueID") or "_"
        ).strip() if isinstance(author, dict) else "_"
        aliases = {
            f"{source}:{raw_id}",
            raw_id,
            str(url or "").strip(),
        }
        if source == "douyin":
            aliases.add(f"https://www.douyin.com/video/{raw_id}")
        else:
            aliases.add(f"https://www.tiktok.com/@{unique_id}/video/{raw_id}")
        now = time.time()
        lock = getattr(self, "_short_video_item_cache_lock", None)
        if lock is None:
            lock = self._short_video_item_cache_lock = threading.Lock()
        cache = getattr(self, "_short_video_item_cache", None)
        if cache is None:
            cache = self._short_video_item_cache = OrderedDict()
        elif not hasattr(cache, "move_to_end"):
            cache = self._short_video_item_cache = OrderedDict(cache)
        with lock:
            for alias in aliases:
                if alias:
                    key = f"{source}|{alias}"
                    cache[key] = (now, source, deepcopy(item))
                    cache.move_to_end(key)
            while len(cache) > _MAX_SHORT_VIDEO_ITEM_CACHE_ITEMS:
                cache.popitem(last=False)

    def _short_video_item_lookup(self, url: str) -> tuple[str, dict] | None:
        source = site_for_url(url)
        if source not in {"douyin", "tiktok"}:
            return None
        raw = str(url or "").strip()
        parsed = urllib.parse.urlparse(raw)
        aliases = [raw]
        match = re.search(r"/(?:video|photo|aweme/detail)/([0-9A-Za-z_-]+)", parsed.path, re.I)
        if match:
            aliases.extend([match.group(1), f"{source}:{match.group(1)}"])
        lock = getattr(self, "_short_video_item_cache_lock", None)
        cache = getattr(self, "_short_video_item_cache", None)
        if lock is None or cache is None:
            return None
        with lock:
            for alias in aliases:
                cached = cache.get(f"{source}|{alias}")
                if cached is None:
                    continue
                cached_at, cached_source, item = cached
                if time.time() - cached_at > _SHORT_VIDEO_ITEM_CACHE_TTL_SECONDS:
                    cache.pop(f"{source}|{alias}", None)
                    continue
                cache.move_to_end(f"{source}|{alias}")
                return cached_source, deepcopy(item)
            # Aliases are individually bounded. A creator playlist can evict the
            # URL alias while retaining the same raw ID under another alias; scan
            # values before declaring a cache miss.
            raw_id = ""
            for candidate in aliases:
                if candidate.startswith(f"{source}:"):
                    raw_id = candidate.split(":", 1)[1]
                    break
                if candidate.isdigit():
                    raw_id = candidate
                    break
            if raw_id:
                for cached_at, cached_source, item in reversed(list(cache.values())):
                    if cached_source != source or time.time() - cached_at > _SHORT_VIDEO_ITEM_CACHE_TTL_SECONDS:
                        continue
                    item_id = str(item.get("aweme_id") if source == "douyin" else item.get("id") or "").strip()
                    if item_id == raw_id:
                        return cached_source, deepcopy(item)
        return None

    def _recover_tiktok_item(self, url: str) -> dict | None:
        parsed = urllib.parse.urlparse(str(url or ""))
        match = re.search(r"/(?:video|photo)/([0-9]+)", parsed.path, re.I)
        if not match:
            return None
        raw_id = match.group(1)
        handle = ""
        path_parts = [part for part in parsed.path.split("/") if part]
        for index, part in enumerate(path_parts[:-1]):
            if part.startswith("@"):
                handle = part[1:]
                break
        queries = [value for value in (handle, raw_id) if value]
        for query in queries:
            try:
                self._search_tiktok(query, 1, 20)
            except Exception as exc:
                logger.info("TikTok item recovery search failed query=%s id=%s: %s", query, raw_id, exc)
                continue
            cached = self._short_video_item_lookup(url)
            if cached is not None:
                logger.info("TikTok item recovered id=%s query=%s", raw_id, query)
                return cached[1]
        return None

    def _video_info_from_short_video_item(self, item: dict, source: str, requested_url: str) -> VideoInfo:
        raw_value = item.get("aweme_id") if source == "douyin" else item.get("id")
        raw_id = str(raw_value or "").strip()
        if not raw_id:
            raise ValueError("short video item has no id")
        author = item.get("author") or {}
        if source == "douyin":
            uploader = str(author.get("nickname") or "").strip() if isinstance(author, dict) else ""
            creator_id = str(author.get("sec_uid") or author.get("uid") or "").strip() if isinstance(author, dict) else ""
            creator_url = f"https://www.douyin.com/user/{creator_id}" if creator_id else ""
            webpage_url = f"https://www.douyin.com/video/{raw_id}"
            title = str(item.get("desc") or item.get("item_title") or raw_id).strip()
            video = item.get("video") or {}
            duration = int(float(video.get("duration") or 0) / 1000)
        else:
            unique_id = str(
                author.get("uniqueId") or author.get("unique_id") or author.get("uniqueID") or "_"
            ).strip() if isinstance(author, dict) else "_"
            uploader = str(author.get("nickname") or unique_id).strip() if isinstance(author, dict) else unique_id
            creator_id = str(author.get("secUid") or author.get("sec_uid") or unique_id).strip() if isinstance(author, dict) else unique_id
            creator_url = f"https://www.tiktok.com/@{unique_id}" if unique_id and unique_id != "_" else ""
            webpage_url = f"https://www.tiktok.com/@{unique_id}/video/{raw_id}"
            title = str(item.get("desc") or raw_id).strip()
            video = item.get("video") or {}
            duration = int(video.get("duration") or 0)

        formats: list[dict] = []

        def add_play(play: dict | str, index: int, codec: str = "") -> None:
            if isinstance(play, str):
                urls = [play]
                play = {}
            elif isinstance(play, dict):
                urls = play.get("UrlList") or play.get("url_list") or play.get("urlList") or []
            else:
                return
            if isinstance(urls, str):
                urls = [urls]
            url = self._preferred_short_video_url(urls, source)
            if not url:
                return
            width = int(play.get("Width") or play.get("width") or video.get("Width") or video.get("width") or 0)
            height = int(play.get("Height") or play.get("height") or video.get("Height") or video.get("height") or 0)
            formats.append({"format_id": f"{source}-{index}-{width}x{height}", "url": url,
                            "ext": "mp4", "protocol": "https", "width": width, "height": height,
                            "fps": int(play.get("Fps") or play.get("fps") or 30),
                            "vcodec": codec or str(play.get("CodecType") or play.get("codecType") or "h264"),
                            "acodec": "aac", "tbr": float(play.get("Bitrate") or play.get("bitrate") or 0) / 1000})
        bitrate_items = video.get("bitrateInfo") or video.get("bit_rate") or []
        struct = video.get("PlayAddrStruct") or video.get("play_addr") or video.get("playAddr") or {}
        # TikTok search cards expose ``video.playAddr`` as a lone raw CDN URL
        # alongside ``bitrateInfo[*].PlayAddr.UrlList``. The raw URL is often a
        # higher nominal resolution but returns 403; the bitrate list contains
        # the authenticated same-origin gateway. Do not let the unusable raw
        # URL win quality sorting merely because its dimensions are larger.
        if not (source == "tiktok" and isinstance(struct, str) and bitrate_items):
            add_play(struct, 0)
        for index, bitrate in enumerate(bitrate_items, 1):
            if not isinstance(bitrate, dict):
                continue
            play = bitrate.get("PlayAddr") or bitrate.get("play_addr") or bitrate.get("playAddr") or {}
            add_play(play, index, str(bitrate.get("CodecType") or bitrate.get("codec_type") or ""))
        if not formats:
            add_play(video.get("downloadAddr") or video.get("download_addr") or {}, 999)
        qualities = QualitySelector.select_all(formats)
        if not qualities:
            raise ValueError("short video item has no playable media URL")
        cover = video.get("cover") or video.get("Cover") or video.get("dynamicCover") or ""
        if isinstance(cover, dict):
            cover = (cover.get("url_list") or cover.get("UrlList") or [""])[0]
        cookie_browser = self.config.cookie_browser_for_site(source) if getattr(self, "config", None) is not None else ""
        browser_kind = cookie_browser.split(":", 1)[0].strip().lower()
        if browser_kind == "firefox":
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0"
        else:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        headers = {"User-Agent": user_agent,
                   "Referer": webpage_url, "Origin": "https://www.tiktok.com" if source == "tiktok" else "https://www.douyin.com"}
        browser = cookie_browser
        if browser:
            cookie = load_browser_cookie_header(browser, webpage_url)
            playback_cookie = self._compact_short_video_cookie_header(cookie, source)
            if playback_cookie:
                headers["Cookie"] = playback_cookie
        return VideoInfo(video_id=f"{source}:{raw_id}", title=title or raw_id, source_site=source,
                         uploader=uploader, channel_id=creator_id, creator_id=creator_id,
                         creator_url=creator_url, duration=duration, webpage_url=webpage_url or requested_url,
                         thumbnail=str(cover or ""), qualities=qualities, http_headers=headers, raw_info=item)

    @staticmethod
    def _preferred_short_video_url(urls: object, source: str) -> str:
        """Prefer the authenticated same-origin media gateway over raw CDN hosts.

        Douyin and TikTok commonly put two CDN URLs before an ``/aweme/v1/play/``
        URL. Those CDN URLs can return 403 for a valid logged-in browser session,
        while the same-origin gateway validates the Cookie and redirects to a
        usable CDN object. Preserve the original order as the fallback for older
        payloads that contain only direct media URLs.
        """
        if isinstance(urls, str):
            candidates = [urls]
        elif isinstance(urls, (list, tuple)):
            candidates = [str(value or "").strip() for value in urls]
        else:
            candidates = []
        candidates = [url for url in candidates if url.startswith(("http://", "https://"))]
        expected_host = f"www.{source}.com"
        return next(
            (
                url
                for url in candidates
                if urllib.parse.urlparse(url).hostname == expected_host
                and "/aweme/v1/play/" in urllib.parse.urlparse(url).path
            ),
            candidates[0] if candidates else "",
        )

    @staticmethod
    def _compact_short_video_cookie_header(cookie: str, source: str) -> str:
        """Keep mpv's media request headers below FFmpeg's header-size limit.

        A logged-in Douyin profile can accumulate tens of kilobytes of UI and
        experiment cookies. Forwarding all of them makes FFmpeg fail with
        ``overlong headers`` before it reads any media bytes. The signed media
        gateways only need the core session/device cookies.
        """
        common_names = {
            "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "ttwid",
            "s_v_web_id", "msToken", "tt_csrf_token",
        }
        if source == "douyin":
            common_names.update({
                "passport_csrf_token", "__ac_nonce", "__ac_signature",
            })
        elif source == "xiaohongshu":
            common_names.update({
                "a1", "web_session", "id_token", "webId", "gid", "websectiga", "xsecappid",
            })
        selected: list[str] = []
        for part in str(cookie or "").split(";"):
            name, separator, _value = part.strip().partition("=")
            if separator and name in common_names:
                selected.append(part.strip())
        return "; ".join(selected)

    @staticmethod
    def _short_video_home_item(item: dict, source: str) -> HomeVideo | None:
        if source == "douyin":
            video_id = str(item.get("aweme_id") or "").strip()
            title = str(item.get("desc") or item.get("item_title") or "").strip()
            author = item.get("author") or {}
            video = item.get("video") or {}
            thumbnail = ""
            if isinstance(video, dict):
                for cover_key in ("cover", "origin_cover", "dynamic_cover", "animated_cover"):
                    cover = video.get(cover_key) or {}
                    urls = cover.get("url_list") if isinstance(cover, dict) else []
                    if isinstance(urls, list) and urls:
                        thumbnail = str(urls[0] or "").strip()
                        if thumbnail:
                            break
            url = f"https://www.douyin.com/video/{video_id}" if video_id else ""
            duration = int(float(video.get("duration") or 0) / 1000) if isinstance(video, dict) else 0
            uploader = str(author.get("nickname") or "").strip() if isinstance(author, dict) else ""
        else:
            video_id = str(item.get("id") or "").strip()
            title = str(item.get("desc") or "").strip()
            author = item.get("author") or {}
            video = item.get("video") or {}
            thumbnail = str(video.get("cover") or video.get("dynamicCover") or "")
            unique_id = str(
                author.get("uniqueId") or author.get("unique_id") or author.get("uniqueID") or "_"
            ).strip() if isinstance(author, dict) else "_"
            url = f"https://www.tiktok.com/@{unique_id}/video/{video_id}" if video_id else ""
            duration = int((video.get("duration") or 0) if isinstance(video, dict) else 0)
            uploader = str(author.get("nickname") or unique_id).strip() if isinstance(author, dict) else ""
        if not video_id or not url:
            return None
        return HomeVideo(video_id=f"{source}:{video_id}", title=title or video_id, webpage_url=url,
                         source_site=source, uploader=uploader, duration=duration, thumbnail=thumbnail)

    @classmethod
    def _douyin_card_is_displayable(cls, item: dict) -> bool:
        video = cls._short_video_home_item(item, "douyin")
        if video is None:
            return False
        # 作者为空时 UI 会回退显示站点名 "Douyin"。该占位作者与空封面同时
        # 出现的条目在真实环境中无法播放，不允许进入首页池或搜索结果。
        uploader = str(video.uploader or "").strip().casefold()
        thumbnail = str(video.thumbnail or "").strip()
        return bool(thumbnail or (uploader and uploader != "douyin"))

    def search_videos(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 56,
        *,
        force_refresh: bool = False,
        source: str = "",
    ) -> tuple[list[HomeVideo], bool]:
        source = self.normalize_source(source) or self.home_source()
        query = str(keyword or "").strip()
        key = self._cache_key("search", source, query, page, page_size)
        cached = self._cache_lookup(key, _SEARCH_CACHE_TTL_SECONDS)
        if not force_refresh and cached:
            return cached
        try:
            if source == "bilibili":
                result = self.bilibili.search_videos(query, page, page_size)
            elif source == "douyin":
                result = self._search_douyin(query, page, page_size, force_refresh=force_refresh)
            elif source == "tiktok":
                try:
                    result = self._search_tiktok(query, page, page_size)
                except Exception as exc:
                    logger.warning("TikTok official search failed; using recommendation fallback: %s", exc)
                    result = self._search_tiktok_fallback(query, page, page_size)
            elif source == "xiaohongshu":
                result = self._search_xiaohongshu(query, page, page_size, force_refresh=force_refresh)
            else:
                result = self.youtube.search_videos(query, page, page_size)
        except Exception:
            if source == "douyin" and cached:
                logger.warning(
                    "douyin search refresh blocked; retaining cached page query=%s page=%s count=%s",
                    query,
                    page,
                    len(cached[0]),
                )
                return list(cached[0]), True
            raise
        self._cache_store(key, result)
        return result

    def _search_xiaohongshu(
        self,
        keyword: str,
        page: int,
        page_size: int,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[HomeVideo], bool]:
        client = getattr(self, "_xiaohongshu_browser_client", None)
        if client is None or not hasattr(client, "request_search"):
            raise RuntimeError("小红书搜索浏览器服务尚未初始化")
        page = max(1, int(page))
        count = min(20, max(1, int(page_size)))
        payload = client.request_search(
            keyword,
            page,
            count,
            force_refresh=force_refresh,
        )
        raw_items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        videos = self._xiaohongshu_videos(raw_items, context="search")
        start = (page - 1) * count
        page_videos = videos[start : start + count]
        if not page_videos:
            raise RuntimeError("小红书搜索没有返回可播放的视频，请检查 Cookie 或完成安全验证")
        return page_videos, bool(payload.get("has_more")) or len(videos) > start + count

    def _search_tiktok(self, keyword: str, page: int, page_size: int) -> tuple[list[HomeVideo], bool]:
        """Search TikTok's web endpoint using the browser-shaped parameter set.

        The endpoint is sensitive to missing telemetry fields and returns 403 or
        an empty body when called with the abbreviated recommendation parameters.
        It currently responds with ``item_list`` (not ``itemList``) for this route.
        """
        page = max(1, int(page))
        target_count = max(1, min(100, int(page_size)))
        count = min(20, target_count)
        query = str(keyword or "").strip()
        referer = "https://www.tiktok.com/search?q=" + urllib.parse.quote(query)
        cookie = ""
        browser = self.config.cookie_browser_for_site("tiktok")
        if browser:
            cookie = load_browser_cookie_header(browser, referer)
        cookie_values: dict[str, str] = {}
        for part in cookie.split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                cookie_values[key] = value
        params = {
            "WebIdLastTime": str(int(time.time())),
            "aid": "1988", "app_language": "en", "app_name": "tiktok_web",
            "browser_language": "zh-CN", "browser_name": "Mozilla", "browser_online": "true",
            "browser_platform": "Win32", "browser_version": "5.0 (Windows)",
            "channel": "tiktok_web", "cookie_enabled": "true", "count": str(count),
            "cursor": "0", "device_platform": "web_pc", "focus_state": "true",
            "device_id": "",
            "from_page": "search", "history_len": "2", "is_fullscreen": "false",
            "is_page_visible": "true", "keyword": query, "language": "en", "os": "windows",
            "priority_region": "", "referer": "", "region": "US", "screen_height": "1080",
            "screen_width": "1920", "tz_name": "Asia/Shanghai", "verifyFp": cookie_values.get("verify_fp", ""),
            "webcast_language": "en", "msToken": cookie_values.get("msToken", ""),
        }
        videos: list[HomeVideo] = []
        seen: set[str] = set()
        has_more = False
        cursor = 0
        search_id = ""
        wanted_end = page * target_count
        max_requests = max(1, (wanted_end + count - 1) // count + 2)
        for _ in range(max_requests):
            params["cursor"] = str(cursor)
            if search_id:
                params["search_id"] = search_id
            payload = self._request_short_video_json("tiktok", "https://www.tiktok.com/api/search/item/full/", params, referer)
            if not search_id:
                search_id = self._short_video_search_id(payload)
            raw_items = payload.get("item_list") or payload.get("itemList") or []
            if not isinstance(raw_items, list):
                raw_items = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                self._short_video_item_store("tiktok", item)
                parsed = self._short_video_home_item(item, "tiktok")
                if parsed is not None and parsed.video_id not in seen:
                    seen.add(parsed.video_id)
                    videos.append(parsed)
            current_has_more = bool(payload.get("has_more") or payload.get("hasMore"))
            has_more = current_has_more
            next_cursor = int(payload.get("cursor") or cursor + len(raw_items))
            if len(videos) >= wanted_end or not current_has_more or next_cursor == cursor or not raw_items:
                break
            cursor = next_cursor
        if not videos:
            raise RuntimeError("TikTok 搜索接口返回空结果")
        start = (page - 1) * target_count
        return videos[start:wanted_end], bool(has_more or len(videos) > wanted_end)

    def _search_douyin(
        self, keyword: str, page: int, page_size: int, *, force_refresh: bool = False
    ) -> tuple[list[HomeVideo], bool]:
        page = max(1, int(page))
        # 抖音搜索的原生批次最多约 20 条。一页对应一次平台请求，避免为了
        # 通用 UI 的 56 条页大小连续请求三到四次并触发安全校验。
        target_count = max(1, min(20, int(page_size)))
        count = min(20, target_count)
        referer = "https://www.douyin.com/search/" + urllib.parse.quote(keyword)
        fingerprint = ""
        if getattr(self, "config", None) is not None:
            try:
                fingerprint = self._config_fingerprint("douyin")
            except (AttributeError, TypeError):
                pass
        session_key = f"{str(keyword or '').strip().casefold()}|{target_count}|{fingerprint}"
        session_lock = getattr(self, "_douyin_search_session_lock", None)
        if session_lock is None:
            session_lock = self._douyin_search_session_lock = threading.Lock()
        sessions = getattr(self, "_douyin_search_sessions", None)
        if sessions is None:
            sessions = self._douyin_search_sessions = OrderedDict()
        cookie_browser = ""
        if getattr(self, "config", None) is not None:
            cookie_browser = str(self.config.cookie_browser_for_site("douyin") or "")
        use_firefox_identity = (
            getattr(self, "_douyin_browser_client", None) is None
            and cookie_browser.split(":", 1)[0].strip().lower() == "firefox"
        )
        browser_name = "Firefox" if use_firefox_identity else "Chrome"
        browser_version = "142.0" if use_firefox_identity else "131"
        engine_name = "Gecko" if use_firefox_identity else "Blink"
        engine_version = browser_version

        with session_lock:
            now = time.time()
            previous_session = sessions.get(session_key)
            if previous_session is not None and now - float(previous_session.get("updated_at", 0.0)) > _SEARCH_CACHE_TTL_SECONDS:
                sessions.pop(session_key, None)
                previous_session = None
            if previous_session is not None:
                sessions.move_to_end(session_key)

            # 翻页始终沿用 search_id/cursor。第一页明确刷新时才建立候选会话，
            # 且旧会话保留到候选请求成功，以便安全校验发生时无损回退。
            if previous_session is not None and (not force_refresh or page > 1):
                session = previous_session
            else:
                session = {
                    "updated_at": now,
                    "search_id": "",
                    "cursor": 0,
                    "items": [],
                    "seen_ids": set(),
                    "page_ranges": {},
                    "has_more": True,
                    "blocked_until": 0.0,
                }

            page_ranges: dict[int, tuple[int, int]] = session["page_ranges"]
            if page in page_ranges and not force_refresh:
                start, end = page_ranges[page]
                videos = [
                    self._short_video_home_item(item, "douyin")
                    for item in session["items"][start:end]
                    if self._douyin_card_is_displayable(item)
                ]
                videos = [video for video in videos if video is not None]
                has_next = bool(len(session["items"]) > end or session["has_more"])
                logger.info(
                    "douyin search session cache hit query=%s page=%s count=%s has_next=%s",
                    keyword,
                    page,
                    len(videos),
                    has_next,
                )
                return videos, has_next

            blocked_until = float(session.get("blocked_until", 0.0))
            if blocked_until > now:
                fallback_range = page_ranges.get(page)
                if fallback_range is not None:
                    start, end = fallback_range
                    videos = [
                        self._short_video_home_item(item, "douyin")
                        for item in session["items"][start:end]
                        if self._douyin_card_is_displayable(item)
                    ]
                    return [video for video in videos if video is not None], True
                raise RuntimeError(
                    f"抖音搜索正在进行请求冷却，请在 {max(1, int(blocked_until - now))} 秒后重试；已有结果不会丢失"
                )

            if page > 1 and page - 1 not in page_ranges:
                raise RuntimeError("请先加载抖音搜索的上一页，以便沿用平台搜索会话")
            # 页面边界按实际已返回条目记录。首批只有 17 条时，下一页从第 18 条
            # 续取，而不是按固定 56 条偏移跳过中间结果。
            page_start = page_ranges[page - 1][1] if page > 1 else 0
            raw_items: list[dict] = session["items"]
            seen_ids: set[str] = session["seen_ids"]
            search_id = str(session["search_id"] or "")
            cursor = int(session["cursor"] or 0)
            verify_blocked = False
            request_count = 0
            max_requests = 1

            for _ in range(max_requests):
                if len(raw_items) - page_start >= target_count:
                    break
                request_params = {
                    "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
                    "search_channel": "aweme_general", "keyword": keyword, "count": str(count),
                    "offset": str(cursor), "search_source": "normal_search", "query_correct_type": "1",
                    "is_filter_search": "0", "pc_client_type": "1", "version_code": "190500",
                    "version_name": "19.5.0", "cookie_enabled": "true", "screen_width": "1920",
                    "screen_height": "1080", "browser_language": "zh-CN", "browser_platform": "Win32",
                    "browser_name": browser_name, "browser_version": browser_version, "browser_online": "true",
                    "engine_name": engine_name, "engine_version": engine_version, "os_name": "Windows", "os_version": "10",
                    "platform": "PC", "from_group_id": "", "need_filter_settings": "1",
                    "list_type": "single", "cpu_core_num": "16", "device_memory": "8",
                    "downlink": "10", "effective_type": "4g", "round_trip_time": "50",
                }
                if search_id:
                    request_params["search_id"] = search_id
                payload = self._request_douyin_browse_json(
                    "https://www.douyin.com/aweme/v1/web/general/search/single/",
                    request_params,
                    referer,
                )
                request_count += 1
                nil_info = payload.get("search_nil_info") if isinstance(payload.get("search_nil_info"), dict) else {}
                verify_blocked = (
                    nil_info.get("search_nil_type") == "verify_check"
                    or nil_info.get("search_nil_item") == "verify_check"
                )
                if verify_blocked:
                    session["blocked_until"] = time.time() + _DOUYIN_VERIFY_COOLDOWN_SECONDS
                    session["has_more"] = True
                    logger.warning(
                        "douyin search verify_check query=%s page=%s offset=%s requests=%s retained=%s",
                        keyword,
                        page,
                        cursor,
                        request_count,
                        len(raw_items) - page_start,
                    )
                    break

                if not search_id:
                    search_id = self._short_video_search_id(payload)
                batch_items = [item for item in (payload.get("aweme_list") or []) if isinstance(item, dict)]
                for card in payload.get("data") or []:
                    if not isinstance(card, dict):
                        continue
                    aweme = card.get("aweme_info")
                    if isinstance(aweme, dict):
                        batch_items.append(aweme)
                    batch_items.extend(item for item in (card.get("aweme_list") or []) if isinstance(item, dict))
                added = 0
                for item in batch_items:
                    video_id = str(item.get("aweme_id") or "").strip()
                    if video_id and self._douyin_card_is_displayable(item) and video_id not in seen_ids:
                        seen_ids.add(video_id)
                        raw_items.append(item)
                        added += 1

                next_cursor = int(payload.get("cursor") or cursor + count)
                cursor_stalled = next_cursor == cursor
                if not cursor_stalled:
                    cursor = next_cursor
                # 有效批次偶尔错误返回 has_more=0；半批以上允许继续探测一次。
                # 空批、全重复批或 cursor 停滞则立即停止，避免无意义请求。
                likely_truncated = len(batch_items) >= max(8, count // 2)
                session["has_more"] = bool(payload.get("has_more")) or likely_truncated
                logger.info(
                    "douyin search batch query=%s page=%s offset=%s received=%s added=%s has_more=%s",
                    keyword,
                    page,
                    request_params["offset"],
                    len(batch_items),
                    added,
                    session["has_more"],
                )
                if not session["has_more"] or cursor_stalled or not added:
                    break

            page_end = min(page_start + target_count, len(raw_items))
            if page_end > page_start:
                page_ranges[page] = (page_start, page_end)
            session["updated_at"] = time.time()
            session["search_id"] = search_id
            session["cursor"] = cursor
            for raw_item in raw_items[page_start:page_end]:
                self._short_video_item_store("douyin", raw_item)

            if page_end == page_start:
                if previous_session is not None and previous_session is not session and page in previous_session["page_ranges"]:
                    old_start, old_end = previous_session["page_ranges"][page]
                    old_videos = [
                        self._short_video_home_item(item, "douyin")
                        for item in previous_session["items"][old_start:old_end]
                        if self._douyin_card_is_displayable(item)
                    ]
                    logger.warning("douyin search blocked; falling back to previous session query=%s page=%s", keyword, page)
                    return [video for video in old_videos if video is not None], True
                if verify_blocked:
                    raise RuntimeError("抖音搜索请求触发平台安全验证，已自动降低请求频率；请在 30 秒后重试")
                raise RuntimeError("抖音搜索接口返回空结果")

            if verify_blocked and previous_session is not None and previous_session is not session:
                old_range = previous_session["page_ranges"].get(page)
                if old_range is not None and old_range[1] - old_range[0] > page_end - page_start:
                    old_start, old_end = old_range
                    old_videos = [
                        self._short_video_home_item(item, "douyin")
                        for item in previous_session["items"][old_start:old_end]
                        if self._douyin_card_is_displayable(item)
                    ]
                    logger.warning("douyin search partial refresh discarded in favor of fuller cached page query=%s", keyword)
                    return [video for video in old_videos if video is not None], True

            sessions[session_key] = session
            sessions.move_to_end(session_key)
            while len(sessions) > _MAX_DOUYIN_SEARCH_SESSIONS:
                sessions.popitem(last=False)
            videos = [
                self._short_video_home_item(item, "douyin")
                for item in raw_items[page_start:page_end]
                if self._douyin_card_is_displayable(item)
            ]
            videos = [video for video in videos if video is not None]
            has_next = bool(len(raw_items) > page_end or session["has_more"] or verify_blocked)
            logger.info(
                "douyin search page completed query=%s page=%s count=%s requests=%s verify_blocked=%s has_next=%s",
                keyword,
                page,
                len(videos),
                request_count,
                verify_blocked,
                has_next,
            )
            return videos, has_next

    @staticmethod
    def _short_video_search_id(payload: dict) -> str:
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        log_pb = payload.get("log_pb") if isinstance(payload.get("log_pb"), dict) else {}
        return str(
            extra.get("search_request_id")
            or log_pb.get("impr_id")
            or extra.get("logid")
            or ""
        ).strip()

    def _search_tiktok_fallback(self, keyword: str, page: int, page_size: int) -> tuple[list[HomeVideo], bool]:
        normalized = str(keyword or "").strip().casefold()
        if not normalized:
            return [], False
        # TikTok's web search endpoint requires a rotating signature and may
        # return 403 even with a valid browser Cookie. Search the authenticated
        # recommendation pages as a bounded fallback, but inspect all metadata
        # fields retained by the API (description, hashtags and author) rather
        # than only the card title. This avoids silently missing obvious hits.
        candidates: list[HomeVideo] = []
        seen: set[str] = set()
        has_more = False
        for feed_page in range(1, 5):
            try:
                batch, feed_more = self._fetch_short_video_home("tiktok", feed_page, 56)
            except Exception as exc:
                logger.warning("TikTok fallback search feed page %s unavailable: %s", feed_page, exc)
                if not candidates:
                    raise RuntimeError(
                        "TikTok 搜索暂时无法获取结果。TikTok 当前搜索接口需要动态签名，"
                        "且本次请求被平台风控拦截。请确认已在 Firefox 登录 TikTok，"
                        "或稍后重试；首页推荐内容仍可正常播放。"
                    ) from exc
                break
            has_more = has_more or feed_more
            for video in batch:
                if video.video_id not in seen:
                    seen.add(video.video_id)
                    candidates.append(video)
        matched = [
            video for video in candidates
            if normalized in f"{video.title} {video.uploader}".casefold()
        ]
        if not matched:
            raise RuntimeError(
                "TikTok 搜索暂时无法获取结果。TikTok 当前搜索接口需要动态签名，"
                "且本次请求被平台风控拦截（HTTP 403）。请确认已在 Firefox 登录 TikTok，"
                "或稍后重试；首页推荐内容仍可正常播放。"
            )
        start = (max(1, int(page)) - 1) * max(1, int(page_size))
        end = start + max(1, int(page_size))
        return matched[start:end], has_more and len(matched) > end

    def _cache_key(self, mode: str, source: str, keyword: str, page: int, page_size: int) -> str:
        fingerprint = self._config_fingerprint(source)
        normalized = keyword.strip().lower()
        return f"{mode}|{source}|{normalized}|{int(page)}|{int(page_size)}|{fingerprint}"

    def _config_fingerprint(self, source: str = "") -> str:
        # 指纹要跟着实际请求的站点走：同一次会话里首页/搜索可以在两个站点间来回切，
        # 各自的 Cookie 文件不同，混用一个指纹会让缓存串站。
        source = self.normalize_source(source) or self.config.default_home_source()
        cookie_file = self.config.cookie_file(source)
        cookie_stamp = ""
        if cookie_file:
            path = Path(cookie_file)
            try:
                stat = path.stat()
                cookie_stamp = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
            except OSError:
                cookie_stamp = str(path)
        proxy_label, proxy_value = self.config.effective_proxy()
        payload = {
            "source": source,
            "cookie_browser": self.config.cookie_browser_for_site(source),
            "cookie_file": cookie_stamp,
            "proxy_label": proxy_label,
            "proxy_value": proxy_value,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _cache_lookup(self, key: str, ttl_seconds: float) -> tuple[list[HomeVideo], bool] | None:
        with self._page_cache_lock:
            cached = self._page_cache.get(key)
            if cached is None:
                return None
            cached_at, videos, has_next = cached
            if time.time() - cached_at > ttl_seconds:
                self._page_cache.pop(key, None)
                return None
            self._page_cache.move_to_end(key)
            return list(videos), has_next

    def _cache_store(self, key: str, result: tuple[list[HomeVideo], bool]) -> None:
        videos, has_next = result
        with self._page_cache_lock:
            self._page_cache[key] = (time.time(), list(videos), has_next)
            self._page_cache.move_to_end(key)
            while len(self._page_cache) > _MAX_PAGE_CACHE_ITEMS:
                self._page_cache.popitem(last=False)

    def _creator_cache_lookup(self, key: str) -> PlaylistInfo | None:
        with self._creator_cache_lock:
            cached = self._creator_cache.get(key)
            if cached is None:
                return None
            cached_at, playlist = cached
            if time.time() - cached_at > _CREATOR_CACHE_TTL_SECONDS:
                self._creator_cache.pop(key, None)
                return None
            self._creator_cache.move_to_end(key)
            return deepcopy(playlist)

    def _creator_cache_store(self, key: str, playlist: PlaylistInfo | None) -> None:
        with self._creator_cache_lock:
            self._creator_cache[key] = (time.time(), deepcopy(playlist))
            self._creator_cache.move_to_end(key)
            while len(self._creator_cache) > _MAX_CREATOR_CACHE_ITEMS:
                self._creator_cache.popitem(last=False)

    def _collection_cache_lookup(self, key: str) -> tuple[PlaylistInfo | None] | None:
        """命中返回单元素元组，未命中返回 None。

        合集的 None 是有意义的结果（不属于任何合集），所以不能像 creator 那样
        直接用 None 表示"没缓存"。
        """
        with self._collection_cache_lock:
            cached = self._collection_cache.get(key)
            if cached is None:
                return None
            cached_at, playlist = cached
            if time.time() - cached_at > _COLLECTION_CACHE_TTL_SECONDS:
                self._collection_cache.pop(key, None)
                return None
            self._collection_cache.move_to_end(key)
            return (deepcopy(playlist),)

    def _collection_cache_store(self, key: str, playlist: PlaylistInfo | None) -> None:
        with self._collection_cache_lock:
            self._collection_cache[key] = (time.time(), deepcopy(playlist))
            self._collection_cache.move_to_end(key)
            while len(self._collection_cache) > _MAX_COLLECTION_CACHE_ITEMS:
                self._collection_cache.popitem(last=False)


class BilibiliResolver:
    def __init__(self, config: ConfigService, ytdlp_resolver: YoutubeResolver) -> None:
        self.config = config
        self.ytdlp = ytdlp_resolver

    def detect_url_kind(self, url: str) -> str:
        raw = str(url or "").strip()
        if not _is_bilibili_url(raw):
            return "unknown"
        parsed = urllib.parse.urlparse(raw)
        path = parsed.path.lower()
        if "/lists/" in path and (urllib.parse.parse_qs(parsed.query).get("type") or [""])[0] in {"season", "series"}:
            return "playlist"
        if any(token in path for token in ("/watchlater", "/favlist", "/medialist/play/", "/list/ml")):
            return "playlist"
        if any(token in path for token in ("/favlist/", "/medialist/", "/list/", "/season/", "/collection-detail")):
            return "playlist"
        if "/bangumi/play/" in path or "/cheese/play/" in path:
            return "playlist"
        return "video"

    def resolve_playlist(self, url: str) -> PlaylistInfo:
        raw = str(url or "").strip()
        parsed = urllib.parse.urlparse(raw)
        path = parsed.path.lower()
        if "/lists/" in path:
            list_type = str((urllib.parse.parse_qs(parsed.query).get("type") or [""])[0]).strip().lower()
            if list_type == "season":
                playlist = self._resolve_space_season_playlist(raw)
                if playlist.entries:
                    return playlist
        if "/watchlater" in path:
            playlist = self._resolve_watch_later_playlist()
            if playlist.entries:
                return playlist
        if "/favlist" in path:
            playlist = self._resolve_favorite_playlist(raw)
            if playlist.entries:
                return playlist
        if "/medialist/play/" in path or "/list/ml" in path:
            playlist = self._resolve_favorite_playlist(raw)
            if playlist.entries:
                return playlist
        if "/video/" in path:
            playlist = self._resolve_video_pages_playlist(raw)
            if playlist.entries:
                return playlist
        if "/bangumi/play/ep" in path:
            season_url = self._season_url_from_episode(raw)
            if season_url:
                playlist = self._resolve_bangumi_season_playlist(season_url)
                if playlist.entries:
                    return playlist
        if "/bangumi/play/ss" in path:
            playlist = self._resolve_bangumi_season_playlist(raw)
            if playlist.entries:
                return playlist
        return self.ytdlp.resolve_playlist_generic(raw)

    def fetch_home_videos(self, page: int = 1, page_size: int = 56) -> tuple[list[HomeVideo], bool]:
        page = max(1, int(page))
        page_size = max(1, min(56, int(page_size)))
        start = (page - 1) * page_size
        end = start + page_size
        total_needed = end + 1
        all_videos: list[HomeVideo] = []
        batch_index = 0
        while len(all_videos) < total_needed and batch_index < 10:
            batch_size = min(_BILIBILI_HOME_PAGE_LIMIT, total_needed - len(all_videos))
            if batch_size <= 0:
                batch_size = _BILIBILI_HOME_PAGE_LIMIT
            payload = self._request_json(
                "https://api.bilibili.com/x/web-interface/index/top/feed/rcmd",
                params={
                    "ps": batch_size,
                    "fresh_type": 3,
                    "fresh_idx": batch_index,
                    "feed_version": "V8",
                },
                cookie_policy="prefer",
            )
            items = payload.get("data", {}).get("item", [])
            batch_videos = [
                video for item in items if isinstance(item, dict) and (video := _home_video_from_bilibili_item(item))
            ]
            all_videos.extend(batch_videos)
            if not items:
                break
            batch_index += 1

        videos = all_videos[start:end]
        has_next = len(all_videos) > end
        logger.info("bilibili home fetched page=%s page_size=%s count=%s", page, page_size, len(videos))
        return videos, has_next

    def fetch_creator_videos(
        self,
        video: VideoInfo,
        limit: int = 50,
    ) -> tuple[str, list[PlaylistEntry]]:
        mid = str(video.creator_id or video.channel_id or "").strip()
        creator_url = str(video.creator_url or "").strip().rstrip("/")
        if not creator_url and mid:
            creator_url = f"https://space.bilibili.com/{mid}"
        if not mid:
            raise RuntimeError("当前 Bilibili 视频缺少制作者 MID")

        limit = max(1, min(50, int(limit)))
        primary_error = ""
        cookie_header = self._preferred_cookie_header(creator_url or "https://www.bilibili.com/")
        try:
            params: dict[str, object] = {
                "mid": mid,
                "pn": 1,
                "ps": limit,
                "order": "pubdate",
                "order_avoided": "true",
                "platform": "web",
                "web_location": 1550101,
            }
            signed = self._sign_wbi_params(params, cookie_header=cookie_header)
            payload = self._request_json(
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params=signed,
                cookie_header=cookie_header,
                cookie_policy="none",
            )
            archives = (((payload.get("data") or {}).get("list") or {}).get("vlist") or [])
            playlist_id = f"bilibili:creator:{mid}"
            entries = [
                entry
                for index, item in enumerate(archives, start=1)
                if isinstance(item, dict)
                and (entry := _creator_entry_from_bilibili_archive(item, playlist_id, index)) is not None
            ]
            if entries:
                logger.info("bilibili creator API fetched mid=%s count=%s", mid, len(entries))
                return creator_url, entries
            primary_error = "Bilibili 制作者投稿接口没有返回可用视频"
        except Exception as exc:  # noqa: BLE001
            primary_error = str(exc)
            logger.warning("bilibili creator API failed mid=%s: %s", mid, exc)

        try:
            return self.ytdlp.fetch_creator_videos(video, limit)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Bilibili 作者视频获取失败：{primary_error}；yt-dlp 兜底失败：{exc}") from exc

    def _resolve_video_pages_playlist(self, url: str) -> PlaylistInfo:
        bvid = _extract_bvid(url)
        aid = _extract_aid(url)
        if not bvid and not aid:
            return PlaylistInfo(playlist_id="", title="", webpage_url=url, source_site="bilibili", entries=[])
        params = {"bvid": bvid} if bvid else {"aid": aid}
        payload = self._request_json(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
            cookie_policy="prefer",
        )
        data = payload.get("data", {})
        return self._pages_playlist_from_view(data, url, bvid=bvid, aid=aid)

    def _pages_playlist_from_view(self, data: dict, url: str, *, bvid: str, aid: str) -> PlaylistInfo:
        """把 view 接口的 data.pages 转成分 P 列表。合集探测会复用同一份 data，不再重复请求。"""
        pages = data.get("pages") or []
        video_url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
        entries = []
        uploader = str((data.get("owner") or {}).get("name") or "").strip()
        thumbnail = _normalize_bilibili_thumbnail(str(data.get("pic") or ""))
        for item in pages:
            if not isinstance(item, dict):
                continue
            page_no = int(item.get("page") or 0)
            if page_no <= 0:
                continue
            part = str(item.get("part") or "").strip() or f"P{page_no}"
            entry_url = f"{video_url}?p={page_no}"
            entries.append(
                {
                    "playlist_id": bvid or f"av{aid}",
                    "video_id": _bilibili_video_key(entry_url, bvid=bvid, aid=aid),
                    "title": part,
                    "webpage_url": entry_url,
                    "source_site": "bilibili",
                    "uploader": uploader,
                    "duration": int(item.get("duration") or 0),
                    "thumbnail": thumbnail,
                    "position": page_no,
                    "availability": "",
                }
            )
        if not entries:
            return PlaylistInfo(playlist_id="", title="", webpage_url=url, source_site="bilibili", entries=[])
        current_page = int((urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("p") or ["1"])[0] or 1)
        current_video_id = _bilibili_video_key(f"{video_url}?p={current_page}", bvid=bvid, aid=aid)
        return PlaylistInfo(
            playlist_id=bvid or f"av{aid}",
            title=str(data.get("title") or "").strip() or (bvid or aid or "Bilibili Playlist"),
            webpage_url=video_url,
            source_site="bilibili",
            uploader=uploader,
            thumbnail=thumbnail,
            entry_count=len(entries),
            source_type="playlist",
            current_video_id=current_video_id,
            entries=[
                _playlist_entry_from_dict(item)
                for item in entries
            ],
        )

    def resolve_collection(self, video: VideoInfo) -> PlaylistInfo | None:
        """当前视频所属的"合集"，按 UGC 合集 → 多 P → 番剧季的优先级探测。

        单 P 且无合集的普通稿件返回 None——这是常态，调用方只写 debug 日志，不打扰用户。
        """
        url = str(video.webpage_url or "").strip()
        if not url:
            return None
        if "/bangumi/play/" in urllib.parse.urlparse(url).path:
            playlist = self._resolve_bangumi_season_playlist(url)
            if not playlist.entries:
                return None
            playlist.playlist_id = f"bilibili:bangumi:{playlist.playlist_id}"
            playlist.source_type = "collection"
            for entry in playlist.entries:
                entry.playlist_id = playlist.playlist_id
            return playlist

        bvid = _extract_bvid(url)
        aid = _extract_aid(url)
        if not bvid and not aid:
            return None
        params = {"bvid": bvid} if bvid else {"aid": aid}
        payload = self._request_json(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
            cookie_policy="prefer",
        )
        data = payload.get("data") or {}

        season = data.get("ugc_season")
        if isinstance(season, dict) and season:
            playlist = self._collection_from_ugc_season(
                season,
                url,
                bvid=bvid,
                aid=aid,
                uploader=str((data.get("owner") or {}).get("name") or "").strip(),
            )
            if playlist is not None:
                return playlist

        # 无合集但多 P：用户在多 P 稿件里期待左侧能看到同稿件的其他分 P。
        if len(data.get("pages") or []) > 1:
            playlist = self._pages_playlist_from_view(data, url, bvid=bvid, aid=aid)
            if playlist.entries:
                playlist.playlist_id = f"bilibili:pages:{bvid or f'av{aid}'}"
                playlist.source_type = "collection"
                for entry in playlist.entries:
                    entry.playlist_id = playlist.playlist_id
                return playlist
        return None

    def _collection_from_ugc_season(
        self,
        season: dict,
        url: str,
        *,
        bvid: str,
        aid: str,
        uploader: str = "",
    ) -> PlaylistInfo | None:
        """保留 ugc_season 的章节层级，同时维护兼容用的扁平 entries。"""
        season_id = str(season.get("id") or "").strip()
        playlist_id = f"bilibili:ugcseason:{season_id or bvid or aid}"
        season_mid = str(season.get("mid") or "").strip()
        # 合集地址要稳定（不能用当前这一集的 URL），否则保存后换集就认不出是同一个合集。
        # 这个 space 合集页地址本身也能被 resolve_playlist 解析回来。
        season_url = (
            f"https://space.bilibili.com/{season_mid}/channel/collectiondetail?sid={season_id}"
            if season_mid and season_id
            else url
        )
        current_key = _bilibili_video_key(url, bvid=bvid, aid=aid)
        current_base_key = _collection_base_key(current_key)
        entries: list[PlaylistEntry] = []
        source_sections: list[PlaylistSection] = []
        album_sections: list[PlaylistSection] = []
        has_multi_page_album = False
        album_position = 0
        for section_index, section in enumerate(season.get("sections") or [], start=1):
            if not isinstance(section, dict):
                continue
            source_section_id = str(section.get("id") or section_index).strip()
            source_section_title = _strip_html(str(section.get("title") or "").strip())
            source_section_thumbnail = _normalize_bilibili_thumbnail(
                str(section.get("cover") or section.get("thumbnail") or "")
            )
            source_section_entries: list[PlaylistEntry] = []
            for episode in section.get("episodes") or []:
                if not isinstance(episode, dict):
                    continue
                episode_bvid = str(episode.get("bvid") or "").strip()
                episode_aid = str(episode.get("aid") or "").strip()
                if not episode_bvid and not episode_aid:
                    continue
                arc = episode.get("arc") if isinstance(episode.get("arc"), dict) else {}
                base_url = (
                    f"https://www.bilibili.com/video/{episode_bvid}"
                    if episode_bvid
                    else f"https://www.bilibili.com/video/av{episode_aid}"
                )
                album_title = _strip_html(
                    str(episode.get("title") or arc.get("title") or "").strip() or episode_bvid or episode_aid
                )
                album_thumbnail = _normalize_bilibili_thumbnail(str(arc.get("pic") or ""))
                raw_pages = episode.get("pages") if isinstance(episode.get("pages"), list) else []
                pages = [page for page in raw_pages if isinstance(page, dict)]
                if not pages:
                    page = episode.get("page") if isinstance(episode.get("page"), dict) else {}
                    pages = [page]
                multi_page = len(pages) > 1
                has_multi_page_album = has_multi_page_album or multi_page
                album_position += 1
                album_id = str(episode.get("id") or episode_bvid or episode_aid or album_position).strip()
                album_entries: list[PlaylistEntry] = []
                for page_index, page in enumerate(pages, start=1):
                    page_no = int(page.get("page") or page_index)
                    entry_url = f"{base_url}?p={page_no}" if multi_page or page_no > 1 else base_url
                    video_id = _bilibili_video_key(entry_url, bvid=episode_bvid, aid=episode_aid)
                    page_title = _strip_html(str(page.get("part") or "").strip())
                    entry = PlaylistEntry(
                        playlist_id=playlist_id,
                        video_id=video_id,
                        title=page_title if multi_page and page_title else album_title,
                        webpage_url=entry_url,
                        source_site="bilibili",
                        uploader=uploader,
                        duration=int(page.get("duration") or arc.get("duration") or 0),
                        thumbnail=album_thumbnail,
                        position=len(entries) + 1,
                        availability="",
                        section_id=source_section_id,
                        section_title=source_section_title,
                        section_position=section_index,
                        section_thumbnail=source_section_thumbnail,
                    )
                    entries.append(entry)
                    source_section_entries.append(entry)
                    album_entries.append(entry)
                if album_entries:
                    album_sections.append(
                        PlaylistSection(
                            section_id=album_id,
                            title=album_title,
                            position=album_position,
                            thumbnail=album_thumbnail,
                            entries=album_entries,
                        )
                    )
            if source_section_entries:
                source_sections.append(
                    PlaylistSection(
                        section_id=source_section_id,
                        title=source_section_title or f"专辑 {section_index}",
                        position=section_index,
                        thumbnail=source_section_thumbnail or source_section_entries[0].thumbnail,
                        entries=source_section_entries,
                    )
                )
        if not entries:
            return None
        if has_multi_page_album:
            sections = album_sections
            for album in sections:
                for entry in album.entries:
                    entry.section_id = album.section_id
                    entry.section_title = album.title
                    entry.section_position = album.position
                    entry.section_thumbnail = album.thumbnail
        else:
            sections = source_sections
            raw_sections = season.get("sections") or []
            # 单个无标题 section 对用户没有额外信息，继续显示原来的扁平列表。
            if len(sections) == 1 and len(raw_sections) == 1 and not str(raw_sections[0].get("title") or "").strip():
                sections = []
        current_entry = next((entry for entry in entries if entry.video_id == current_key), None)
        if current_entry is None:
            current_entry = next(
                (entry for entry in entries if _collection_base_key(entry.video_id) == current_base_key),
                entries[0],
            )
        return PlaylistInfo(
            playlist_id=playlist_id,
            title=_strip_html(str(season.get("title") or "").strip()) or f"合集 {season_id}",
            webpage_url=season_url,
            source_site="bilibili",
            uploader=uploader,
            thumbnail=_normalize_bilibili_thumbnail(str(season.get("cover") or "")),
            entry_count=len(entries),
            source_type="collection",
            current_video_id=current_entry.video_id,
            current_section_id=current_entry.section_id,
            entries=entries,
            sections=sections,
        )

    def _season_url_from_episode(self, url: str) -> str:
        try:
            info = self.ytdlp.resolve(url)
        except Exception:
            return ""
        raw = info.raw_info or {}
        season_id = str(raw.get("season_id") or "").strip()
        if not season_id:
            return ""
        return f"https://www.bilibili.com/bangumi/play/ss{season_id}"

    def _resolve_bangumi_season_playlist(self, url: str) -> PlaylistInfo:
        season_id = _extract_season_id(url)
        if not season_id:
            return self.ytdlp.resolve_playlist_generic(url)
        payload = self._request_json(
            "https://api.bilibili.com/pgc/view/web/season",
            params={"season_id": season_id},
            cookie_policy="prefer",
        )
        result = payload.get("result") or {}
        episodes = result.get("episodes") or []
        entries: list[PlaylistEntry] = []
        current_video_id = ""
        current_ep = _extract_episode_id(url)
        for index, episode in enumerate(episodes, start=1):
            if not isinstance(episode, dict):
                continue
            ep_id = str(episode.get("id") or "").strip()
            share_url = _normalize_bilibili_url(str(episode.get("share_url") or ""))
            if not share_url and ep_id:
                share_url = f"https://www.bilibili.com/bangumi/play/ep{ep_id}"
            if not share_url:
                continue
            title = str(episode.get("title") or "").strip()
            long_title = str(episode.get("long_title") or "").strip()
            display_title = " ".join(part for part in [title, long_title] if part).strip() or ep_id
            entry = PlaylistEntry(
                playlist_id=f"ss{season_id}",
                video_id=_bilibili_video_key(share_url, aid=ep_id),
                title=display_title,
                webpage_url=share_url,
                source_site="bilibili",
                uploader=str((result.get("up_info") or {}).get("uname") or "").strip(),
                duration=_parse_duration_to_seconds(episode.get("duration") or 0),
                thumbnail=_normalize_bilibili_thumbnail(str(episode.get("cover") or result.get("cover") or "")),
                position=index,
                availability="",
            )
            entries.append(entry)
            if ep_id and ep_id == current_ep:
                current_video_id = entry.video_id
        if entries and not current_video_id:
            current_video_id = entries[0].video_id
        return PlaylistInfo(
            playlist_id=f"ss{season_id}",
            title=str(result.get("title") or "").strip() or f"ss{season_id}",
            webpage_url=f"https://www.bilibili.com/bangumi/play/ss{season_id}",
            source_site="bilibili",
            uploader=str((result.get("up_info") or {}).get("uname") or "").strip(),
            thumbnail=_normalize_bilibili_thumbnail(str(result.get("cover") or "")),
            entry_count=len(entries),
            source_type="album",
            current_video_id=current_video_id,
            entries=entries,
        )

    def _resolve_favorite_playlist(self, url: str) -> PlaylistInfo:
        parsed = urllib.parse.urlparse(url)
        media_id = _extract_media_id(url)
        if not media_id:
            fid = str((urllib.parse.parse_qs(parsed.query).get("fid") or [""])[0]).strip()
            up_mid = _extract_space_mid(url)
            if fid and up_mid:
                media_id = self._media_id_from_fid(up_mid, fid)
        if not media_id:
            return PlaylistInfo(playlist_id="", title="", webpage_url=url, source_site="bilibili", entries=[])

        page = 1
        page_size = 40
        entries: list[PlaylistEntry] = []
        info_data: dict = {}
        expected_count = 0
        while True:
            payload = self._request_json(
                "https://api.bilibili.com/x/v3/fav/resource/list",
                params={
                    "media_id": media_id,
                    "pn": page,
                    "ps": page_size,
                    "keyword": "",
                    "order": "mtime",
                    "type": 0,
                    "tid": 0,
                    "platform": "web",
                },
                cookie_policy="prefer",
            )
            data = payload.get("data") or {}
            info_data = data.get("info") or info_data
            expected_count = int((info_data or {}).get("media_count") or expected_count or 0)
            medias = data.get("medias") or []
            if not medias:
                break
            for index, media in enumerate(medias, start=len(entries) + 1):
                if not isinstance(media, dict):
                    continue
                entry = _favorite_entry_from_media(media, media_id=str(media_id), position=index)
                if entry:
                    entries.append(entry)
            if expected_count and len(entries) >= expected_count:
                break
            page += 1
            if page > 100:
                break

        if not entries:
            return PlaylistInfo(playlist_id=str(media_id), title="", webpage_url=url, source_site="bilibili", entries=[])

        return PlaylistInfo(
            playlist_id=str(media_id),
            title=str(info_data.get("title") or f"收藏夹 {media_id}"),
            webpage_url=url,
            source_site="bilibili",
            uploader=str(((info_data.get("upper") or {}).get("name")) or "").strip(),
            thumbnail=_normalize_bilibili_thumbnail(str(info_data.get("cover") or entries[0].thumbnail or "")),
            entry_count=len(entries),
            source_type="playlist",
            current_video_id=entries[0].video_id,
            entries=entries,
        )

    def _resolve_watch_later_playlist(self) -> PlaylistInfo:
        payload = self._request_json(
            "https://api.bilibili.com/x/v2/history/toview",
            cookie_policy="prefer",
        )
        data = payload.get("data") or {}
        items = data.get("list") or []
        entries: list[PlaylistEntry] = []
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            entry = _watch_later_entry_from_item(item, position=index)
            if entry:
                entries.append(entry)
        return PlaylistInfo(
            playlist_id="watchlater",
            title="稍后再看",
            webpage_url="https://www.bilibili.com/watchlater/",
            source_site="bilibili",
            uploader="",
            thumbnail=entries[0].thumbnail if entries else "",
            entry_count=len(entries),
            source_type="playlist",
            current_video_id=entries[0].video_id if entries else "",
            entries=entries,
        )

    def _media_id_from_fid(self, up_mid: str, fid: str) -> str:
        payload = self._request_json(
            "https://api.bilibili.com/x/v3/fav/folder/created/list-all",
            params={"up_mid": up_mid},
            cookie_policy="prefer",
        )
        folders = (payload.get("data") or {}).get("list") or []
        for folder in folders:
            if not isinstance(folder, dict):
                continue
            if str(folder.get("id") or "") == fid:
                return str(folder.get("id") or "")
            if str(folder.get("fid") or "") == fid:
                return str(folder.get("id") or "")
        return ""

    def _resolve_space_season_playlist(self, url: str) -> PlaylistInfo:
        mid = _extract_space_mid(url)
        season_id = _extract_space_list_id(url)
        if not mid or not season_id:
            return PlaylistInfo(playlist_id="", title="", webpage_url=url, source_site="bilibili", entries=[])

        payload = self._request_json(
            "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list",
            params={
                "mid": mid,
                "season_id": season_id,
                "page_num": 1,
                "page_size": 100,
            },
            cookie_policy="prefer",
        )
        data = payload.get("data") or {}
        archives = data.get("archives") or []
        meta = self._resolve_space_season_meta(mid, season_id)
        entries: list[PlaylistEntry] = []
        for index, archive in enumerate(archives, start=1):
            if not isinstance(archive, dict):
                continue
            bvid = str(archive.get("bvid") or "").strip()
            aid = str(archive.get("aid") or "").strip()
            if not bvid and not aid:
                continue
            url_value = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
            entries.append(
                PlaylistEntry(
                    playlist_id=season_id,
                    video_id=_bilibili_video_key(url_value, bvid=bvid, aid=aid),
                    title=_strip_html(str(archive.get("title") or "").strip() or bvid or aid),
                    webpage_url=url_value,
                    source_site="bilibili",
                    uploader=str(mid),
                    duration=int(archive.get("duration") or 0),
                    thumbnail=_normalize_bilibili_thumbnail(str(archive.get("pic") or "")),
                    position=index,
                    availability="",
                )
            )

        playlist_title = str(meta.get("name") or meta.get("title") or f"合集 {season_id}")
        playlist_cover = _normalize_bilibili_thumbnail(str(meta.get("cover") or (entries[0].thumbnail if entries else "")))
        playlist_uploader = str(meta.get("mid") or mid)
        return PlaylistInfo(
            playlist_id=season_id,
            title=playlist_title,
            webpage_url=url,
            source_site="bilibili",
            uploader=playlist_uploader,
            thumbnail=playlist_cover,
            entry_count=len(entries),
            source_type="playlist",
            current_video_id=entries[0].video_id if entries else "",
            entries=entries,
        )

    def _resolve_space_season_meta(self, mid: str, season_id: str) -> dict:
        payload = self._request_json(
            "https://api.bilibili.com/x/polymer/web-space/seasons_series_list",
            params={
                "mid": mid,
                "page_num": 1,
                "page_size": 20,
            },
            cookie_policy="prefer",
        )
        seasons = (((payload.get("data") or {}).get("items_lists") or {}).get("seasons_list") or [])
        for season in seasons:
            if not isinstance(season, dict):
                continue
            meta = season.get("meta") or {}
            if str(meta.get("season_id") or "") == str(season_id):
                return meta
        return {}

    def search_videos(self, keyword: str, page: int = 1, page_size: int = 56) -> tuple[list[HomeVideo], bool]:
        query = str(keyword or "").strip()
        if not query:
            return [], False

        page = max(1, int(page))
        page_size = max(1, min(56, int(page_size)))
        last_error = ""

        cookie_header = self._preferred_cookie_header("https://www.bilibili.com/")
        if cookie_header:
            try:
                return self._search_api_paged(
                    "https://api.bilibili.com/x/web-interface/search/type",
                    query=query,
                    page=page,
                    page_size=page_size,
                    cookie_header=cookie_header,
                    use_wbi=False,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.warning("bilibili search candidate A failed: %s", exc)

        try:
            return self._search_api_paged(
                "https://api.bilibili.com/x/web-interface/wbi/search/type",
                query=query,
                page=page,
                page_size=page_size,
                cookie_header=cookie_header,
                use_wbi=True,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("bilibili search candidate B failed: %s", exc)

        videos = self._search_html_fallback(query, page, page_size, cookie_header=cookie_header)
        if videos:
            return videos, len(videos) >= page_size

        raise RuntimeError(last_error or "Bilibili 搜索失败，公开接口、签名接口和网页兜底均不可用。")

    def _search_api_paged(
        self,
        url: str,
        *,
        query: str,
        page: int,
        page_size: int,
        cookie_header: str,
        use_wbi: bool,
    ) -> tuple[list[HomeVideo], bool]:
        start = (page - 1) * page_size
        end = start + page_size
        first_api_page = start // _BILIBILI_SEARCH_PAGE_LIMIT + 1
        first_offset = start % _BILIBILI_SEARCH_PAGE_LIMIT
        needed = first_offset + page_size + 1
        api_page_count = max(1, (needed + _BILIBILI_SEARCH_PAGE_LIMIT - 1) // _BILIBILI_SEARCH_PAGE_LIMIT)

        collected: list[HomeVideo] = []
        expected_total = 0
        for offset_page in range(api_page_count):
            api_page = first_api_page + offset_page
            params: dict[str, object] = {
                "search_type": "video",
                "keyword": query,
                "page": api_page,
                "page_size": _BILIBILI_SEARCH_PAGE_LIMIT,
                "order": "totalrank",
            }
            request_params = self._sign_wbi_params(params, cookie_header=cookie_header) if use_wbi else params
            payload = self._request_json(
                url,
                params=request_params,
                cookie_header=cookie_header,
                cookie_policy="none",
            )
            data = payload.get("data", {})
            expected_total = max(expected_total, int(data.get("numResults") or 0))
            result = data.get("result", [])
            batch = [video for item in result if isinstance(item, dict) and (video := _home_video_from_search_item(item))]
            collected.extend(batch)
            if len(batch) < _BILIBILI_SEARCH_PAGE_LIMIT:
                break

        sliced = collected[first_offset:first_offset + page_size]
        has_next = len(collected) > first_offset + page_size
        if expected_total:
            has_next = has_next or expected_total > end
        return sliced, has_next

    def _search_html_fallback(
        self,
        keyword: str,
        page: int,
        page_size: int,
        *,
        cookie_header: str,
    ) -> list[HomeVideo]:
        html_text = self._request_text(
            "https://search.bilibili.com/video",
            params={"keyword": keyword, "page": page},
            cookie_header=cookie_header,
            cookie_policy="none",
        )
        if "验证码" in html_text or "risk-captcha" in html_text:
            raise RuntimeError("Bilibili 搜索页面触发风控验证码。")

        matches = re.finditer(
            r'href="(?P<url>//www\.bilibili\.com/video/[^"]+)"[^>]*title="(?P<title>[^"]+)"',
            html_text,
            flags=re.IGNORECASE,
        )
        videos: list[HomeVideo] = []
        seen: set[str] = set()
        for match in matches:
            url = "https:" + match.group("url")
            title = _strip_html(match.group("title"))
            video_id = _bilibili_video_key(url)
            if not title or video_id in seen:
                continue
            seen.add(video_id)
            videos.append(
                HomeVideo(
                    video_id=video_id,
                    title=title,
                    webpage_url=url,
                    source_site="bilibili",
                )
            )
            if len(videos) >= page_size:
                break
        return videos

    def _request_json(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        cookie_header: str = "",
        cookie_policy: str = "prefer",
    ) -> dict:
        text = self._request_text(url, params=params, cookie_header=cookie_header, cookie_policy=cookie_policy)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Bilibili 返回了非 JSON 内容: {text[:200]}") from exc
        code = int(payload.get("code", 0) or 0)
        if code != 0:
            raise RuntimeError(f"Bilibili 接口返回错误 code={code}, message={payload.get('message')}")
        return payload

    def _request_text(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        cookie_header: str = "",
        cookie_policy: str = "prefer",
    ) -> str:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        full_url = f"{url}?{query}" if query else url
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        final_cookie = cookie_header
        if cookie_policy == "prefer" and not final_cookie:
            final_cookie = self._preferred_cookie_header(full_url)
        if final_cookie:
            headers["Cookie"] = final_cookie
        req = urllib.request.Request(full_url, headers=headers)
        # 必须走 build_opener 而不是全局 urlopen：全局 opener 不带 ProxyHandler，
        # 用户配置的代理会被静默忽略，B 站接口在需要代理的网络下必然失败。
        opener = self._build_opener()
        with opener.open(req, timeout=25) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """按调用构建 opener：OpenerDirector 非线程安全，不能在多个 worker 间复用。"""
        handlers: list[urllib.request.BaseHandler] = []
        _source, proxy = self.config.effective_proxy()
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            # 显式给出空代理映射，避免 urllib 回退去读环境变量里的 http_proxy。
            handlers.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*handlers)

    def _preferred_cookie_header(self, url: str) -> str:
        browser_cookie = self._browser_cookie_header(url)
        if browser_cookie:
            return browser_cookie
        cookie_file = self.config.cookie_file_for_url(url)
        if cookie_file:
            return load_cookie_header(cookie_file, url)
        return ""

    def _browser_cookie_header(self, url: str) -> str:
        tried: set[str] = set()

        site = self.config.cookie_site_for_url(url)
        explicit = self.config.explicit_cookie_browser_for_site(site)
        if explicit:
            tried.add(explicit)
            header = load_browser_cookie_header(explicit, url)
            if header:
                return header

        auto = self.config.auto_cookie_browser_for_site(site)
        if auto:
            tried.add(auto)
            header = load_browser_cookie_header(auto, url)
            if header:
                return header

        # Firefox 优先：load_browser_cookie_header 目前只读得了 Firefox，排前面能少走几轮空转。
        for _label, browser_spec in rank_cookie_sources(detect_browser_cookie_sources()):
            if browser_spec in tried:
                continue
            header = load_browser_cookie_header(browser_spec, url)
            if header:
                return header
        return ""

    def _sign_wbi_params(self, params: dict[str, object], cookie_header: str = "") -> dict[str, object]:
        nav = self._request_json(
            "https://api.bilibili.com/x/web-interface/nav",
            cookie_header=cookie_header,
            cookie_policy="none",
        )
        wbi = nav.get("data", {}).get("wbi_img", {})
        img_key = _basename_without_ext(str(wbi.get("img_url") or ""))
        sub_key = _basename_without_ext(str(wbi.get("sub_url") or ""))
        mixin_key = "".join((img_key + sub_key)[index] for index in _WBI_MIXIN_KEY if index < len(img_key + sub_key))[:32]

        signed = {key: value for key, value in params.items()}
        signed["wts"] = int(time.time())
        filtered = {
            key: str(value).translate(_INVALID_WBI_CHARS)
            for key, value in sorted(signed.items(), key=lambda item: item[0])
        }
        query = urllib.parse.urlencode(filtered)
        filtered["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
        return filtered


def _parse_bilibili_search_result(payload: dict) -> tuple[list[HomeVideo], bool]:
    data = payload.get("data", {})
    result = data.get("result", [])
    videos = [video for item in result if isinstance(item, dict) and (video := _home_video_from_search_item(item))]
    page = int(data.get("page") or 1)
    page_size = int(data.get("pagesize") or len(videos) or 1)
    num_pages = int(data.get("numPages") or page)
    has_next = page < num_pages or len(videos) >= page_size
    return videos, has_next


def _creator_entry_key(source_site: str, video_id: str, webpage_url: str) -> str:
    if source_site == "bilibili":
        bvid = _extract_bvid(video_id) or _extract_bvid(webpage_url)
        if bvid:
            return f"bilibili:{bvid.lower()}"
        aid = _extract_aid(webpage_url)
        if not aid:
            match = re.search(r"(?:bilibili:)?av(\d+)", str(video_id or ""), flags=re.IGNORECASE)
            aid = match.group(1) if match else ""
        if aid:
            return f"bilibili:av{aid}"
    normalized_id = str(video_id or "").strip()
    if normalized_id:
        return f"{source_site}:{normalized_id}"
    return str(webpage_url or "").strip().lower()


def _creator_entry_from_bilibili_archive(
    item: dict,
    playlist_id: str,
    position: int,
) -> PlaylistEntry | None:
    bvid = str(item.get("bvid") or "").strip()
    aid = str(item.get("aid") or "").strip()
    if not bvid and not aid:
        return None
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"
    title = _strip_html(str(item.get("title") or "").strip())
    if not title:
        return None
    return PlaylistEntry(
        playlist_id=playlist_id,
        video_id=_bilibili_video_key(url, bvid=bvid, aid=aid),
        title=title,
        webpage_url=url,
        source_site="bilibili",
        uploader=_strip_html(str(item.get("author") or "").strip()),
        duration=_parse_duration_to_seconds(item.get("length") or item.get("duration") or 0),
        thumbnail=_normalize_bilibili_thumbnail(str(item.get("pic") or "")),
        position=position,
        availability="",
        upload_date=_bilibili_upload_date(item),
    )


def _playlist_entry_from_dict(item: dict) -> PlaylistEntry:
    return PlaylistEntry(
        playlist_id=str(item.get("playlist_id") or ""),
        video_id=str(item.get("video_id") or ""),
        title=str(item.get("title") or ""),
        webpage_url=str(item.get("webpage_url") or ""),
        source_site=str(item.get("source_site") or "bilibili"),
        uploader=str(item.get("uploader") or ""),
        duration=int(item.get("duration") or 0),
        thumbnail=str(item.get("thumbnail") or ""),
        position=int(item.get("position") or 0),
        availability=str(item.get("availability") or ""),
        upload_date=str(item.get("upload_date") or ""),
    )


def _favorite_entry_from_media(media: dict, media_id: str, position: int) -> PlaylistEntry | None:
    bvid = str(media.get("bvid") or media.get("bv_id") or "").strip()
    if not bvid:
        return None
    page = int(media.get("page") or 1)
    url = f"https://www.bilibili.com/video/{bvid}"
    if page > 1:
        url += f"?p={page}"
    return PlaylistEntry(
        playlist_id=media_id,
        video_id=_bilibili_video_key(url, bvid=bvid),
        title=_strip_html(str(media.get("title") or "").strip() or bvid),
        webpage_url=url,
        source_site="bilibili",
        uploader=str(((media.get("upper") or {}).get("name")) or "").strip(),
        duration=int(media.get("duration") or 0),
        thumbnail=_normalize_bilibili_thumbnail(str(media.get("cover") or "")),
        position=position,
        availability="",
        upload_date=_bilibili_upload_date(media),
    )


def _watch_later_entry_from_item(item: dict, position: int) -> PlaylistEntry | None:
    bvid = str(item.get("bvid") or "").strip()
    aid = str(item.get("aid") or "").strip()
    page_info = item.get("page") or {}
    page = int(page_info.get("page") or 1)
    url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    if not url and aid:
        url = f"https://www.bilibili.com/video/av{aid}"
    if not url:
        return None
    if page > 1:
        url += f"?p={page}"
    title = _strip_html(str(item.get("title") or "").strip() or bvid or aid)
    part = _strip_html(str(page_info.get("part") or "").strip())
    if part and part != title:
        title = f"{title} - {part}"
    return PlaylistEntry(
        playlist_id="watchlater",
        video_id=_bilibili_video_key(url, bvid=bvid, aid=aid),
        title=title,
        webpage_url=url,
        source_site="bilibili",
        uploader=str(((item.get("owner") or {}).get("name")) or "").strip(),
        duration=int(item.get("duration") or page_info.get("duration") or 0),
        thumbnail=_normalize_bilibili_thumbnail(str(item.get("pic") or "")),
        position=position,
        availability="",
        upload_date=_bilibili_upload_date(item),
    )


def _home_video_from_bilibili_item(item: dict) -> HomeVideo | None:
    bvid = str(item.get("bvid") or "").strip()
    url = _normalize_bilibili_url(str(item.get("uri") or "")) or (
        f"https://www.bilibili.com/video/{bvid}" if bvid else ""
    )
    if not url:
        return None
    return HomeVideo(
        video_id=_bilibili_video_key(url, bvid=bvid, aid=str(item.get("id") or "")),
        title=_strip_html(str(item.get("title") or "").strip()),
        webpage_url=url,
        source_site="bilibili",
        uploader=str((item.get("owner") or {}).get("name") or "").strip(),
        duration=int(item.get("duration") or 0),
        thumbnail=_normalize_thumbnail(str(item.get("pic") or "")),
        upload_date=_bilibili_upload_date(item),
    )


def _home_video_from_search_item(item: dict) -> HomeVideo | None:
    url = _normalize_bilibili_url(str(item.get("arcurl") or ""))
    bvid = str(item.get("bvid") or "").strip()
    if not url and bvid:
        url = f"https://www.bilibili.com/video/{bvid}"
    if not url:
        return None
    return HomeVideo(
        video_id=_bilibili_video_key(url, bvid=bvid, aid=str(item.get("aid") or "")),
        title=_strip_html(str(item.get("title") or "").strip()),
        webpage_url=url,
        source_site="bilibili",
        uploader=_strip_html(str(item.get("author") or "").strip()),
        duration=_parse_duration_to_seconds(item.get("duration") or 0),
        thumbnail=_normalize_bilibili_thumbnail(str(item.get("pic") or "")),
        upload_date=_bilibili_upload_date(item),
    )


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def _bilibili_upload_date(item: dict) -> str:
    """从 B 站条目里取发布时间并换算成 YYYYMMDD。

    不同接口字段名不一：rcmd/搜索用 pubdate，历史用 senddate，收藏用 ctime/pubtime。
    值是 unix 秒；取不到或非法时返回空串（列表里就留空）。
    """
    for key in ("pubdate", "senddate", "ctime", "pubtime", "created"):
        raw = item.get(key)
        if raw in (None, "", 0):
            continue
        try:
            timestamp = int(raw)
        except (TypeError, ValueError):
            continue
        if timestamp <= 0:
            continue
        try:
            return _dt.datetime.fromtimestamp(timestamp).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            continue
    return ""


def _normalize_bilibili_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://"):
        return "https://" + raw[len("http://") :]
    if raw.startswith("https://"):
        return raw
    if raw.startswith(("www.bilibili.com", "m.bilibili.com", "bilibili.com", "b23.tv")):
        return "https://" + raw
    return ""


def _normalize_bilibili_thumbnail(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("//"):
        return "https:" + raw
    return raw


def _normalize_thumbnail(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("//"):
        return "https:" + raw
    return raw


def _parse_duration_to_seconds(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    parts = [part for part in text.split(":") if part.isdigit()]
    if not parts:
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _basename_without_ext(url: str) -> str:
    tail = str(url or "").rsplit("/", 1)[-1]
    return tail.split(".", 1)[0]


def _is_bilibili_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    return _is_bilibili_host(urllib.parse.urlparse(raw).netloc.lower())


def _is_bilibili_host(host: str) -> bool:
    return host.endswith("bilibili.com") or host.endswith("b23.tv")


def _extract_bvid(text: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]{10})", str(text or ""))
    return match.group(1) if match else ""


def _extract_aid(text: str) -> str:
    match = re.search(r"/av(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _extract_episode_id(text: str) -> str:
    match = re.search(r"/ep(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _extract_season_id(text: str) -> str:
    match = re.search(r"/ss(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _extract_media_id(text: str) -> str:
    raw = str(text or "")
    for pattern in (
        r"/ml(\d+)",
        r"media_id=(\d+)",
    ):
        match = re.search(pattern, raw)
        if match:
            return match.group(1)
    return ""


def _extract_space_mid(text: str) -> str:
    match = re.search(r"space\.bilibili\.com/(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _extract_space_list_id(text: str) -> str:
    match = re.search(r"/lists/(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _collection_base_key(video_id: str) -> str:
    """去掉分 P 后缀的视频键。

    合集里的条目一般不带 `:pN`，而当前播放地址可能带（`?p=1`），
    直接比对会把"同一集"判成两条。
    """
    key = str(video_id or "")
    head, sep, tail = key.rpartition(":p")
    if sep and tail.isdigit():
        return head
    return key


def _bilibili_video_key(url: str, bvid: str = "", aid: str = "") -> str:
    parsed = urllib.parse.urlparse(url)
    page = str((urllib.parse.parse_qs(parsed.query).get("p") or [""])[0]).strip()
    actual_bvid = _extract_bvid(bvid or url)
    if actual_bvid:
        suffix = f":p{page}" if page.isdigit() else ""
        return f"bilibili:{actual_bvid}{suffix}"
    actual_aid = str(aid or _extract_aid(url)).strip()
    if actual_aid:
        suffix = f":p{page}" if page.isdigit() else ""
        return f"bilibili:av{actual_aid}{suffix}"
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return f"bilibili:{tail or 'unknown'}"
