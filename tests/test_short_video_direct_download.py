from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from download.command_builder import direct_download_output_path
from download.download_worker import DownloadWorker
from download.models import DownloadTask
from resolver.models import HomeVideo, VideoInfo, VideoQuality
from services.logging_service import sanitize_command


class _RangeHandler(BaseHTTPRequestHandler):
    payload = (b"short-video-data-" * 65536)
    requests: list[dict[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        headers = {key: value for key, value in self.headers.items()}
        type(self).requests.append(headers)
        range_header = str(self.headers.get("Range") or "")
        start = 0
        if range_header.startswith("bytes="):
            start = int(range_header[6:].split("-", 1)[0] or 0)
        body = self.payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        pass


class ShortVideoDirectDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        _RangeHandler.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = SimpleNamespace(
            effective_proxy=lambda: ("", ""),
            cookie_browser_for_site=lambda _site: "firefox:test",
            cookie_file_for_url=lambda _url: "",
            download_dir=lambda: self.temp.name,
        )

    def _task(self) -> DownloadTask:
        return DownloadTask(
            url="https://www.douyin.com/video/123",
            download_url=f"http://127.0.0.1:{self.server.server_address[1]}/media",
            title="direct clip",
            video_id="douyin:123",
            source_site="douyin",
            save_dir=self.temp.name,
            http_headers={"User-Agent": "test-agent"},
        )

    def test_direct_download_skips_webpage_extractor_and_adds_runtime_headers(self) -> None:
        task = self._task()
        worker = DownloadWorker(task, self.config)
        with patch("download.download_worker.load_browser_cookie_header", return_value="sessionid=secret"):
            result = worker._run_direct_media()

        output = Path(result.output_path)
        self.assertTrue(result.succeeded())
        self.assertEqual(output.read_bytes(), _RangeHandler.payload)
        request = _RangeHandler.requests[0]
        self.assertEqual(request.get("Referer"), task.url)
        self.assertEqual(request.get("Origin"), "https://www.douyin.com")
        self.assertEqual(request.get("Cookie"), "sessionid=secret")
        self.assertEqual(request.get("User-Agent"), "test-agent")
        self.assertNotIn("secret", str(task.to_dict()))

    def test_direct_download_resumes_a_part_file_with_range(self) -> None:
        task = self._task()
        part_path = direct_download_output_path(task).with_suffix(".mp4.part")
        part_path.parent.mkdir(parents=True, exist_ok=True)
        partial = _RangeHandler.payload[:12345]
        part_path.write_bytes(partial)

        worker = DownloadWorker(task, self.config)
        with patch("download.download_worker.load_browser_cookie_header", return_value=""):
            result = worker._run_direct_media()

        self.assertTrue(result.succeeded())
        self.assertEqual(Path(result.output_path).read_bytes(), _RangeHandler.payload)
        self.assertEqual(_RangeHandler.requests[0].get("Range"), f"bytes={len(partial)}-")

    def test_legacy_short_video_task_is_refreshed_by_exact_video_id(self) -> None:
        task = DownloadTask(
            url="https://www.tiktok.com/@u/video/456",
            title="legacy clip",
            video_id="tiktok:456",
            source_site="tiktok",
            quality_label="576p",
            format_selector="tiktok-old-format/best",
            save_dir=self.temp.name,
        )
        card = HomeVideo(
            video_id="tiktok:456", title="legacy clip", source_site="tiktok",
            webpage_url=task.url,
        )
        video = VideoInfo(
            video_id="tiktok:456", title="legacy clip", source_site="tiktok", webpage_url=task.url,
            qualities={
                "576p": VideoQuality(
                    label="576p", height=1024, width=576, fps=30, vcodec="h264", acodec="aac",
                    ext="mp4", format_id="current", video_url=self._task().download_url,
                ),
            },
        )
        search_queries: list[str] = []

        def search(query: str, *_args, **_kwargs):
            search_queries.append(query)
            if len(search_queries) == 1:
                raise RuntimeError("title search blocked")
            return [card], False

        resolver = SimpleNamespace(search_videos=search, resolve=lambda _url: video)
        worker = DownloadWorker(task, self.config)
        with patch("download.download_worker.SiteResolver", return_value=resolver):
            error = worker._refresh_legacy_short_video_task()

        self.assertEqual(error, "")
        self.assertEqual(task.download_url, video.qualities["576p"].video_url)
        self.assertEqual(task.format_selector, "best")
        self.assertEqual(search_queries, ["legacy clip", "456"])

    def test_signed_media_url_is_redacted_from_command_logs(self) -> None:
        command = sanitize_command([
            "yt-dlp",
            "https://www.tiktok.com/aweme/v1/play/?item_id=1&signature=secret",
        ])

        self.assertEqual(command, ["yt-dlp", "https://www.tiktok.com/<media-url>"])
        self.assertNotIn("secret", str(command))


if __name__ == "__main__":
    unittest.main()
