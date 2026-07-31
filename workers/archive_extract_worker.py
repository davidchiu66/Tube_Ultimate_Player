from __future__ import annotations

import logging
import traceback
from pathlib import Path, PurePosixPath, PureWindowsPath

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


logger = logging.getLogger("tube_player.worker")


class ArchiveEntryRejected(RuntimeError):
    """压缩包内出现越界或危险条目时抛出。"""


class ArchiveExtractWorkerSignals(QObject):
    success = Signal(str)
    error = Signal(str)
    finished = Signal()


def validate_archive_entry(name: str, extract_dir: Path) -> Path:
    """校验单个压缩包条目，返回其解压后的绝对路径。

    拒绝绝对路径、盘符路径、UNC 路径以及任何通过 ".." 逃逸出目标目录的条目
    （即 zip-slip / 路径穿越攻击）。
    """
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise ArchiveEntryRejected("压缩包内存在空文件名条目")
    if raw.startswith("/") or raw.startswith("//"):
        raise ArchiveEntryRejected(f"压缩包内存在绝对路径条目：{name}")
    if PureWindowsPath(raw).drive or PureWindowsPath(raw).is_absolute():
        raise ArchiveEntryRejected(f"压缩包内存在绝对路径条目：{name}")
    parts = PurePosixPath(raw).parts
    if any(part == ".." for part in parts):
        raise ArchiveEntryRejected(f"压缩包内存在路径穿越条目：{name}")

    root = extract_dir.resolve()
    target = (root / Path(*parts)).resolve() if parts else root
    if target != root and not target.is_relative_to(root):
        raise ArchiveEntryRejected(f"压缩包内条目会写出目标目录：{name}")
    return target


def validate_archive_entries(names: list[str], extract_dir: Path) -> list[Path]:
    return [validate_archive_entry(name, extract_dir) for name in names]


class ArchiveExtractWorker(QRunnable):
    def __init__(
        self,
        archive_path: Path,
        extract_dir: Path,
        *,
        required_files: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.archive_path = Path(archive_path)
        self.extract_dir = Path(extract_dir)
        self.required_files = tuple(required_files or ())
        self.signals = ArchiveExtractWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("archive extract started archive=%s target=%s", self.archive_path, self.extract_dir)
            self.extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                import py7zr
            except ImportError as exc:
                raise RuntimeError("缺少 py7zr 依赖，无法解压 7z 压缩包。请先执行 pip install -r requirements.txt") from exc

            with py7zr.SevenZipFile(self.archive_path, mode="r") as archive:
                names = [str(name) for name in archive.getnames()]
                validate_archive_entries(names, self.extract_dir)
                logger.info("archive entries validated archive=%s count=%s", self.archive_path, len(names))
                archive.extractall(path=self.extract_dir)

            self._ensure_required_files()
            logger.info("archive extract completed archive=%s target=%s", self.archive_path, self.extract_dir)
            self.signals.success.emit(str(self.extract_dir))
        except Exception as exc:
            logger.exception("archive extract failed archive=%s target=%s", self.archive_path, self.extract_dir)
            detail = str(exc).strip() or traceback.format_exc()
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()

    def _ensure_required_files(self) -> None:
        """解压完成后确认关键文件确实存在，避免把空目录当成安装成功。"""
        for required in self.required_files:
            leaf = required.lower()
            if any(candidate.name.lower() == leaf for candidate in self.extract_dir.rglob("*") if candidate.is_file()):
                continue
            raise RuntimeError(f"解压完成但未找到必需文件：{required}")
