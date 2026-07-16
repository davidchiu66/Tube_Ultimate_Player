from __future__ import annotations

import logging
import json
from pathlib import Path
import re

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

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
from resolver.source_utils import detect_source_site
from services.config_service import ConfigService


logger = logging.getLogger("tube_player.download")
TASKS_FILE = DATA_DIR / "download_tasks.json"
VIDEO_ID_IN_FILENAME_RE = re.compile(r"\[(?P<video_id>[0-9A-Za-z:_-]{6,128})\]")


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
        self._task_index: dict[str, DownloadTask] = {}
        self._url_index: dict[str, DownloadTask] = {}
        self._max_concurrent = self.config.download_max_concurrent()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1200)
        self._save_timer.timeout.connect(self._save_tasks)
        self._load_tasks()
        self._deduplicate_tasks()
        self._import_completed_files()
        self._deduplicate_tasks()
        self._rebuild_indexes()

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
        self._register_task(task)
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
        self._unregister_task(task)
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
        self._schedule_save()

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
        return self._task_index.get(task_id)

    def _find_by_url(self, url: str) -> DownloadTask | None:
        return self._url_index.get(url)

    @staticmethod
    def _find_downloaded_file(task: DownloadTask) -> str:
        if not task.video_id or not task.save_dir:
            return ""
        save_dir = Path(task.save_dir)
        if not save_dir.exists():
            return ""
        markers = [f" [{candidate}]" for candidate in _video_id_candidates(task.video_id)]
        for path in save_dir.iterdir():
            if not path.is_file() or path.suffix.lower() in (".part", ".ytdl"):
                continue
            if any(marker in path.name for marker in markers):
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
        self._rebuild_indexes()
        logger.info("download tasks loaded count=%s file=%s", len(self._tasks), TASKS_FILE)
        self._save_tasks()

    def _deduplicate_tasks(self) -> None:
        if len(self._tasks) < 2:
            return

        kept: list[DownloadTask] = []
        removed = 0
        for task in self._tasks:
            duplicate_index = _find_duplicate_task_index(kept, task)
            if duplicate_index < 0:
                kept.append(task)
                continue

            winner = _prefer_task(kept[duplicate_index], task)
            if winner is task:
                kept[duplicate_index] = task
            removed += 1

        if removed:
            self._tasks = kept
            self._rebuild_indexes()
            logger.info("download tasks deduplicated removed=%s remaining=%s", removed, len(self._tasks))
            self._save_tasks()

    def _import_completed_files(self) -> None:
        save_dir = Path(self.config.download_dir())
        if not save_dir.exists():
            return

        existing_ids = {_normalized_video_id(task.video_id) for task in self._tasks if task.video_id}
        existing_paths = {
            str(Path(task.output_path)).lower()
            for task in self._tasks
            if task.output_path
        }
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
            normalized_video_id = _normalized_video_id(video_id)
            normalized_path = str(path).lower()
            if normalized_video_id in existing_ids or normalized_path in existing_paths:
                continue
            title = path.stem[: match.start()].strip() or path.stem
            url = _url_from_video_id(video_id)
            task = DownloadTask(
                url=url,
                video_id=video_id,
                source_site=detect_source_site(url),
                title=title,
                quality_label="Local",
                save_dir=str(save_dir),
                status=STATUS_COMPLETED,
                progress=100.0,
                output_path=str(path),
            )
            self._tasks.append(task)
            self._register_task(task)
            existing_ids.add(normalized_video_id)
            existing_paths.add(normalized_path)
            imported += 1

        if imported:
            logger.info("download completed files imported count=%s dir=%s", imported, save_dir)
            self._save_tasks()

    def _save_tasks(self) -> None:
        try:
            self._save_timer.stop()
            TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = [task.to_dict() for task in self._tasks if task.status != STATUS_DELETED]
            temp_path = TASKS_FILE.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(TASKS_FILE)
        except OSError:
            logger.exception("failed to save download tasks file=%s", TASKS_FILE)

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def flush(self) -> None:
        if self._save_timer.isActive():
            self._save_tasks()

    def _rebuild_indexes(self) -> None:
        self._task_index = {}
        self._url_index = {}
        for task in self._tasks:
            self._register_task(task)

    def _register_task(self, task: DownloadTask) -> None:
        self._task_index[task.task_id] = task
        if task.status != STATUS_DELETED and task.url:
            self._url_index[task.url] = task

    def _unregister_task(self, task: DownloadTask) -> None:
        self._task_index.pop(task.task_id, None)
        current = self._url_index.get(task.url)
        if current is task:
            self._url_index.pop(task.url, None)


