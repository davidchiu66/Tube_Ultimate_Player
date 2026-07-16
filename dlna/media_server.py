from __future__ import annotations

import logging
import mimetypes
import secrets
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


logger = logging.getLogger("tube_player.dlna.http")


@dataclass(slots=True)
class DlnaMediaSource:
    title: str
    video_url: str
    audio_url: str | None = None
    file_path: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    mime_type: str = "video/mp4"
    video_codec: str = ""
    audio_codec: str = ""
    ffmpeg_path: str = ""
    proxy: str = ""
    start_position: float = 0.0

    @property
    def requires_mux(self) -> bool:
        return bool(self.audio_url)

    @property
    def is_local_file(self) -> bool:
        return bool(self.file_path)

    @property
    def output_mime_type(self) -> str:
        return "video/mp2t" if self.requires_mux else self.mime_type


class DlnaMediaServer:
    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._bind_host = ""
        self._sources: dict[str, DlnaMediaSource] = {}
        self._sources_lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._processes_lock = threading.Lock()

    def register_source(
        self,
        source: DlnaMediaSource,
        remote_host: str,
        preferred_port: int = 8899,
    ) -> str:
        local_ip = local_ip_for_remote(remote_host)
        self._ensure_started(local_ip, preferred_port)
        token = secrets.token_urlsafe(18)
        with self._sources_lock:
            self._sources.clear()
            self._sources[token] = source
        port = int(self._server.server_address[1]) if self._server else preferred_port
        url = f"http://{local_ip}:{port}/media/{token}"
        logger.info(
            "DLNA media registered url=%s mux=%s mime=%s title=%s",
            url,
            source.requires_mux,
            source.output_mime_type,
            source.title,
        )
        return url

    def source(self, token: str) -> DlnaMediaSource | None:
        with self._sources_lock:
            return self._sources.get(token)

    def stop_streams(self) -> None:
        with self._sources_lock:
            self._sources.clear()
        with self._processes_lock:
            processes = list(self._processes)
        for process in processes:
            _terminate_process(process)

    def stop(self) -> None:
        self.stop_streams()
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def track_process(self, process: subprocess.Popen) -> None:
        with self._processes_lock:
            self._processes.add(process)

    def untrack_process(self, process: subprocess.Popen) -> None:
        with self._processes_lock:
            self._processes.discard(process)

    def _ensure_started(self, bind_host: str, preferred_port: int) -> None:
        if self._server is not None and self._bind_host == bind_host:
            return
        self.stop()
        server = None
        try:
            server = ThreadingHTTPServer((bind_host, int(preferred_port)), _DlnaRequestHandler)
        except OSError as exc:
            logger.warning("DLNA media port %s unavailable on %s: %s; using random port", preferred_port, bind_host, exc)
            server = ThreadingHTTPServer((bind_host, 0), _DlnaRequestHandler)
        server.daemon_threads = True
        server.dlna_owner = self  # type: ignore[attr-defined]
        self._server = server
        self._bind_host = bind_host
        self._thread = threading.Thread(target=server.serve_forever, name="DlnaMediaHttpServer", daemon=True)
        self._thread.start()
        logger.info("DLNA media server started host=%s port=%s", bind_host, server.server_address[1])


