from __future__ import annotations

from pathlib import Path
import shutil

from app_paths import thirdpart_path
from download.models import DownloadTask
from resolver.models import VideoInfo
from services.config_service import ConfigService
from services.cookie_service import prepare_cookie_file


def build_download_task(
    video: VideoInfo,
    quality_label: str,
    config: ConfigService,
) -> DownloadTask:
    quality = video.qualities.get(quality_label) if quality_label else None
    format_selector = "bestvideo+bestaudio/best"
    expected_bytes = None
    if quality:
        format_selector = quality.format_id
        expected_bytes = _expected_bytes(
            quality.filesize,
            quality.audio_filesize,
            quality.tbr,
            quality.audio_tbr,
            video.duration,
        )
        if quality.audio_format_id:
            if _ffmpeg_available(config):
                format_selector = f"{quality.format_id}+{quality.audio_format_id}"
            else:
                return DownloadTask(
                    url=video.webpage_url,
                    video_id=video.video_id,
                    source_site=video.source_site,
                    title=video.title or video.webpage_url,
                    quality_label=f"{quality_label} (单文件降级)",
                    format_selector="best",
                    save_dir=config.download_dir(),
                    expected_bytes=expected_bytes,
                )
        format_selector = f"{format_selector}/bestvideo+bestaudio/best"

    return DownloadTask(
        url=video.webpage_url,
        video_id=video.video_id,
        source_site=video.source_site,
        title=video.title or video.webpage_url,
        quality_label=quality_label or "Auto",
        format_selector=format_selector,
        save_dir=config.download_dir(),
        expected_bytes=expected_bytes,
    )


def build_download_command(
    task: DownloadTask,
    config: ConfigService,
    force_cookie_file: bool = False,
) -> list[str]:
    output_template = str(Path(task.save_dir) / "%(title).200B [%(id)s].%(ext)s")
    command = [
        str(_find_ytdlp()),
        "--newline",
        "--no-color",
        "--progress",
        "--progress-delta",
        "1",
        "--continue",
        "--no-playlist",
        "--geo-bypass",
        "--socket-timeout",
        "30",
        "--retries",
        "5",
        "--fragment-retries",
        "5",
        "--merge-output-format",
        "mp4",
        "--progress-template",
        "download:progress:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
        "--print",
        "after_move:filepath:%(filepath)s",
        "-o",
        output_template,
        "-f",
        task.format_selector or "bestvideo+bestaudio/best",
    ]

    js_runtime = config.js_runtime()
    if js_runtime:
        command.extend(["--js-runtimes", js_runtime])

    ffmpeg_location = _ffmpeg_location(config)
    if ffmpeg_location:
        command.extend(["--ffmpeg-location", ffmpeg_location])

    _, proxy = config.effective_proxy()
    if proxy:
        command.extend(["--proxy", proxy])

    cookie_browser = config.explicit_cookie_browser()
    cookie_file = config.cookie_file()
    if force_cookie_file and cookie_file:
        command.extend(["--cookies", prepare_cookie_file(cookie_file, task.url)])
    elif cookie_browser:
        command.extend(["--cookies-from-browser", cookie_browser])
    elif cookie_file:
        command.extend(["--cookies", prepare_cookie_file(cookie_file, task.url)])
    elif auto_cookie_browser := config.auto_cookie_browser():
        command.extend(["--cookies-from-browser", auto_cookie_browser])

    command.append(task.url)
    return command


def should_retry_with_cookie_file(output: str) -> bool:
    detail = output.lower()
    browser_cookie_failures = (
        "could not copy chrome cookie database",
        "failed to decrypt with dpapi",
        "could not find chrome cookies database",
    )
    return any(message in detail for message in browser_cookie_failures)


def _find_ytdlp() -> Path:
    bundled = thirdpart_path("yt-dlp.exe")
    if bundled.exists():
        return bundled
    return Path("yt-dlp")


def _ffmpeg_available(config: ConfigService) -> bool:
    location = _ffmpeg_location(config)
    if location:
        return True
    candidates = [thirdpart_path("ffmpeg.exe"), thirdpart_path("ffmpeg")]
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        candidates.append(Path(system_ffmpeg))
    return any(path.exists() for path in candidates)


def _ffmpeg_location(config: ConfigService) -> str:
    configured = config.download_ffmpeg_location()
    if configured and _contains_ffmpeg(Path(configured)):
        return configured
    thirdpart = thirdpart_path("ffmpeg.exe")
    if thirdpart.exists():
        return str(thirdpart.parent)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return str(Path(system_ffmpeg).parent)
    return ""


def _contains_ffmpeg(path: Path) -> bool:
    if path.is_file():
        return path.name.lower() in ("ffmpeg.exe", "ffmpeg")
    return (path / "ffmpeg.exe").exists() or (path / "ffmpeg").exists()


def _expected_bytes(
    video_size: int | None,
    audio_size: int | None,
    video_tbr: float | None,
    audio_tbr: float | None,
    duration: int,
) -> int | None:
    total = _safe_int(video_size) + _safe_int(audio_size)
    if total > 0:
        return total

    if duration <= 0:
        return None
    tbr = _safe_float(video_tbr) + _safe_float(audio_tbr)
    if tbr <= 0:
        return None
    return int(tbr * 1000 / 8 * duration)


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