def _url_from_video_id(video_id: str) -> str:
    raw = str(video_id or "").strip()
    if raw.startswith("BV"):
        return f"https://www.bilibili.com/video/{raw}"
    if raw.startswith("av") and raw[2:].isdigit():
        return f"https://www.bilibili.com/video/{raw}"
    if raw.startswith("bilibili:BV"):
        body = raw[len("bilibili:") :]
        if ":p" in body:
            bvid, page = body.split(":p", 1)
            if page.isdigit():
                return f"https://www.bilibili.com/video/{bvid}?p={page}"
        return f"https://www.bilibili.com/video/{body}"
    if raw.startswith("bilibili:av"):
        aid = raw[len("bilibili:av") :]
        if ":p" in aid:
            aid, page = aid.split(":p", 1)
            if page.isdigit():
                return f"https://www.bilibili.com/video/av{aid}?p={page}"
        if aid.isdigit():
            return f"https://www.bilibili.com/video/av{aid}"
    return f"https://www.youtube.com/watch?v={raw}"


def _normalized_video_id(video_id: str) -> str:
    raw = str(video_id or "").strip()
    if raw.startswith("bilibili:"):
        raw = raw[len("bilibili:") :]
    if raw.startswith("BV"):
        return raw.split(":p", 1)[0]
    if raw.startswith("av"):
        return raw.split(":p", 1)[0]
    return raw


def _video_id_candidates(video_id: str) -> list[str]:
    raw = str(video_id or "").strip()
    if not raw:
        return []

    candidates: list[str] = []
    for value in (raw, _normalized_video_id(raw)):
        value = str(value).strip()
        if not value or value in candidates:
            continue
        candidates.append(value)
        if value.startswith("bilibili:"):
            stripped = value[len("bilibili:") :]
            if stripped and stripped not in candidates:
                candidates.append(stripped)
        if ":p" in value:
            trimmed = value.split(":p", 1)[0]
            if trimmed and trimmed not in candidates:
                candidates.append(trimmed)
    return candidates


def _find_duplicate_task_index(tasks: list[DownloadTask], candidate: DownloadTask) -> int:
    candidate_norm = _normalized_video_id(candidate.video_id)
    candidate_path = _normalized_existing_path(candidate.output_path)
    for index, current in enumerate(tasks):
        current_norm = _normalized_video_id(current.video_id)
        current_path = _normalized_existing_path(current.output_path)
        if candidate_norm and current_norm and candidate_norm == current_norm:
            return index
        if candidate_path and current_path and candidate_path == current_path:
            return index
    return -1


def _prefer_task(left: DownloadTask, right: DownloadTask) -> DownloadTask:
    left_score = _task_preference_score(left)
    right_score = _task_preference_score(right)
    if right_score > left_score:
        return right
    return left


def _task_preference_score(task: DownloadTask) -> tuple[int, int, int, float]:
    local_path = 1 if task.output_path and Path(task.output_path).exists() else 0
    completed = 1 if task.status == STATUS_COMPLETED else 0
    non_local_quality = 1 if task.quality_label != "Local" else 0
    progress = float(task.progress or 0.0)
    return (completed, local_path, non_local_quality, progress)


def _normalized_existing_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        if Path(raw).exists():
            return str(Path(raw).resolve()).lower()
    except OSError:
        return raw.lower()
    return raw.lower()
