from __future__ import annotations

import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dlna.media_server import (
    DlnaMediaSource,
    _DlnaRequestHandler,
    _range_start,
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


class InputResilienceTests(unittest.TestCase):
    """两路输入都要带重连与超时，且必须落在各自的 -i 之前才是 per-input 选项。"""

    def _command(self, audio_codec: str = "mp4a.40.2") -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg = Path(temp_dir) / "ffmpeg"
            ffmpeg.touch()
            source = DlnaMediaSource(
                title="resilience",
                video_url="https://cdn/video.m4s",
                audio_url="https://cdn/audio.m4s",
                headers={"Referer": "https://www.bilibili.com/"},
                audio_codec=audio_codec,
                ffmpeg_path=str(ffmpeg),
            )
            return build_ffmpeg_mux_command(source)

    def test_both_inputs_carry_reconnect_options(self) -> None:
        command = self._command()

        self.assertEqual(command.count("-reconnect"), 2)
        self.assertEqual(command.count("-reconnect_streamed"), 2)
        self.assertEqual(command.count("-reconnect_on_network_error"), 2)
        self.assertEqual(command.count("-rw_timeout"), 2)

    def test_reconnect_options_precede_their_own_input(self) -> None:
        command = self._command()
        input_positions = [index for index, value in enumerate(command) if value == "-i"]
        reconnect_positions = [index for index, value in enumerate(command) if value == "-reconnect"]

        self.assertEqual(len(input_positions), 2)
        self.assertEqual(len(reconnect_positions), 2)
        # 第 n 个 -reconnect 必须出现在第 n 个 -i 之前，且不早于上一个 -i。
        self.assertLess(reconnect_positions[0], input_positions[0])
        self.assertLess(input_positions[0], reconnect_positions[1])
        self.assertLess(reconnect_positions[1], input_positions[1])

    def test_aac_source_still_copies_audio(self) -> None:
        command = self._command("mp4a.40.2")

        self.assertEqual(command[command.index("-c:a") + 1], "copy")
        self.assertNotIn("-af", command)

    def test_non_aac_source_transcodes_with_resample_sync(self) -> None:
        command = self._command("opus")

        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertIn("-af", command)
        self.assertIn("aresample=async=1:first_pts=0", command)

    def test_muxing_queue_guard_is_present(self) -> None:
        command = self._command()

        self.assertEqual(command[command.index("-max_muxing_queue_size") + 1], "4096")
        self.assertIn("+resend_headers", command)
        self.assertEqual(command[-1], "pipe:1")


class ContentFeatureHeaderTests(unittest.TestCase):
    """三条 serve 路径都要发 contentFeatures.dlna.org，OP 位按可 Range 性区分。"""

    def test_muxed_stream_is_advertised_as_non_seekable(self) -> None:
        handler = _FakeHandler()
        source = DlnaMediaSource(
            title="features",
            video_url="https://example.invalid/video",
            audio_url="https://example.invalid/audio",
            ffmpeg_path=sys.executable,
        )
        owner = SimpleNamespace(track_process=lambda _p: None, untrack_process=lambda _p: None)

        with patch("dlna.media_server.build_ffmpeg_mux_command", return_value=[sys.executable, "-c", ""]):
            handler._serve_muxed(owner, source, head_only=True)

        headers = dict(handler.sent_headers)
        self.assertIn("DLNA.ORG_OP=00", headers["contentFeatures.dlna.org"])

    def test_local_file_is_advertised_as_seekable(self) -> None:
        handler = _FakeHandler()
        handler.headers = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "clip.mp4"
            media.write_bytes(b"0123456789")
            source = DlnaMediaSource(title="local", video_url="", file_path=str(media))

            handler._serve_file(source, head_only=True)

        headers = dict(handler.sent_headers)
        self.assertIn("DLNA.ORG_OP=01", headers["contentFeatures.dlna.org"])


class RangeStartTests(unittest.TestCase):
    def test_request_and_response_range_forms_are_parsed(self) -> None:
        self.assertEqual(_range_start("bytes=100-"), 100)
        self.assertEqual(_range_start("bytes 200-299/1000"), 200)
        self.assertEqual(_range_start("bytes=-500"), 0)
        self.assertEqual(_range_start(""), 0)
        self.assertEqual(_range_start("items=1-2"), 0)


class ProxyResumeTests(unittest.TestCase):
    """单文件直投：上游中途报错要按已发字节续传，最终字节流必须与不中断时一致。"""

    PAYLOAD = bytes(range(256)) * 8

    class _FlakyResponse:
        """读到 fail_after 字节后抛一次 OSError，模拟上游连接被回收。"""

        def __init__(self, payload: bytes, fail_after: int | None) -> None:
            self.payload = payload
            self.fail_after = fail_after
            self.served = 0

        def read(self, size: int) -> bytes:
            if self.fail_after is not None and self.served >= self.fail_after:
                raise OSError("upstream reset")
            end = self.served + size
            if self.fail_after is not None:
                end = min(end, self.fail_after)
            chunk = self.payload[self.served:end]
            self.served = end
            return chunk

    def _run(self, fail_after: int | None, *, attempts_available: int = 3):
        handler = _FakeHandler()
        opened: list[str] = []
        first = self._FlakyResponse(self.PAYLOAD, fail_after)

        def fake_open(request, timeout=None):
            value = request.headers.get("Range") or request.headers.get("range") or ""
            opened.append(value)
            start = _range_start(value)
            remaining = attempts_available - len(opened)
            return self._FlakyResponse(self.PAYLOAD[start:], None if remaining >= 0 else 0)

        opener = SimpleNamespace(open=fake_open)
        source = DlnaMediaSource(title="resume", video_url="https://cdn/movie.mp4")
        with patch("dlna.media_server.time.sleep", lambda _seconds: None):
            handler._forward_with_resume(
                first,
                opener=opener,
                source=source,
                headers={"User-Agent": "test"},
                base_offset=0,
            )
        return handler, opened

    def test_uninterrupted_stream_needs_no_retry(self) -> None:
        handler, opened = self._run(None)

        self.assertEqual(handler.wfile.getvalue(), self.PAYLOAD)
        self.assertEqual(opened, [])

    def test_interrupted_stream_resumes_from_sent_offset(self) -> None:
        handler, opened = self._run(600)

        self.assertEqual(handler.wfile.getvalue(), self.PAYLOAD)
        self.assertEqual(opened, ["bytes=600-"])

    def test_resume_gives_up_after_the_attempt_limit(self) -> None:
        with self.assertRaises(OSError):
            self._run(100, attempts_available=0)


if __name__ == "__main__":
    unittest.main()
