from __future__ import annotations

import logging
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from download.command_builder import (
    build_download_command,
    should_retry_with_cookie_file,
)
from download.models import DownloadTask
from services.config_service import ConfigService
from services.logging_service import sanitize_command


logger = logging.getLogger("tube_player.download")
STANDARD_PERCENT_RE = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%", re.IGNORECASE)
STANDARD_SPEED_RE = re.compile(r"\sat\s+(?P<speed>\S+)", re.IGNORECASE)
STANDARD_ETA_RE = re.compile(r"\sETA\s+(?P<eta>\S+)", re.IGNORECASE)


class DownloadWorkerSignals(QObject):
    started = Signal(str)
    progress = Signal(str, float, str, str)
    completed = Signal(str, str)
    failed = Signal(str, str)
    stopped = Signal(str)


class DownloadWorker(QRunnable):
    def __init__(self, task: DownloadTask, config: ConfigService) -> None:
        super().__init__()
        self.task = task
        self.config = config
        self.signals = DownloadWorkerSignals()
        self._process: subprocess.Popen[str] | None = None
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()
        process = self._process
        if process and process.poll() is None:
            logger.info("terminating download task_id=%s title=%s", self.task.task_id, self.task.title)
            try:
                process.terminate()
            except OSError:
                pass

    @Slot()
    def run(self) -> None:
        self.signals.started.emit(self.task.task_id)
        output = self._run_once(force_cookie_file=False)
        if (
            output.returncode != 0
            and not self._stop_requested.is_set()
            and self.config.cookie_file()
            and should_retry_with_cookie_file(output.text)
        ):
            logger.warning(
                "download browser cookie extraction failed; retrying with configured cookie file task_id=%s",
                self.task.task_id,
            )
            output = self._run_once(force_cookie_file=True)

        if self._stop_requested.is_set():
            self.signals.stopped.emit(self.task.task_id)
        elif output.succeeded():
            self.signals.completed.emit(self.task.task_id, output.output_path)
        else:
            self.signals.failed.emit(self.task.task_id, output.error_message())

    def _run_once(self, force_cookie_file: bool) -> "_DownloadOutput":
        command = build_download_command(self.task, self.config, force_cookie_file=force_cookie_file)
        logger.info(
            "download start task_id=%s attempt=%s title=%s",
            self.task.task_id,
            "cookie-file" if force_cookie_file else "primary",
            self.task.title,
        )
        logger.debug("download command task_id=%s command=%s", self.task.task_id, sanitize_command(command))

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        started = time.perf_counter()
        lines: list[str] = []
        output_path = ""

        self._process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )

        output_queue: Queue[str] = Queue()
        reader = threading.Thread(
            target=_read_output_lines,
            args=(self._process, output_queue),
            daemon=True,
        )
        reader.start()
        estimator = _FileProgressEstimator(self.task)
        next_file_poll = 0.0

        while True:
            output_path = self._drain_output_lines(output_queue, lines, output_path)
            now = time.perf_counter()
            if now >= next_file_poll:
                self._emit_file_progress(estimator, now)
                next_file_poll = now + 1.0
            if self._process.poll() is not None:
                break
            time.sleep(0.1)

        reader.join(timeout=1.0)
        output_path = self._drain_output_lines(output_queue, lines, output_path)

        returncode = self._process.wait()
        if not output_path or not Path(output_path).exists():
            resolved_output_path = _find_downloaded_file(self.task)
            if resolved_output_path:
                if output_path:
                    logger.warning(
                        "download output path does not exist; using resolved file task_id=%s output_path=%s resolved=%s",
                        self.task.task_id,
                        output_path,
                        resolved_output_path,
                    )
                output_path = resolved_output_path
        elapsed = time.perf_counter() - started
        logger.info(
            "download finished task_id=%s returncode=%s elapsed=%.2fs output_path=%s",
            self.task.task_id,
            returncode,
            elapsed,
            output_path,
        )
        self._process = None
        return _DownloadOutput(returncode=returncode, text="\n".join(lines), output_path=output_path)

    def _drain_output_lines(self, output_queue: Queue[str], lines: list[str], output_path: str) -> str:
        while True:
            try:
                line = output_queue.get_nowait()
            except Empty:
                return output_path
            if line:
                output_path = self._handle_output_line(line, lines, output_path)

    def _emit_file_progress(self, estimator: "_FileProgressEstimator", now: float) -> None:
        snapshot = estimator.poll(now)
        if not snapshot:
            return
        percent, speed, eta, current_bytes = snapshot
        logger.debug(
            "download file progress task_id=%s percent=%.1f speed=%s eta=%s bytes=%s expected=%s",
            self.task.task_id,
            percent,
            speed,
            eta,
            current_bytes,
            self.task.expected_bytes or 0,
        )
        self.signals.progress.emit(self.task.task_id, percent, speed, eta)

    def _handle_output_line(self, line: str, lines: list[str], output_path: str) -> str:
        lines.append(line)
        if len(lines) > 80:
            del lines[:-80]
        parsed = _parse_progress(line)
        if parsed:
            percent, speed, eta = parsed
            logger.debug(
                "download progress task_id=%s percent=%.1f speed=%s eta=%s",
                self.task.task_id,
                percent,
                speed,
                eta,
            )
            self.task.progress = percent
            self.task.speed_text = speed
            self.task.eta_text = eta
            self.signals.progress.emit(self.task.task_id, percent, speed, eta)
        elif line.startswith("filepath:"):
            output_path = line.split(":", 1)[1].strip()
        else:
            logger.debug("download output task_id=%s: %s", self.task.task_id, line)
        return output_path


