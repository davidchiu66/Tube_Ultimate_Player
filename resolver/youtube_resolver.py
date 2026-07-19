from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app_paths import thirdpart_path
from resolver.models import HomeVideo, PlaylistEntry, PlaylistInfo, VideoInfo
from resolver.quality_selector import QualitySelector
from resolver.subtitle_parser import SubtitleParser
from services.config_service import ConfigService, detect_browser_cookie_sources
from services.cookie_service import prepare_cookie_file
from services.logging_service import sanitize_command


logger = logging.getLogger("tube_player.resolver")
ytdlp_logger = logging.getLogger("tube_player.ytdlp")
VIDEO_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]{11}$")


class YoutubeResolver:
    def __init__(self, config: ConfigService) -> None:
        self.config = config
        self.ytdlp_path = self._find_ytdlp()

    def resolve(self, url: str) -> VideoInfo:
        normalized_url = normalize_youtube_video_url(url)
        if normalized_url:
            if normalized_url != url:
                ytdlp_logger.info("normalized YouTube URL from %s to %s", url, normalized_url)
            url = normalized_url
        elif _is_youtube_playlist_url(url):
            raise RuntimeError("当前链接是 YouTube 播放列表，不支持直接播放，请打开具体视频后再播放。")

        command = self._build_command(url)
        result = self._run_ytdlp(command, url, "primary")
        if result.returncode != 0:
            result = self._retry_with_alternate_browsers(
                url,
                "primary",
                lambda browser: self._build_command(url, override_cookie_browser=browser),
                result,
            )
        if result.returncode != 0 and self._should_retry_with_cookie_file(result):
            cookie_file = self.config.cookie_file_for_url(url)
            if cookie_file:
                ytdlp_logger.warning("browser cookie extraction failed; retrying with configured cookie file")
                command = self._build_command(url, force_cookie_file=True)
                result = self._run_ytdlp(command, url, "fallback-cookie-file")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            ytdlp_logger.error("yt-dlp failed detail:\n%s", detail)
            raise RuntimeError(self._format_error(detail))

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            ytdlp_logger.exception("yt-dlp returned invalid JSON")
            raise RuntimeError(f"yt-dlp 返回的 JSON 无法解析: {exc}") from exc

        self._log_info_summary(info)
        return self._parse_info(info, result.stderr.strip())

    def detect_url_kind(self, url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return "unknown"
        if _is_youtube_video_with_playlist_url(raw):
            return "video_with_playlist"
        if _is_youtube_playlist_url(raw):
            return "playlist"
        if normalize_youtube_video_url(raw):
            return "video"
        return "unknown"

    def resolve_playlist(self, url: str) -> PlaylistInfo:
        kind = self.detect_url_kind(url)
        if kind not in ("playlist", "video_with_playlist"):
            raise RuntimeError("当前链接不是 YouTube 播放列表或专辑链接")

        return self.resolve_playlist_generic(url)

    def resolve_playlist_generic(self, url: str) -> PlaylistInfo:
        source_site = _detect_source_site(url)

        command = self._build_playlist_command(url)
        result = self._run_ytdlp(command, url, "playlist")
        if result.returncode != 0:
            result = self._retry_with_alternate_browsers(
                url,
                "playlist",
                lambda browser: self._build_playlist_command(url, override_cookie_browser=browser),
                result,
            )
        if result.returncode != 0 and self._should_retry_with_cookie_file(result):
            cookie_file = self.config.cookie_file_for_url(url)
            if cookie_file:
                ytdlp_logger.warning("playlist browser cookie extraction failed; retrying with configured cookie file")
                command = self._build_playlist_command(url, force_cookie_file=True)
                result = self._run_ytdlp(command, url, "playlist-fallback-cookie-file")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            ytdlp_logger.error("yt-dlp playlist failed detail:\n%s", detail)
            raise RuntimeError(self._format_error(detail))

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            ytdlp_logger.exception("yt-dlp playlist returned invalid JSON")
            raise RuntimeError(f"yt-dlp 返回的 playlist JSON 无法解析: {exc}") from exc

        playlist = self._parse_playlist_info(info, url, source_site=source_site)
        ytdlp_logger.info(
            "playlist resolved site=%s id=%s title=%s source_type=%s count=%s current_video_id=%s",
            source_site,
            playlist.playlist_id,
            playlist.title,
            playlist.source_type,
            len(playlist.entries),
            playlist.current_video_id,
        )
        return playlist

    def fetch_home_videos(
        self,
        page: int = 1,
        page_size: int = 56,
    ) -> tuple[list[HomeVideo], bool]:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        total_needed = page * page_size + 1
        url = "https://www.youtube.com/"
        command = self._build_home_command(url, total_needed)
        result = self._run_ytdlp(command, url, f"home-page-{page}")
        if result.returncode != 0:
            result = self._retry_with_alternate_browsers(
                url,
                f"home-page-{page}",
                lambda browser: self._build_home_command(url, total_needed, override_cookie_browser=browser),
                result,
            )
        if result.returncode != 0 and self._should_retry_with_cookie_file(result):
            cookie_file = self.config.cookie_file_for_url(url)
            if cookie_file:
                ytdlp_logger.warning("home browser cookie extraction failed; retrying with configured cookie file")
                command = self._build_home_command(url, total_needed, force_cookie_file=True)
                result = self._run_ytdlp(command, url, f"home-page-{page}-fallback-cookie-file")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            ytdlp_logger.error("yt-dlp home fetch failed detail:\n%s", detail)
            raise RuntimeError(self._format_error(detail))

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            ytdlp_logger.exception("yt-dlp home returned invalid JSON")
            raise RuntimeError(f"yt-dlp 返回的首页 JSON 无法解析: {exc}") from exc

        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        videos = [video for entry in entries if (video := self._parse_home_entry(entry))]
        start = (page - 1) * page_size
        end = start + page_size
        paged = videos[start:end]
        has_next = len(videos) > end
        ytdlp_logger.info(
            "home videos fetched page=%s page_size=%s count=%s has_next=%s source_entries=%s",
            page,
            page_size,
            len(paged),
            has_next,
            len(entries),
        )
        return paged, has_next

    def search_videos(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 45,
    ) -> tuple[list[HomeVideo], bool]:
        query = str(keyword or "").strip()
        if not query:
            return [], False

        page = max(1, int(page))
        page_size = max(1, min(45, int(page_size)))
        total_needed = page * page_size + 1
        url = f"ytsearch{total_needed}:{query}"
        command = self._build_home_command(url, total_needed)
        result = self._run_ytdlp(command, url, f"search-page-{page}")
        if result.returncode != 0:
            result = self._retry_with_alternate_browsers(
                url,
                f"search-page-{page}",
                lambda browser: self._build_home_command(url, total_needed, override_cookie_browser=browser),
                result,
            )
        if result.returncode != 0 and self._should_retry_with_cookie_file(result):
            cookie_file = self.config.cookie_file_for_url(url)
            if cookie_file:
                ytdlp_logger.warning("search browser cookie extraction failed; retrying with configured cookie file")
                command = self._build_home_command(url, total_needed, force_cookie_file=True)
                result = self._run_ytdlp(command, url, f"search-page-{page}-fallback-cookie-file")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            ytdlp_logger.error("yt-dlp search failed detail:\n%s", detail)
            raise RuntimeError(self._format_error(detail))

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            ytdlp_logger.exception("yt-dlp search returned invalid JSON")
            raise RuntimeError(f"yt-dlp 返回的搜索 JSON 无法解析: {exc}") from exc

        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        videos = [video for entry in entries if (video := self._parse_home_entry(entry))]
        start = (page - 1) * page_size
        end = start + page_size
        paged = videos[start:end]
        has_next = len(videos) > end
        ytdlp_logger.info(
            "search videos fetched keyword=%s page=%s page_size=%s count=%s has_next=%s source_entries=%s",
            query,
            page,
            page_size,
            len(paged),
            has_next,
            len(entries),
        )
        return paged, has_next

    def fetch_creator_videos(
        self,
        video: VideoInfo,
        limit: int = 50,
    ) -> tuple[str, list[PlaylistEntry]]:
        creator_url = _creator_videos_url(video)
        if not creator_url:
            raise RuntimeError("当前视频缺少可用的制作者主页")

        limit = max(1, min(50, int(limit)))
        command = self._build_home_command(creator_url, limit)
        result = self._run_ytdlp(command, creator_url, "creator-videos")
        if result.returncode != 0:
            result = self._retry_with_alternate_browsers(
                creator_url,
                "creator-videos",
                lambda browser: self._build_home_command(
                    creator_url,
                    limit,
                    override_cookie_browser=browser,
                ),
                result,
            )
        if result.returncode != 0 and self._should_retry_with_cookie_file(result):
            cookie_file = self.config.cookie_file_for_url(creator_url)
            if cookie_file:
                command = self._build_home_command(creator_url, limit, force_cookie_file=True)
                result = self._run_ytdlp(command, creator_url, "creator-videos-fallback-cookie-file")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(self._format_error(detail))

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"yt-dlp 返回的作者视频 JSON 无法解析: {exc}") from exc

        playlist_id = f"{video.source_site}:creator:{video.creator_id or video.channel_id}"
        entries: list[PlaylistEntry] = []
        for index, item in enumerate(info.get("entries") or [], start=1):
            if not isinstance(item, dict):
                continue
            entry = self._parse_playlist_entry(
                item,
                playlist_id,
                index,
                source_site=video.source_site,
            )
            if entry is not None:
                entries.append(entry)
        ytdlp_logger.info(
            "creator videos fetched site=%s creator=%s count=%s",
            video.source_site,
            video.creator_id or video.channel_id,
            len(entries),
        )
        return creator_url, entries

    def _run_ytdlp(
        self,
        command: list[str],
        url: str,
        attempt: str,
    ) -> subprocess.CompletedProcess[str]:
        safe_command = sanitize_command(command)
        started = time.perf_counter()
        ytdlp_logger.info("yt-dlp resolve start attempt=%s url=%s", attempt, url)
        ytdlp_logger.debug("yt-dlp command attempt=%s command=%s", attempt, safe_command)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=creationflags,
        )
        elapsed = time.perf_counter() - started
        ytdlp_logger.info(
            "yt-dlp resolve finished attempt=%s returncode=%s elapsed=%.2fs stdout_bytes=%s stderr_bytes=%s",
            attempt,
            result.returncode,
            elapsed,
            len(result.stdout.encode("utf-8", errors="replace")) if result.stdout else 0,
            len(result.stderr.encode("utf-8", errors="replace")) if result.stderr else 0,
        )
        if result.stderr:
            ytdlp_logger.warning("yt-dlp stderr attempt=%s:\n%s", attempt, result.stderr.strip())
        return result

    def _retry_with_alternate_browsers(
        self,
        url: str,
        attempt_prefix: str,
        builder,
        result: subprocess.CompletedProcess[str],
    ) -> subprocess.CompletedProcess[str]:
        if not self._should_retry_with_alternate_browser(result):
            return result
        for index, browser in enumerate(self._alternate_cookie_browsers(), start=1):
            ytdlp_logger.warning("retrying with alternate browser cookies source=%s url=%s", browser, url)
            command = builder(browser)
            result = self._run_ytdlp(command, url, f"{attempt_prefix}-alt-browser-{index}")
            if result.returncode == 0:
                return result
        return result

    def _alternate_cookie_browsers(self) -> list[str]:
        current = self.config.explicit_cookie_browser() or self.config.auto_cookie_browser()
        browsers: list[str] = []
        for _label, value in detect_browser_cookie_sources():
            if not value or value == current or value in browsers:
                continue
            browsers.append(value)
        return browsers

    def _build_command(
        self,
        url: str,
        force_cookie_file: bool = False,
        override_cookie_browser: str = "",
    ) -> list[str]:
        languages = self.config.get("youtube.subtitle_languages", ["zh-Hans", "zh-Hant", "zh", "en"])
        command = [
            str(self.ytdlp_path),
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "--geo-bypass",
            "--socket-timeout",
            "30",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "vtt/srt/best",
        ]

        js_runtime = self.config.js_runtime()
        if js_runtime:
            command.extend(["--js-runtimes", js_runtime])

        _, proxy = self.config.effective_proxy()
        if proxy:
            command.extend(["--proxy", proxy])

        cookie_browser = override_cookie_browser or self.config.explicit_cookie_browser()
        cookie_file = self.config.cookie_file_for_url(url)
        if force_cookie_file and cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif cookie_browser:
            command.extend(["--cookies-from-browser", cookie_browser])
        elif cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif auto_cookie_browser := self.config.auto_cookie_browser():
            command.extend(["--cookies-from-browser", auto_cookie_browser])

        command.append(url)
        return command

    def _build_home_command(
        self,
        url: str,
        limit: int,
        force_cookie_file: bool = False,
        override_cookie_browser: str = "",
    ) -> list[str]:
        command = [
            str(self.ytdlp_path),
            "--dump-single-json",
            "--flat-playlist",
            "--playlist-end",
            str(max(1, min(500, limit))),
            "--skip-download",
            "--geo-bypass",
            "--socket-timeout",
            "30",
            "--retries",
            "5",
        ]

        js_runtime = self.config.js_runtime()
        if js_runtime:
            command.extend(["--js-runtimes", js_runtime])

        _, proxy = self.config.effective_proxy()
        if proxy:
            command.extend(["--proxy", proxy])

        cookie_browser = override_cookie_browser or self.config.explicit_cookie_browser()
        cookie_file = self.config.cookie_file_for_url(url)
        if force_cookie_file and cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif cookie_browser:
            command.extend(["--cookies-from-browser", cookie_browser])
        elif cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif auto_cookie_browser := self.config.auto_cookie_browser():
            command.extend(["--cookies-from-browser", auto_cookie_browser])

        command.append(url)
        return command

    def _build_playlist_command(
        self,
        url: str,
        force_cookie_file: bool = False,
        override_cookie_browser: str = "",
    ) -> list[str]:
        command = [
            str(self.ytdlp_path),
            "--dump-single-json",
            "--flat-playlist",
            "--yes-playlist",
            "--skip-download",
            "--geo-bypass",
            "--socket-timeout",
            "30",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
        ]

        js_runtime = self.config.js_runtime()
        if js_runtime:
            command.extend(["--js-runtimes", js_runtime])

        _, proxy = self.config.effective_proxy()
        if proxy:
            command.extend(["--proxy", proxy])

        cookie_browser = override_cookie_browser or self.config.explicit_cookie_browser()
        cookie_file = self.config.cookie_file_for_url(url)
        if force_cookie_file and cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif cookie_browser:
            command.extend(["--cookies-from-browser", cookie_browser])
        elif cookie_file:
            command.extend(["--cookies", prepare_cookie_file(cookie_file, url)])
        elif auto_cookie_browser := self.config.auto_cookie_browser():
            command.extend(["--cookies-from-browser", auto_cookie_browser])

        command.append(url)
        return command

    def _parse_info(self, info: dict, warnings: str = "") -> VideoInfo:
        formats = info.get("formats") or []
        qualities = QualitySelector.select_all(formats)
        if not qualities:
            ytdlp_logger.error("no playable qualities found: %s", self._format_stats(info, formats))
            raise RuntimeError(self._format_no_quality_error(info, formats, warnings))

        subtitles = SubtitleParser.parse(
            info.get("subtitles") or {},
            info.get("automatic_captions") or {},
        )

        webpage_url = str(info.get("webpage_url") or "")
        source_site = _detect_source_site(webpage_url)
        if source_site == "bilibili":
            creator_id = str(info.get("uploader_id") or info.get("channel_id") or "").strip()
            creator_url = str(info.get("uploader_url") or info.get("channel_url") or "").strip()
            if not creator_id and isinstance(info.get("owner"), dict):
                creator_id = str((info.get("owner") or {}).get("mid") or "").strip()
            if creator_id and not creator_url:
                creator_url = f"https://space.bilibili.com/{creator_id}"
        else:
            creator_id = str(info.get("channel_id") or info.get("uploader_id") or "").strip()
            creator_url = str(info.get("channel_url") or info.get("uploader_url") or "").strip()
            if creator_id and not creator_url:
                creator_url = f"https://www.youtube.com/channel/{creator_id}"
        video_id = str(info.get("id") or "")
        if source_site == "bilibili":
            video_id = _bilibili_video_key(
                webpage_url,
                bvid=str(info.get("bvid") or info.get("id") or ""),
                aid=str(info.get("aid") or ""),
            )

        return VideoInfo(
            video_id=video_id,
            title=str(info.get("title") or "未命名视频"),
            source_site=source_site,
            description=str(info.get("description") or ""),
            uploader=str(info.get("uploader") or ""),
            channel_id=str(info.get("channel_id") or info.get("uploader_id") or ""),
            creator_id=creator_id,
            creator_url=creator_url,
            duration=int(info.get("duration") or 0),
            upload_date=str(info.get("upload_date") or ""),
            webpage_url=webpage_url,
            thumbnail=_normalize_thumbnail_url(str(info.get("thumbnail") or "")),
            qualities=qualities,
            subtitles=subtitles,
            automatic_captions=info.get("automatic_captions") or {},
            http_headers=info.get("http_headers") or {},
            raw_info=info,
        )

    @staticmethod
    def _parse_playlist_info(info: dict, source_url: str, source_site: str = "youtube") -> PlaylistInfo:
        raw_entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        playlist_id = str(info.get("id") or "").strip() or _playlist_id_from_url(source_url) or _generic_playlist_id(source_url)
        current_video_id = _current_video_id_for_site(source_url, source_site)
        source_type = _detect_playlist_source_type(info, source_url)
        thumbnail = _extract_thumbnail(info) or _playlist_thumbnail_fallback(raw_entries, current_video_id)
        webpage_url = str(info.get("webpage_url") or source_url or "").strip()

        entries: list[PlaylistEntry] = []
        for index, entry in enumerate(raw_entries, start=1):
            parsed = YoutubeResolver._parse_playlist_entry(entry, playlist_id, index, source_site=source_site)
            if parsed:
                entries.append(parsed)

        return PlaylistInfo(
            playlist_id=playlist_id or f"playlist-{int(time.time())}",
            title=str(info.get("title") or "未命名播放列表"),
            webpage_url=webpage_url,
            source_site=source_site,
            uploader=str(info.get("uploader") or info.get("channel") or "").strip(),
            thumbnail=thumbnail,
            entry_count=int(info.get("playlist_count") or info.get("n_entries") or len(entries) or 0),
            source_type=source_type,
            current_video_id=current_video_id,
            entries=entries,
        )

    @staticmethod
    def _parse_playlist_entry(
        entry: dict,
        playlist_id: str,
        position: int,
        source_site: str = "youtube",
    ) -> PlaylistEntry | None:
        if source_site == "bilibili":
            return YoutubeResolver._parse_bilibili_playlist_entry(entry, playlist_id, position)

        video_id = _clean_video_id(str(entry.get("id") or "").strip())
        url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        normalized_url = normalize_youtube_video_url(url, fallback_video_id=video_id)
        if not normalized_url:
            return None
        actual_video_id = _video_id_from_watch_url(normalized_url)
        if not actual_video_id:
            return None

        title = str(entry.get("title") or "").strip() or actual_video_id
        thumbnail = _extract_thumbnail(entry)
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{actual_video_id}/hqdefault.jpg"

        return PlaylistEntry(
            playlist_id=playlist_id,
            video_id=actual_video_id,
            title=title,
            webpage_url=normalized_url,
            source_site="youtube",
            uploader=str(entry.get("uploader") or entry.get("channel") or "").strip(),
            duration=int(entry.get("duration") or 0),
            thumbnail=thumbnail,
            position=position,
            availability=str(entry.get("availability") or ""),
        )

    @staticmethod
    def _parse_bilibili_playlist_entry(entry: dict, playlist_id: str, position: int) -> PlaylistEntry | None:
        raw_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        url = _normalize_bilibili_url(raw_url)
        bvid = _extract_bilibili_bvid(url or str(entry.get("bvid") or "").strip())
        aid = str(entry.get("aid") or entry.get("id") or "").strip()
        if not url:
            if bvid:
                url = f"https://www.bilibili.com/video/{bvid}"
            elif aid:
                url = f"https://www.bilibili.com/video/av{aid}"
        if not url:
            return None

        actual_video_id = _bilibili_video_key(url, bvid=bvid, aid=aid)
        title = str(entry.get("title") or "").strip() or actual_video_id
        thumbnail = _normalize_thumbnail_url(_extract_thumbnail(entry))
        return PlaylistEntry(
            playlist_id=playlist_id,
            video_id=actual_video_id,
            title=title,
            webpage_url=url,
            source_site="bilibili",
            uploader=str(entry.get("uploader") or entry.get("channel") or "").strip(),
            duration=int(entry.get("duration") or 0),
            thumbnail=thumbnail,
            position=position,
            availability=str(entry.get("availability") or ""),
        )

    @staticmethod
    def _parse_home_entry(entry: dict) -> HomeVideo | None:
        video_id = _clean_video_id(str(entry.get("id") or "").strip())
        title = str(entry.get("title") or "").strip()
        if not title:
            return None

        url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        normalized_url = normalize_youtube_video_url(url, fallback_video_id=video_id)
        if not normalized_url:
            return None
        url = normalized_url
        video_id = _video_id_from_watch_url(url)
        if not video_id:
            return None

        thumbnail = _normalize_thumbnail_url(str(entry.get("thumbnail") or "").strip())
        thumbnails = entry.get("thumbnails")
        if not thumbnail and isinstance(thumbnails, list) and thumbnails:
            for item in reversed(thumbnails):
                if isinstance(item, dict) and item.get("url"):
                    thumbnail = _normalize_thumbnail_url(str(item["url"]))
                    break
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        return HomeVideo(
            video_id=video_id,
            title=title,
            webpage_url=url,
            source_site="youtube",
            uploader=str(entry.get("uploader") or entry.get("channel") or "").strip(),
            duration=int(entry.get("duration") or 0),
            thumbnail=thumbnail,
        )

    @staticmethod
    def _format_stats(info: dict, formats: list[dict]) -> dict:
        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "formats": len(formats),
            "with_url": sum(1 for fmt in formats if fmt.get("url")),
            "with_manifest": sum(1 for fmt in formats if fmt.get("manifest_url")),
            "video": sum(1 for fmt in formats if fmt.get("vcodec") not in (None, "none")),
            "audio": sum(1 for fmt in formats if fmt.get("acodec") not in (None, "none")),
            "live_status": info.get("live_status"),
            "availability": info.get("availability"),
        }

    @classmethod
    def _log_info_summary(cls, info: dict) -> None:
        formats = info.get("formats") or []
        ytdlp_logger.info("yt-dlp info summary: %s", cls._format_stats(info, formats))
        sample = []
        for fmt in formats[:20]:
            sample.append(
                {
                    "format_id": fmt.get("format_id"),
                    "ext": fmt.get("ext"),
                    "protocol": fmt.get("protocol"),
                    "height": fmt.get("height"),
                    "fps": fmt.get("fps"),
                    "vcodec": fmt.get("vcodec"),
                    "acodec": fmt.get("acodec"),
                    "has_url": bool(fmt.get("url")),
                    "has_manifest_url": bool(fmt.get("manifest_url")),
                }
            )
        ytdlp_logger.debug("yt-dlp format sample(first 20, urls omitted): %s", sample)

    @staticmethod
    def _find_ytdlp() -> Path:
        for name in ("yt-dlp.exe", "yt-dlp_linux", "yt-dlp"):
            bundled = thirdpart_path(name)
            if bundled.is_file():
                return bundled
        return Path("yt-dlp")

    @staticmethod
    def _format_error(detail: str) -> str:
        if not detail:
            return "yt-dlp 解析失败"
        lower = detail.lower()
        if "playlist type is unviewable" in lower:
            return (
                "当前链接指向 YouTube 播放列表或推荐列表，不是具体视频地址。\n\n"
                "请打开列表中的某一个视频后再播放；如果来自首页卡片，程序会自动尝试转换为具体视频地址。\n\n"
                f"{detail}"
            )
        if "sign in to confirm" in lower and "not a bot" in lower:
            if "cookies are no longer valid" in lower:
                return (
                    "YouTube 判定当前 Cookie 已失效或已被浏览器安全轮换。\n\n"
                    "请重新导出 Netscape 格式 cookies.txt；如果使用 Brave/Chrome/Edge 直接读取，"
                    "请先完全关闭浏览器及后台进程后重试。\n\n"
                    f"{detail}"
                )
            return (
                "YouTube 要求登录确认不是机器人，当前 Cookie 没有通过校验。\n\n"
                "建议在设置页选择从浏览器读取 Cookie，并确认浏览器里已经登录 YouTube。\n\n"
                f"{detail}"
            )
        if "could not copy" in lower and "cookie database" in lower:
            return (
                "yt-dlp 无法复制浏览器 Cookie 数据库。浏览器可能正在运行、数据库被锁定，"
                "或当前系统权限不允许读取。\n\n"
                "如果已经配置了 cookie.txt，请保持自动检测或关闭浏览器读取；如要直接读浏览器 Cookie，请先关闭浏览器后重试。\n\n"
                f"{detail}"
            )
        if "failed to decrypt with dpapi" in lower:
            return (
                "yt-dlp 无法使用 Windows DPAPI 解密浏览器 Cookie。\n\n"
                "程序会优先回退到配置的 cookie.txt；如果仍失败，请重新导出 Netscape 格式 cookies.txt。\n\n"
                f"{detail}"
            )
        if "requested format is not available" in lower:
            return (
                "yt-dlp 报告请求的格式不可用。当前程序会自动选择可播放格式，如仍出现此提示，"
                "通常说明该视频当前返回的格式集合异常或受限制。\n\n"
                f"{detail}"
            )
        return detail

    @staticmethod
    def _should_retry_with_cookie_file(result: subprocess.CompletedProcess[str]) -> bool:
        detail = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        browser_cookie_failures = (
            "could not copy chrome cookie database",
            "failed to decrypt with dpapi",
            "could not find chrome cookies database",
        )
        return any(message in detail for message in browser_cookie_failures)

    @staticmethod
    def _should_retry_with_alternate_browser(result: subprocess.CompletedProcess[str]) -> bool:
        detail = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        browser_cookie_failures = (
            "failed to decrypt with dpapi",
            "could not copy chrome cookie database",
            "could not find chrome cookies database",
            "could not find firefox cookies database",
        )
        return any(message in detail for message in browser_cookie_failures)

    @staticmethod
    def _format_no_quality_error(info: dict, formats: list[dict], warnings: str) -> str:
        with_url = sum(1 for fmt in formats if fmt.get("url"))
        with_manifest = sum(1 for fmt in formats if fmt.get("manifest_url"))
        videos = sum(1 for fmt in formats if fmt.get("vcodec") not in (None, "none"))
        audios = sum(1 for fmt in formats if fmt.get("acodec") not in (None, "none"))
        live_status = info.get("live_status") or ""
        availability = info.get("availability") or ""

        lines = [
            "yt-dlp 返回了视频信息，但没有找到可直接交给播放器的媒体地址。",
            "",
            f"formats 总数: {len(formats)}",
            f"带 url 的格式: {with_url}",
            f"带 manifest_url 的格式: {with_manifest}",
            f"视频格式数: {videos}",
            f"音频格式数: {audios}",
        ]
        if live_status:
            lines.append(f"直播状态: {live_status}")
        if availability:
            lines.append(f"可用性: {availability}")
        if warnings:
            lines.extend(["", "yt-dlp 警告:", warnings])
        lines.extend(
            [
                "",
                "通常原因：Cookie 已失效、账号未通过 YouTube 校验、视频受地区/年龄/会员限制，",
                "或者 YouTube 当前返回的可播放格式不足。",
            ]
        )
        return "\n".join(lines)


