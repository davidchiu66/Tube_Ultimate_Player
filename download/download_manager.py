from __future__ import annotations

import logging
import json
from pathlib import Path
import re

from PySide6.QtCore import QObject, QThreadPool, Signal

from app_paths import DATA_DIR
from download.command_builder import build_download_task
from download.download_worker import DownloadWorker
from download.models import (
    STATUS_COMPLETED,
    STATUS_DELETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_QUEUED,
    DownloadTask,
)
from resolver.models import VideoInfo
from services.config_service import ConfigService


logger = logging.getLogger("tube_player.download")
TASKS_FILE = DATA_DIR / "download_tasks.json"
VIDEO_ID_IN_FILENAME_RE = re.compile(r"\[(?P<video_id>[0-9A-Za-z_-]{11})\]")


class DownloadManager(QObject):
    task_added = Signal(object)
    task_changed = Signal(object)
    task_removed = Signal(str)
    task_completed = Signal(object)
    message = Signal(str)

    def __init__(self, config: ConfigService, thread_pool: QThreadPool) -> None:
        super().__init__()
        self.config = config
        self.thread_pool = thread_pool
        self._tasks: list[DownloadTask] = []
        self._workers: dict[str, DownloadWorker] = {}
        self._max_concurrent = self.config.download_max_concurrent()
        self._load_tasks()
        self._import_completed_files()

    def tasks(self) -> list[DownloadTask]:
        return list(self._tasks)

    def enqueue(self, video: VideoInfo, quality_label: str) -> DownloadTask | None:
        url = video.webpage_url
        existing = self._find_by_url(url)
        if existing:
            if existing.status == STATUS_COMPLETED:
                self.message.emit(f"已完成下载：{existing.title}")
            else:
                self.message.emit(f"下载任务已存在：{existing.title}")
            return existing

        task = build_download_task(video, quality_label, self.config)
        self._tasks.append(task)
        logger.info(
            "download queued task_id=%s title=%s quality=%s format=%s",
            task.task_id,
            task.title,
            task.quality_label,
            task.format_selector,
        )
        self.task_added.emit(task)
        self.message.emit(f"已加入下载队列：{task.title}")
        self._save_tasks()
        self._schedule()
        return task

    def pause_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if not task or task.status not in (STATUS_QUEUED, STATUS_DOWNLOADING):
            return

        logger.info("download pause requested task_id=%s title=%s", task.task_id, task.title)
        task.status = STATUS_PAUSED
        task.touch()
        self.task_changed.emit(task)
        self._save_tasks()

        worker = self._workers.get(task_id)
        if worker:
            worker.stop()
        self._schedule()

    def start_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if not task or task.status not in (STATUS_PAUSED, STATUS_FAILED, STATUS_QUEUED):
            return

        logger.info("download start requested task_id=%s title=%s", task.task_id, task.title)
        task.status = STATUS_QUEUED
        task.error_message = ""
        task.touch()
        self.task_changed.emit(task)
        self._save_tasks()
        self._schedule()

    def delete_task(self, task_id: str) -> None:
        task = self._find(task_id)
        if not task:
            return

        logger.info("download delete requested task_id=%s title=%s", task.task_id, task.title)
        task.status = STATUS_DELETED
        worker = self._workers.pop(task_id, None)
        if worker:
            worker.stop()
        self._tasks = [item for item in self._tasks if item.task_id != task_id]
        self.task_removed.emit(task_id)
        self._save_tasks()
        self._schedule()

    def reload_settings(self) -> None:
        self.config.load()
        self._max_concurrent = self.config.download_max_concurrent()
        self._schedule()

    def _schedule(self) -> None:
        active = sum(1 for task in self._tasks if task.status == STATUS_DOWNLOADING)
        slots = max(0, self._max_concurrent - active)
        if slots <= 0:
            return

        for task in self._tasks:
            if slots <= 0:
                break
            if task.status != STATUS_QUEUED:
                continue
            self._start_worker(task)
            slots -= 1

    def _start_worker(self, task: DownloadTask) -> None:
        if task.task_id in self._workers:
            return
        task.save_dir = self.config.download_dir()
        task.status = STATUS_DOWNLOADING
        task.touch()
        self.task_changed.emit(task)
        self._save_tasks()

        worker = DownloadWorker(task, self.config)
        worker.signals.progress.connect(self._progress)
        worker.signals.completed.connect(self._completed)
        worker.signals.failed.connect(self._failed)
        worker.signals.stopped.connect(self._stopped)
        self._workers[task.task_id] = worker
        self.thread_pool.start(worker)

    def _progress(self, task_id: str, percent: float, speed: str, eta: str) -> None:
        task = self._find(task_id)
        if not task or task.status != STATUS_DOWNLOADING:
            return
        task.progress = percent
        task.speed_text = speed
        task.eta_text = eta
        task.touch()
        self.task_changed.emit(task)
        self._save_tasks()

    def _completed(self, task_id: str, output_path: str) -> None:
        self._workers.pop(task_id, None)
        task = self._find(task_id)
        if not task or task.status == STATUS_DELETED:
            self._schedule()
            return
        task.status = STATUS_COMPLETED
        task.progress = 100.0
        task.output_path = self._resolve_output_path(task, output_path)
        task.error_message = ""
        task.touch()
        logger.info("download completed task_id=%s path=%s", task.task_id, task.output_path)
        self.task_changed.emit(task)
        self.task_completed.emit(task)
        self.message.emit(f"下载完成：{task.title}")
        self._save_tasks()
        self._schedule()

    def _failed(self, task_id: str, message: str) -> None:
        self._workers.pop(task_id, None)
        task = self._find(task_id)
        if not task or task.status == STATUS_DELETED:
            self._schedule()
            return
        task.status = STATUS_FAILED
        task.error_message = message
        task.touch()
        logger.error("download failed task_id=%s error=%s", task.task_id, message)
        self.task_changed.emit(task)
        self.message.emit(f"下载失败：{task.title}")
        self._save_tasks()
        self._schedule()

    def _stopped(self, task_id: str) -> None:
        self._workers.pop(task_id, None)
        task = self._find(task_id)
        if task and task.status == STATUS_DOWNLOADING:
            task.status = STATUS_PAUSED
            task.touch()
            self.task_changed.emit(task)
            self._save_tasks()
        self._schedule()

    def _find(self, task_id: str) -> DownloadTask | None:
        for task in self._tasks:
            if task.task_id == task_id:
                return task
        return None

    def _find_by_url(self, url: str) -> DownloadTask | None:
        for task in self._tasks:
            if task.url == url and task.status != STATUS_DELETED:
                return task
        return None

    @staticmethod
    def _find_downloaded_file(task: DownloadTask) -> str:
        if not task.video_id or not task.save_dir:
            return ""
        save_dir = Path(task.save_dir)
        if not save_dir.exists():
            return ""
        marker = f" [{task.video_id}]"
        for path in save_dir.iterdir():
            if path.is_file() and marker in path.name and path.suffix.lower() not in (".part", ".ytdl"):
                return str(path)
        return ""

    def _resolve_output_path(self, task: DownloadTask, output_path: str) -> str:
        if output_path and Path(output_path).exists():
            return output_path

        resolved = self._find_downloaded_file(task)
        if resolved:
            if output_path:
                logger.warning(
                    "download completed path missing; resolved by video_id task_id=%s output_path=%s resolved=%s",
                    task.task_id,
                    output_path,
                    resolved,
                )
            return resolved
        return output_path

    def _load_tasks(self) -> None:
        if not TASKS_FILE.exists():
            return
        try:
            raw_tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("failed to load download tasks file=%s", TASKS_FILE)
            return
        if not isinstance(raw_tasks, list):
            logger.warning("download tasks file has invalid shape file=%s", TASKS_FILE)
            return

        tasks: list[DownloadTask] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            try:
                task = DownloadTask.from_dict(item)
            except TypeError:
                logger.exception("failed to parse persisted download task: %s", item)
                continue
            if task.status == STATUS_DELETED:
                continue
            if task.status in (STATUS_DOWNLOADING, STATUS_QUEUED):
                task.status = STATUS_PAUSED
                task.speed_text = ""
                task.eta_text = ""
                task.touch()
            if task.status == STATUS_COMPLETED:
                task.output_path = self._resolve_output_path(task, task.output_path)
            tasks.append(task)

        self._tasks = tasks
        logger.info("download tasks loaded count=%s file=%s", len(self._tasks), TASKS_FILE)
        self._save_tasks()

    def _import_completed_files(self) -> None:
        save_dir = Path(self.config.download_dir())
        if not save_dir.exists():
            return

        existing_ids = {task.video_id for task in self._tasks if task.video_id}
        imported = 0
        transient_suffixes = (".part", ".ytdl", ".tmp", ".temp")
        try:
            paths = list(save_dir.iterdir())
        except OSError:
            logger.exception("failed to scan download directory dir=%s", save_dir)
            return

        for path in paths:
            if not path.is_file() or path.name.lower().endswith(transient_suffixes):
                continue
            match = VIDEO_ID_IN_FILENAME_RE.search(path.stem)
            if not match:
                continue
            video_id = match.group("video_id")
            if video_id in existing_ids:
                continue
            title = path.stem[: match.start()].strip() or path.stem
            task = DownloadTask(
                url=f"https://www.youtube.com/watch?v={video_id}",
                video_id=video_id,
                title=title,
                quality_label="Local",
                save_dir=str(save_dir),
                status=STATUS_COMPLETED,
                progress=100.0,
                output_path=str(path),
            )
            self._tasks.append(task)
            existing_ids.add(video_id)
            imported += 1

        if imported:
            logger.info("download completed files imported count=%s dir=%s", imported, save_dir)
            self._save_tasks()

    def _save_tasks(self) -> None:
        try:
            TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = [task.to_dict() for task in self._tasks if task.status != STATUS_DELETED]
            TASKS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("failed to save download tasks file=%s", TASKS_FILE)
