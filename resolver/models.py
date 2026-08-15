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
    section_id: str = ""
    section_title: str = ""
    section_position: int = 0
    section_thumbnail: str = ""

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
    current_section_id: str = ""
    entries: list[PlaylistEntry] = field(default_factory=list)
    sections: list[PlaylistSection] = field(default_factory=list)


@dataclass
class PlaylistSection:
    section_id: str
    title: str
    position: int
    thumbnail: str = ""
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
    # 同档位的已混音变体地址（多语言视频才有）。选"随画面（免转码）"时改播它，
    # 换回今天的单流行为——投屏没有 FFmpeg 时的出路（C1 裁定）。
    muxed_video_url: str | None = None


@dataclass(frozen=True)
class PlaybackQualityHint:
    label: str
    height: int
    fps: int


@dataclass(frozen=True)
class PlaybackRequestContext:
    request_id: int
    target_url: str
    reason: str = "direct"
    quality_hint: PlaybackQualityHint | None = None


# 音轨下拉里"随画面（免转码）"这一项的 track_id。它不是一条真音轨，而是"回到
# 已混音单流"的开关，故用哨兵值而不是某个 format_id。
MUXED_AUDIO_TRACK_ID = "__muxed_audio__"


@dataclass
class AudioTrack:
    """一条可选的音频轨（YouTube 的多语言配音）。

    与 `VideoQuality.audio_url` 的关系：`VideoQuality` 上挂的是"默认轨"，供不关心
    音轨的调用方（下载、投屏）直接用；这里是完整的可选清单，供用户在播放器里切换。
    """

    track_id: str
    language: str
    url: str
    acodec: str = ""
    abr: float | None = None
    filesize: int | None = None
    tbr: float | None = None
    # yt-dlp 的语言偏好：10 = 原声，5 = 站点默认（按 Cookie/地区判定），其余 -1。
    language_preference: int = -1
    # yt-dlp 给的可读名，如 "English (US)"；缺失时回退语言码。
    name: str = ""

    @property
    def is_original(self) -> bool:
        return self.language_preference >= 10

    @property
    def is_site_default(self) -> bool:
        return self.language_preference == 5

    @property
    def display_language(self) -> str:
        code = str(self.language or "").strip()
        localized = LANGUAGE_NAMES.get(code.lower())
        if localized:
            return localized
        if self.name.strip():
            return self.name.strip()
        # en-US 这类带地区的码表里没有，退一步按主语言查（zh-Hant 已在上面命中，
        # 不会在这里被降级成"中文"而丢掉简繁区分）。
        base = code.split("-", 1)[0]
        return LANGUAGE_NAMES.get(base.lower(), code)

    @property
    def label(self) -> str:
        """下拉里显示的文案，如"英语（原声）"。

        只给原声标后缀：站点默认轨对用户来说就是"正常的那条"，再标一次"默认"
        只会让每条都有后缀，反而看不出哪条特殊。
        """
        name = self.display_language or self.language or "音轨"
        return f"{name}（原声）" if self.is_original else name


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
    def is_translated(self) -> bool:
        """YouTube 的机翻轨：地址带 tlang=，由翻译接口现场生成，配额比原文轨紧得多。"""
        return "tlang=" in str(self.url or "")

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
        elif self.is_translated:
            # 机翻轨要标出来：它们共享 YouTube 翻译接口的配额，容易 429。
            suffix = "机翻"
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
    # 可选音轨（R9）：键 = track_id（即 format_id），插入序即下拉显示序。空表 = 无音轨可选。
    audio_tracks: dict[str, AudioTrack] = field(default_factory=dict)
    automatic_captions: dict[str, Any] = field(default_factory=dict)
    http_headers: dict[str, str] = field(default_factory=dict)
    raw_info: dict[str, Any] | None = None