def normalize_youtube_video_url(url: str, fallback_video_id: str = "") -> str:
    raw = str(url or "").strip()
    fallback = _clean_video_id(fallback_video_id)
    if not raw:
        return _watch_url(fallback) if fallback else ""

    if not raw.startswith(("http://", "https://")):
        direct_id = _clean_video_id(raw)
        if direct_id:
            return _watch_url(direct_id)
        return _watch_url(fallback) if fallback else ""

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    query = parse_qs(parsed.query)

    if not _is_youtube_host(host):
        return raw

    video_id = _clean_video_id((query.get("v") or [""])[0])
    if video_id:
        return _watch_url(video_id)

    parts = [part for part in path.split("/") if part]
    if host.endswith("youtu.be") and parts:
        video_id = _clean_video_id(parts[0])
        if video_id:
            return _watch_url(video_id)

    if parts and parts[0] in ("shorts", "live", "embed") and len(parts) > 1:
        video_id = _clean_video_id(parts[1])
        if video_id:
            return _watch_url(video_id)

    list_id = str((query.get("list") or [""])[0]).strip()
    if list_id.startswith("RD"):
        video_id = _clean_video_id(list_id[2:])
        if video_id:
            return _watch_url(video_id)

    return _watch_url(fallback) if fallback else ""


