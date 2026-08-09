from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 常用语言码 → 中文名。yt-dlp 对 YouTube 会直接给出可读的 name，这张表主要用于
# B 站的 ai-zh / ai-en 之类只有语言码的轨道。
LANGUAGE_NAMES = {
    "zh": "中文",
    "zh-hans": "简体中文",
    "zh-hant": "繁体中文",
    "zh-cn": "中文（简体）",
    "zh-tw": "中文（台湾）",
    "zh-hk": "中文（香港）",
    "yue": "粤语",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "es": "西班牙语",
    "fr": "法语",
    "de": "德语",
    "ru": "俄语",
    "pt": "葡萄牙语",
    "ar": "阿拉伯语",
    "it": "意大利语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "hi": "印地语",
    "ms": "马来语",
    "tr": "土耳其语",
}


@dataclass
class HomeVideo:
    video_id: str
    title: str
    webpage_url: str
    source_site: str = "youtube"
    uploader: str = ""
    duration: int = 0
    thumbnail: str = ""
    upload_date: str = ""  # YYYYMMDD，与 VideoInfo.upload_date 同格式；拿不到时为空


@dataclass
class PlaylistEntry:
    playlist_id: str
    video_id: str
    title: str
    webpage_url: str
    source_site: str = "youtube"
    uploader: str = ""
    duration: int = 0
    thumbnail: str = ""
    position: int = 0
    availability: str = ""
    upload_date: str = ""

    def to_home_video(self) -> HomeVideo:
        return HomeVideo(
            video_id=self.video_id,
            title=self.title,
            webpage_url=self.webpage_url,
            source_site=self.source_site,
            uploader=self.uploader,
            duration=self.duration,
            thumbnail=self.thumbnail,
            upload_date=self.upload_date,
        )


@dataclass
class PlaylistInfo:
    playlist_id: str
    title: str
    webpage_url: str
    source_site: str = "youtube"
    uploader: str = ""
    thumbnail: str = ""
    entry_count: int = 0
    source_type: str = "playlist"
    current_video_id: str = ""
    entries: list[PlaylistEntry] = field(default_factory=list)


@dataclass
class SavedPlaylist:
    playlist_key: str
    name: str
    source_url: str = ""
    source_type: str = "manual"
    auto_play_next: bool = False
    created_at: str = ""
    updated_at: str = ""
    entries: list[PlaylistEntry] = field(default_factory=list)


@dataclass
class VideoQuality:
    label: str
    height: int
    width: int
    fps: int
    vcodec: str
    acodec: str
    ext: str
    format_id: str
    video_url: str
    audio_url: str | None = None
    audio_format_id: str | None = None
    audio_filesize: int | None = None
    audio_tbr: float | None = None
    filesize: int | None = None
    tbr: float | None = None


@dataclass
class SubtitleInfo:
    language: str
    ext: str
    url: str = ""
    is_auto: bool = False
    # yt-dlp 对部分站点（如 B 站 AI 字幕）直接内联字幕正文，不给 url。
    data: str = ""
    # yt-dlp 提供的可读语言名，如 "Chinese (Taiwan)"；缺失时回退语言代码。
    name: str = ""

    @property
    def is_ai_generated(self) -> bool:
        """B 站的 AI 字幕语言码形如 ai-zh，yt-dlp 不会另外标注。"""
        return self.language.lower().startswith("ai-")

    @property
    def display_language(self) -> str:
        if self.name.strip():
            return self.name.strip()
        code = self.language[3:] if self.is_ai_generated else self.language
        return LANGUAGE_NAMES.get(code.lower(), code or self.language)

    @property
    def label(self) -> str:
        if self.is_ai_generated:
            suffix = "AI 字幕"
        else:
            suffix = "自动" if self.is_auto else "字幕"
        name = self.display_language
        code = f" [{self.language}]" if name != self.language else ""
        return f"{name}{code} · {suffix}"

    @property
    def is_usable(self) -> bool:
        return bool(self.url or self.data)


@dataclass
class VideoInfo:
    video_id: str
    title: str
    source_site: str = "youtube"
    description: str = ""
    uploader: str = ""
    channel_id: str = ""
    creator_id: str = ""
    creator_url: str = ""
    duration: int = 0
    upload_date: str = ""
    webpage_url: str = ""
    thumbnail: str = ""
    qualities: dict[str, VideoQuality] = field(default_factory=dict)
    subtitles: dict[str, SubtitleInfo] = field(default_factory=dict)
    automatic_captions: dict[str, Any] = field(default_factory=dict)
    http_headers: dict[str, str] = field(default_factory=dict)
    raw_info: dict[str, Any] | None = None