class _DownloadOutput:
    def __init__(self, returncode: int, text: str, output_path: str) -> None:
        self.returncode = returncode
        self.text = text
        self.output_path = output_path

    def succeeded(self) -> bool:
        return self.returncode == 0 or bool(self.output_path and Path(self.output_path).exists())

    def error_message(self) -> str:
        detail = self.text.strip()
        return detail or f"yt-dlp 下载失败，退出码 {self.returncode}"


class _FileProgressEstimator:
    def __init__(self, task: DownloadTask) -> None:
        self.task = task
        self.last_bytes: int | None = None
        self.last_time: float | None = None
        self.last_percent = 0.0

    def poll(self, now: float) -> tuple[float, str, str, int] | None:
        current_bytes = _downloaded_bytes(self.task)
        if current_bytes <= 0:
            return None

        speed_bps = 0.0
        if self.last_bytes is not None and self.last_time is not None:
            elapsed = max(0.001, now - self.last_time)
            speed_bps = max(0.0, (current_bytes - self.last_bytes) / elapsed)

        self.last_bytes = current_bytes
        self.last_time = now

        expected = self.task.expected_bytes or 0
        if expected > 0:
            highest_percent = max(self.last_percent, self.task.progress, current_bytes * 100 / expected)
            percent = 100.0 if highest_percent >= 100.0 else min(99.5, highest_percent)
        else:
            percent = max(self.last_percent, self.task.progress)

        speed_text = _format_speed(speed_bps) if speed_bps > 0 else self.task.speed_text
        eta_text = (
            _format_eta((expected - current_bytes) / speed_bps)
            if expected > current_bytes and speed_bps > 0
            else self.task.eta_text
        )
        if not speed_text and expected <= 0:
            return None

        self.last_percent = percent
        self.task.progress = percent
        self.task.speed_text = speed_text
        self.task.eta_text = eta_text
        return percent, speed_text, eta_text, current_bytes


def _read_output_lines(process: subprocess.Popen[str], output_queue: Queue[str]) -> None:
    stdout = process.stdout
    if stdout is None:
        return

    buffer = ""
    while True:
        chunk = stdout.read(1)
        if chunk == "":
            break
        if chunk in ("\r", "\n"):
            line = buffer.strip()
            buffer = ""
            if line:
                output_queue.put(line)
            continue
        buffer += chunk
        if len(buffer) > 4096:
            output_queue.put(buffer.strip())
            buffer = ""

    if buffer.strip():
        output_queue.put(buffer.strip())


def _downloaded_bytes(task: DownloadTask) -> int:
    save_dir = Path(task.save_dir)
    if not task.video_id or not save_dir.exists():
        return 0

    total = 0
    try:
        for path in save_dir.iterdir():
            if not path.is_file() or task.video_id not in path.name:
                continue
            total += path.stat().st_size
    except OSError:
        return 0
    return total


def _find_downloaded_file(task: DownloadTask) -> str:
    save_dir = Path(task.save_dir)
    if not task.video_id or not save_dir.exists():
        return ""

    transient_suffixes = (".part", ".ytdl", ".tmp", ".temp")
    try:
        for path in save_dir.iterdir():
            lower_name = path.name.lower()
            if not path.is_file() or task.video_id not in path.name:
                continue
            if lower_name.endswith(transient_suffixes):
                continue
            return str(path)
    except OSError:
        return ""
    return ""


def _format_speed(bytes_per_second: float) -> str:
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    value = max(0.0, bytes_per_second)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B/s":
                return f"{value:.0f}{unit}"
            return f"{value:.2f}{unit}"
        value /= 1024
    return ""


def _format_eta(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_progress(line: str) -> tuple[float, str, str] | None:
    normalized = line.strip()
    for prefix in ("progress:", "download:progress:"):
        if normalized.startswith(prefix):
            payload = normalized.removeprefix(prefix)
            parts = payload.split("|")
            if len(parts) < 3:
                return None
            return _progress_tuple(parts[0], parts[1], parts[2])

    percent_match = STANDARD_PERCENT_RE.search(normalized)
    if not percent_match:
        return None
    speed_match = STANDARD_SPEED_RE.search(normalized)
    eta_match = STANDARD_ETA_RE.search(normalized)
    return _progress_tuple(
        percent_match.group("percent") or "0",
        speed_match.group("speed") if speed_match else "",
        eta_match.group("eta") if eta_match else "",
    )


def _progress_tuple(percent_text: str, speed: str, eta: str) -> tuple[float, str, str]:
    percent_text = percent_text.strip().replace("%", "")
    try:
        percent = float(percent_text)
    except ValueError:
        percent = 0.0
    return max(0.0, min(100.0, percent)), speed.strip(), eta.strip()
