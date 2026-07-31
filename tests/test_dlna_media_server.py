from __future__ import annotations

import io
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dlna.media_server import (
    DlnaMediaSource,
    _DlnaRequestHandler,
    _summarize_ffmpeg_stderr,
    build_ffmpeg_mux_command,
)


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


class StderrSummaryTests(unittest.TestCase):
    """-progress 的进度行不能把真正的告警挤出日志尾部。"""

    def _summarize(self, text: str):
        buffer = io.BytesIO(text.encode("utf-8"))
        return _summarize_ffmpeg_stderr(buffer)

    def test_last_out_time_wins_and_progress_lines_are_dropped(self) -> None:
        payload = (
            "[https @ 1] Will reconnect at 1048576\n"
            "frame=100\nout_time=00:00:10.000000\nprogress=continue\n"
            "[mpegts @ 2] Non-monotonous DTS in output stream\n"
            "frame=200\nout_time=00:03:20.000000\nprogress=end\n"
        )

        out_time, detail = self._summarize(payload)

        self.assertEqual(out_time, "00:03:20.000000")
        self.assertIn("Will reconnect", detail)
        self.assertIn("Non-monotonous DTS", detail)
        self.assertNotIn("frame=", detail)
        self.assertNotIn("progress=", detail)

    def test_empty_stderr_is_tolerated(self) -> None:
        self.assertEqual(self._summarize(""), ("", ""))

    def test_progress_only_stderr_has_no_detail(self) -> None:
        out_time, detail = self._summarize("out_time=00:00:05.000000\nprogress=continue\n")

        self.assertEqual(out_time, "00:00:05.000000")
        self.assertEqual(detail, "")


class MuxOutcomeTests(unittest.TestCase):
    """三种收尾原因必须分别记账，否则排查中断时无法判断是哪一侧先松手。"""

    def _run_serve(self, handler, child_code: str) -> list[str]:
        source = DlnaMediaSource(
            title="outcome",
            video_url="https://example.invalid/video",
            audio_url="https://example.invalid/audio",
            ffmpeg_path=sys.executable,
        )
        owner = SimpleNamespace(track_process=lambda _p: None, untrack_process=lambda _p: None)
        command = [sys.executable, "-c", child_code]
        with patch("dlna.media_server.build_ffmpeg_mux_command", return_value=command):
            with self.assertLogs("tube_player.dlna.http", level="INFO") as captured:
                handler._serve_muxed(owner, source, head_only=False)
        return captured.output

    def test_normal_end_is_reported_as_ffmpeg_eof(self) -> None:
        handler = _FakeHandler()

        output = self._run_serve(handler, "import sys;sys.stdout.buffer.write(b'v' * 1024)")

        summary = "\n".join(output)
        self.assertIn("outcome=ffmpeg_eof", summary)
        self.assertIn("bytes=1024", summary)
        self.assertIn("exit=0", summary)

    def test_non_zero_exit_is_reported_as_ffmpeg_error(self) -> None:
        handler = _FakeHandler()

        output = self._run_serve(
            handler,
            "import sys;sys.stderr.write('boom\\n');sys.exit(3)",
        )

        summary = "\n".join(output)
        self.assertIn("outcome=ffmpeg_error", summary)
        self.assertIn("exit=3", summary)
        self.assertIn("boom", summary)

    def test_client_disconnect_is_reported_and_not_raised(self) -> None:
        class BrokenHandler(_FakeHandler):
            def __init__(self) -> None:
                super().__init__()
                self.wfile = SimpleNamespace(write=self._explode)

            @staticmethod
            def _explode(_chunk) -> None:
                raise BrokenPipeError("client gone")

        handler = BrokenHandler()

        output = self._run_serve(handler, "import sys;sys.stdout.buffer.write(b'v' * 1024)")

        self.assertIn("outcome=client_disconnected", "\n".join(output))


class MuxCommandObservabilityTests(unittest.TestCase):
    def test_command_asks_ffmpeg_for_warnings_and_progress(self) -> None:
        source = DlnaMediaSource(
            title="flags",
            video_url="https://example.invalid/video",
            audio_url="https://example.invalid/audio",
            ffmpeg_path=sys.executable,
        )

        command = build_ffmpeg_mux_command(source)

        self.assertIn("-progress", command)
        self.assertEqual(command[command.index("-progress") + 1], "pipe:2")
        self.assertEqual(command[command.index("-loglevel") + 1], "warning")


if __name__ == "__main__":
    unittest.main()
