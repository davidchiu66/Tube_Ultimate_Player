from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

from resolver.models import HomeVideo, PlaylistEntry, PlaylistInfo, VideoInfo
from resolver.youtube_resolver import YoutubeResolver
from services.config_service import ConfigService, detect_browser_cookie_sources
from services.cookie_service import load_browser_cookie_header, load_cookie_header


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
_MAX_PAGE_CACHE_ITEMS = 48


class SiteResolver:
    def __init__(self, config: ConfigService) -> None:
        self.config = config
        self.youtube = YoutubeResolver(config)
        self.bilibili = BilibiliResolver(config, self.youtube)
        self._page_cache: OrderedDict[str, tuple[float, list[HomeVideo], bool]] = OrderedDict()

    def home_source(self) -> str:
        return self.config.default_home_source()

    def home_source_label(self) -> str:
        return "Bilibili" if self.home_source() == "bilibili" else "YouTube"

    def resolve(self, url: str) -> VideoInfo:
        return self.youtube.resolve(url)

    def detect_url_kind(self, url: str) -> str:
        if _is_bilibili_url(url):
            return self.bilibili.detect_url_kind(url)
        return self.youtube.detect_url_kind(url)

    def resolve_playlist(self, url: str) -> PlaylistInfo:
        if _is_bilibili_url(url):
            return self.bilibili.resolve_playlist(url)
        return self.youtube.resolve_playlist(url)

    def fetch_home_videos(
        self,
        page: int = 1,
        page_size: int = 56,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[HomeVideo], bool]:
        source = self.home_source()
        key = self._cache_key("home", source, "", page, page_size)
        if not force_refresh and (cached := self._cache_lookup(key, _HOME_CACHE_TTL_SECONDS)):
            return cached
        if source == "bilibili":
            result = self.bilibili.fetch_home_videos(page, page_size)
        else:
            result = self.youtube.fetch_home_videos(page, page_size)
        self._cache_store(key, result)
        return result

    def search_videos(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 56,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[HomeVideo], bool]:
        source = self.home_source()
        query = str(keyword or "").strip()
        key = self._cache_key("search", source, query, page, page_size)
        if not force_refresh and (cached := self._cache_lookup(key, _SEARCH_CACHE_TTL_SECONDS)):
            return cached
        if source == "bilibili":
            result = self.bilibili.search_videos(query, page, page_size)
        else:
            result = self.youtube.search_videos(query, page, page_size)
        self._cache_store(key, result)
        return result

    def _cache_key(self, mode: str, source: str, keyword: str, page: int, page_size: int) -> str:
        fingerprint = self._config_fingerprint()
        normalized = keyword.strip().lower()
        return f"{mode}|{source}|{normalized}|{int(page)}|{int(page_size)}|{fingerprint}"

    def _config_fingerprint(self) -> str:
        cookie_file = self.config.cookie_file()
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
            "default_home": self.config.default_home_source(),
            "cookie_browser": self.config.cookie_browser(),
            "cookie_file": cookie_stamp,
            "proxy_label": proxy_label,
            "proxy_value": proxy_value,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _cache_lookup(self, key: str, ttl_seconds: float) -> tuple[list[HomeVideo], bool] | None:
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
        self._page_cache[key] = (time.time(), list(videos), has_next)
        self._page_cache.move_to_end(key)
        while len(self._page_cache) > _MAX_PAGE_CACHE_ITEMS:
            self._page_cache.popitem(last=False)


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
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _preferred_cookie_header(self, url: str) -> str:
        browser_cookie = self._browser_cookie_header(url)
        if browser_cookie:
            return browser_cookie
        cookie_file = self.config.cookie_file()
        if cookie_file:
            return load_cookie_header(cookie_file, url)
        return ""

    def _browser_cookie_header(self, url: str) -> str:
        tried: set[str] = set()

        explicit = self.config.explicit_cookie_browser()
        if explicit:
            tried.add(explicit)
            header = load_browser_cookie_header(explicit, url)
            if header:
                return header

        auto = self.config.auto_cookie_browser()
        if auto:
            tried.add(auto)
            header = load_browser_cookie_header(auto, url)
            if header:
                return header

        for _label, browser_spec in detect_browser_cookie_sources():
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
    )


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


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