class _DlnaRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._serve(head_only=False)

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("DLNA HTTP client=%s %s", self.client_address[0], fmt % args)

    def _serve(self, head_only: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/media/"):
            self.send_error(404)
            return
        token = parsed.path.rsplit("/", 1)[-1]
        owner: DlnaMediaServer = self.server.dlna_owner  # type: ignore[attr-defined]
        source = owner.source(token)
        if source is None:
            self.send_error(404)
            return
        try:
            if source.is_local_file:
                self._serve_file(source, head_only)
            elif source.requires_mux:
                self._serve_muxed(owner, source, head_only)
            else:
                self._serve_proxy(source, head_only)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("DLNA HTTP client disconnected title=%s", source.title)
        except Exception as exc:  # noqa: BLE001
            logger.exception("DLNA media request failed title=%s", source.title)
            try:
                self.send_error(502, str(exc))
            except OSError:
                pass

    def _serve_proxy(self, source: DlnaMediaSource, head_only: bool) -> None:
        headers = _upstream_headers(source.headers)
        incoming_range = str(self.headers.get("Range") or "").strip()
        if incoming_range:
            headers["Range"] = incoming_range
        request = urllib.request.Request(
            source.video_url,
            headers=headers,
            method="HEAD" if head_only else "GET",
        )
        proxy_handler = urllib.request.ProxyHandler(
            {"http": source.proxy, "https": source.proxy} if source.proxy.startswith(("http://", "https://")) else None
        )
        opener = urllib.request.build_opener(proxy_handler)
        head_fallback = False
        try:
            response = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            if head_only and exc.code in {400, 403, 405}:
                request = urllib.request.Request(source.video_url, headers={**headers, "Range": "bytes=0-0"})
                response = opener.open(request, timeout=30)
                head_fallback = True
            else:
                raise
        with response:
            status = int(getattr(response, "status", 200) or 200)
            if head_fallback:
                status = 200
            if incoming_range and status == 200:
                status = 206 if response.headers.get("Content-Range") else 200
            self.send_response(status)
            self.send_header("Content-Type", response.headers.get("Content-Type") or source.mime_type)
            content_length = response.headers.get("Content-Length")
            content_range = response.headers.get("Content-Range")
            if head_fallback and content_range and "/" in content_range:
                content_length = content_range.rsplit("/", 1)[-1]
                content_range = ""
            if content_length:
                self.send_header("Content-Length", content_length)
            if content_range:
                self.send_header("Content-Range", content_range)
            for name in ("ETag", "Last-Modified"):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges") or "bytes")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("Connection", "close")
            self.end_headers()
            if head_only:
                return
            while chunk := response.read(256 * 1024):
                self.wfile.write(chunk)

    def _serve_file(self, source: DlnaMediaSource, head_only: bool) -> None:
        path = Path(source.file_path)
        if not path.is_file():
            self.send_error(404)
            return
        file_size = path.stat().st_size
        range_header = str(self.headers.get("Range") or "").strip()
        range_result = _parse_range_header(range_header, file_size)
        if range_result is None:
            self.send_error(416)
            return
        start, end, partial = range_result
        content_length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", source.output_mime_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("Connection", "close")
        self.end_headers()
        if head_only:
            return
        with path.open("rb") as file:
            file.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = file.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _serve_muxed(self, owner: DlnaMediaServer, source: DlnaMediaSource, head_only: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", source.output_mime_type)
        self.send_header("Accept-Ranges", "none")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("Connection", "close")
        self.end_headers()
        if head_only:
            return
        command = build_ffmpeg_mux_command(source)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        owner.track_process(process)
        try:
            if process.stdout is None:
                raise RuntimeError("FFmpeg 没有可读输出")
            while chunk := process.stdout.read(64 * 1024):
                self.wfile.write(chunk)
            return_code = process.wait(timeout=5.0)
            if return_code != 0:
                detail = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
                logger.error("FFmpeg 投屏封装失败 title=%s detail=%s", source.title, detail[-1000:])
        finally:
            _terminate_process(process)
            owner.untrack_process(process)


def build_ffmpeg_mux_command(source: DlnaMediaSource) -> list[str]:
    if not source.ffmpeg_path or not Path(source.ffmpeg_path).is_file():
        raise RuntimeError("分离音视频投屏需要可用的 FFmpeg")
    if not source.audio_url:
        raise RuntimeError("缺少投屏音频流")
    command = [source.ffmpeg_path, "-hide_banner", "-loglevel", "error"]
    input_options = _ffmpeg_input_options(source)
    command.extend(input_options)
    if source.start_position > 0:
        command.extend(["-ss", f"{source.start_position:.3f}"])
    command.extend(["-i", source.video_url])
    command.extend(input_options)
    if source.start_position > 0:
        command.extend(["-ss", f"{source.start_position:.3f}"])
    command.extend(["-i", source.audio_url, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"])
    audio_codec = str(source.audio_codec or "").lower()
    if any(codec in audio_codec for codec in ("aac", "mp4a")):
        command.extend(["-c:a", "copy"])
    else:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-mpegts_flags", "+resend_headers", "-f", "mpegts", "pipe:1"])
    return command


def local_ip_for_remote(remote_host: str) -> str:
    if not remote_host:
        raise RuntimeError("DLNA 设备地址无效")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_host, 9))
        local_ip = str(sock.getsockname()[0])
    finally:
        sock.close()
    if local_ip.startswith(("127.", "169.254.")):
        raise RuntimeError(f"无法为 DLNA 设备选择局域网网卡，本机地址为 {local_ip}")
    return local_ip


def mime_type_for_extension(extension: str) -> str:
    return {
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "mkv": "video/x-matroska",
        "webm": "video/webm",
        "ts": "video/mp2t",
        "m2ts": "video/mp2t",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "wmv": "video/x-ms-wmv",
        "flv": "video/x-flv",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "wma": "audio/x-ms-wma",
    }.get(str(extension or "").lower().lstrip("."), "video/mp4")


def mime_type_for_file(path: str | Path) -> str:
    extension = Path(path).suffix.lstrip(".")
    mapped = mime_type_for_extension(extension)
    if mapped != "video/mp4" or extension.lower() in {"mp4", "m4v"}:
        return mapped
    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed and (guessed.startswith("video/") or guessed.startswith("audio/")):
        return guessed
    return mapped


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int, bool] | None:
    if file_size <= 0:
        return (0, -1, False)
    if not range_header:
        return (0, file_size - 1, False)
    if not range_header.startswith("bytes=") or "," in range_header:
        return None
    start_text, separator, end_text = range_header[6:].partition("-")
    if separator != "-":
        return None
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
        else:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(0, file_size - suffix_length)
            end = file_size - 1
    except ValueError:
        return None
    if start < 0 or end < start or start >= file_size:
        return None
    end = min(end, file_size - 1)
    return (start, end, True)


def _upstream_headers(headers: dict[str, str]) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }
    for name, value in headers.items():
        clean_name = str(name or "").strip()
        clean_value = str(value or "").replace("\r", "").replace("\n", "").strip()
        if clean_name and clean_value:
            result[clean_name] = clean_value
    return result


def _ffmpeg_input_options(source: DlnaMediaSource) -> list[str]:
    result: list[str] = []
    if source.proxy.startswith(("http://", "https://")):
        result.extend(["-http_proxy", source.proxy])
    header_lines = []
    for name, value in _upstream_headers(source.headers).items():
        if name.lower() in {"connection", "accept-encoding"}:
            continue
        header_lines.append(f"{name}: {value}")
    if header_lines:
        result.extend(["-headers", "\r\n".join(header_lines) + "\r\n"])
    return result


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
