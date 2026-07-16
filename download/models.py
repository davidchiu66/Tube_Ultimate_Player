from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4


STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_DELETED = "deleted"


@dataclass
class DownloadTask:
    url: str
    title: str
    video_id: str = ""
    source_site: str = "youtube"
    quality_label: str = "Auto"
    format_selector: str = "bestvideo+bestaudio/best"
    save_dir: str = ""
    expected_bytes: int | None = None
    task_id: str = ""
    status: str = STATUS_QUEUED
    progress: float = 0.0
    speed_text: str = ""
    eta_text: str = ""
    output_path: str = ""
    error_message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.task_id:
            self.task_id = uuid4().hex
        now = datetime.now()
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now

    def touch(self) -> None:
        self.updated_at = datetime.now()

    def is_active(self) -> bool:
        return self.status == STATUS_DOWNLOADING

    def is_finished(self) -> bool:
        return self.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_DELETED)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat() if self.created_at else None
        data["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DownloadTask":
        values = dict(data)
        for key in ("created_at", "updated_at"):
            raw = values.get(key)
            if isinstance(raw, str) and raw:
                try:
                    values[key] = datetime.fromisoformat(raw)
                except ValueError:
                    values[key] = None
            else:
                values[key] = None
        return cls(**values)
