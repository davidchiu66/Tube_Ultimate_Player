from __future__ import annotations

from datetime import datetime
from pathlib import Path

from database.sqlite_manager import SQLiteManager


class PlaybackResumeRepository:
    """Persistence for local-file playback positions."""

    def __init__(self, db: SQLiteManager) -> None:
        self.db = db

    def get_local(self, path: str | Path) -> dict | None:
        normalized = self.normalize_path(path)
        if not normalized:
            return None
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT path, title, file_size, file_mtime, watched_position, duration, updated_at "
                "FROM local_playback_resume WHERE path = ? LIMIT 1",
                (normalized,),
            ).fetchone()
        return dict(row) if row is not None else None

    def save_local(
        self,
        path: str | Path,
        position: float,
        duration: float = 0.0,
        *,
        title: str = "",
    ) -> None:
        normalized = self.normalize_path(path)
        if not normalized:
            return
        file_path = Path(normalized)
        try:
            stat = file_path.stat()
            file_size = int(stat.st_size)
            file_mtime = float(stat.st_mtime)
        except OSError:
            return
        try:
            watched = max(0.0, float(position or 0.0))
            total = max(0.0, float(duration or 0.0))
        except (TypeError, ValueError):
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO local_playback_resume
                    (path, title, file_size, file_mtime, watched_position, duration, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    title = excluded.title,
                    file_size = excluded.file_size,
                    file_mtime = excluded.file_mtime,
                    watched_position = excluded.watched_position,
                    duration = excluded.duration,
                    updated_at = excluded.updated_at
                """,
                (normalized, str(title or file_path.name), file_size, file_mtime, watched, total, now),
            )

    def clear_local(self, path: str | Path) -> None:
        normalized = self.normalize_path(path)
        if not normalized:
            return
        with self.db.connection() as conn:
            conn.execute("DELETE FROM local_playback_resume WHERE path = ?", (normalized,))

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve())
        except OSError:
            return str(Path(raw).expanduser().absolute())

