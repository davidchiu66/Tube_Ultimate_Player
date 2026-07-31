from __future__ import annotations

import io
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dlna.media_server import DlnaMediaSource, _DlnaRequestHandler


STDOUT_BYTES = 512 * 1024
STDERR_BYTES = 2 * 1024 * 1024

NOISY_CHILD = (
    "import sys;"
    f"sys.stderr.buffer.write(b'e' * {STDERR_BYTES});"
    "sys.stderr.buffer.flush();"
    f"sys.stdout.buffer.write(b'v' * {STDOUT_BYTES});"
    "sys.stdout.buffer.flush()"
)


class _FakeHandler(_DlnaRequestHandler):
    def __init__(self) -> None:  # noqa: D107 - 绕过 socket 初始化，仅测试流转发逻辑
        self.wfile = io.BytesIO()
        self.sent_headers: list[tuple[str, str]] = []
        self.status = 0

    def send_response(self, code, message=None) -> None:  # noqa: N802
        self.status = code

    def send_header(self, keyword, value) -> None:  # noqa: N802
        self.sent_headers.append((keyword, value))

    def end_headers(self) -> None:  # noqa: N802
        return None

    def log_message(self, *_args) -> None:
        return None


class MuxedStreamTests(unittest.TestCase):
    """FFmpeg 的 stderr 不能走管道：只读 stdout 时管道写满会让子进程阻塞，投屏卡死。"""

    def test_large_stderr_output_does_not_deadlock(self) -> None:
        handler = _FakeHandler()
        source = DlnaMediaSource(
            title="test",
            video_url="https://example.invalid/video",
            audio_url="https://example.invalid/audio",
            ffmpeg_path=sys.executable,
        )
        owner = SimpleNamespace(track_process=lambda _p: None, untrack_process=lambda _p: None)
        command = [sys.executable, "-c", NOISY_CHILD]
        error: list[BaseException] = []

        def run() -> None:
            try:
                with patch("dlna.media_server.build_ffmpeg_mux_command", return_value=command):
                    handler._serve_muxed(owner, source, head_only=False)
            except BaseException as exc:  # noqa: BLE001 - 测试线程内需要记录任何异常
                error.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=60)

        self.assertFalse(worker.is_alive(), "转发大量 stderr 时发生死锁")
        self.assertEqual(error, [])
        self.assertEqual(len(handler.wfile.getvalue()), STDOUT_BYTES)
        self.assertEqual(handler.status, 200)


if __name__ == "__main__":
    unittest.main()
