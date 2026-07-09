from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HomeVideo:
    video_id: str
    title: str
    webpage_url: str
    source_site: str = "youtube"
    uploader: str = ""
    duration: int = 0
    thumbnail: str = ""


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

    def to_home_video(self) -> HomeVideo:
        return HomeVideo(
            video_id=self.video_id,
            title=self.title,
            webpage_url=self.webpage_url,
            source_site=self.source_site,
            uploader=self.uploader,
            duration=self.duration,
            thumbnail=self.thumbnail,
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
    auto_play_next: bool = True
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
    url: str
    is_auto: bool = False

    @property
    def label(self) -> str:
        suffix = "自动" if self.is_auto else "字幕"
        return f"{self.language} ({suffix}, {self.ext})"


@dataclass
class VideoInfo:
    video_id: str
    title: str
    source_site: str = "youtube"
    description: str = ""
    uploader: str = ""
    channel_id: str = ""
    duration: int = 0
    upload_date: str = ""
    webpage_url: str = ""
    thumbnail: str = ""
    qualities: dict[str, VideoQuality] = field(default_factory=dict)
    subtitles: dict[str, SubtitleInfo] = field(default_factory=dict)
    automatic_captions: dict[str, Any] = field(default_factory=dict)
    http_headers: dict[str, str] = field(default_factory=dict)
    raw_info: dict[str, Any] | None = None