def _video_id_from_watch_url(url: str) -> str:
    parsed = urlparse(url)
    return _clean_video_id((parse_qs(parsed.query).get("v") or [""])[0])


def _clean_video_id(value: str) -> str:
    candidate = str(value or "").strip()
    return candidate if VIDEO_ID_PATTERN.match(candidate) else ""


def _playlist_id_from_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    parsed = urlparse(raw)
    return str((parse_qs(parsed.query).get("list") or [""])[0]).strip()


def _generic_playlist_id(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return tail or parsed.netloc or ""


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _is_youtube_host(host: str) -> bool:
    return host.endswith("youtube.com") or host.endswith("youtu.be") or host.endswith("youtube-nocookie.com")


def _is_bilibili_host(host: str) -> bool:
    return host.endswith("bilibili.com") or host.endswith("b23.tv")


def _creator_videos_url(video: VideoInfo) -> str:
    raw = str(video.creator_url or "").strip().rstrip("/")
    if video.source_site == "bilibili":
        if not raw and video.creator_id:
            raw = f"https://space.bilibili.com/{video.creator_id}"
        if not raw:
            return ""
        parsed = urlparse(raw)
        raw = parsed._replace(query="", fragment="").geturl().rstrip("/")
        return raw if raw.endswith("/video") else f"{raw}/video"

    if not raw and (video.creator_id or video.channel_id):
        raw = f"https://www.youtube.com/channel/{video.creator_id or video.channel_id}"
    if not raw:
        return ""
    parsed = urlparse(raw)
    raw = parsed._replace(query="", fragment="").geturl().rstrip("/")
    return raw if raw.endswith("/videos") else f"{raw}/videos"


def _detect_source_site(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if _is_bilibili_host(host):
        return "bilibili"
    return "youtube"


def _current_video_id_for_site(url: str, source_site: str) -> str:
    if source_site == "bilibili":
        return _bilibili_video_key(url)
    return _video_id_from_watch_url(url)


def _is_youtube_playlist_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    parsed = urlparse(raw)
    if not _is_youtube_host(parsed.netloc.lower()):
        return False
    query = parse_qs(parsed.query)
    return bool(query.get("list")) and not _clean_video_id((query.get("v") or [""])[0])


def _is_youtube_video_with_playlist_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return False
    parsed = urlparse(raw)
    if not _is_youtube_host(parsed.netloc.lower()):
        return False
    query = parse_qs(parsed.query)
    return bool(query.get("list")) and bool(_clean_video_id((query.get("v") or [""])[0]))


def _extract_thumbnail(info: dict) -> str:
    direct = _normalize_thumbnail_url(str(info.get("thumbnail") or "").strip())
    if direct:
        return direct
    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for item in reversed(thumbnails):
            if isinstance(item, dict) and item.get("url"):
                return _normalize_thumbnail_url(str(item["url"]))
    return ""


def _playlist_thumbnail_fallback(entries: list[dict], current_video_id: str) -> str:
    if current_video_id.startswith("bilibili:"):
        for entry in entries:
            thumbnail = _normalize_thumbnail_url(_extract_thumbnail(entry))
            if thumbnail:
                return thumbnail
        return ""
    if current_video_id:
        return f"https://i.ytimg.com/vi/{current_video_id}/hqdefault.jpg"
    for entry in entries:
        entry_id = _clean_video_id(str(entry.get("id") or "").strip())
        if entry_id:
            return f"https://i.ytimg.com/vi/{entry_id}/hqdefault.jpg"
    return ""


def _detect_playlist_source_type(info: dict, source_url: str) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    webpage = str(info.get("webpage_url") or source_url or "").lower()
    title = str(info.get("title") or "").lower()
    if "bilibili" in extractor or "bilibili" in webpage:
        if any(key in webpage for key in ("/bangumi/", "/media/", "/season/")):
            return "album"
        if any(key in webpage for key in ("/favlist/", "/medialist/", "/list/")):
            return "playlist"
        return "playlist"
    if "music.youtube" in webpage:
        return "album"
    if "album" in extractor or "album" in title:
        return "album"
    if _playlist_id_from_url(source_url).startswith("RD"):
        return "mix"
    return "playlist"


def _normalize_bilibili_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://"):
        return "https://" + raw[len("http://") :]
    if raw.startswith(("https://", "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv")):
        return raw if raw.startswith("https://") else "https://" + raw
    return ""


def _normalize_thumbnail_url(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith("//"):
        return "https:" + raw
    return raw


def _extract_bilibili_bvid(text: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]{10})", str(text or ""))
    return match.group(1) if match else ""


def _extract_bilibili_aid(text: str) -> str:
    match = re.search(r"/av(\d+)", str(text or ""))
    return match.group(1) if match else ""


def _bilibili_video_key(url: str, bvid: str = "", aid: str = "") -> str:
    parsed = urlparse(str(url or "").strip())
    page = str((parse_qs(parsed.query).get("p") or [""])[0]).strip()
    actual_bvid = _extract_bilibili_bvid(bvid or url)
    if actual_bvid:
        suffix = f":p{page}" if page.isdigit() else ""
        return f"bilibili:{actual_bvid}{suffix}"
    actual_aid = str(aid or _extract_bilibili_aid(url)).strip()
    if actual_aid:
        suffix = f":p{page}" if page.isdigit() else ""
        return f"bilibili:av{actual_aid}{suffix}"
    path_tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return f"bilibili:{path_tail or 'unknown'}"
