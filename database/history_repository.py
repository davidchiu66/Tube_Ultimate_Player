from __future__ import annotations

from datetime import datetime
from typing import Any

from database.sqlite_manager import SQLiteManager
from resolver.models import VideoInfo
from resolver.source_utils import detect_source_site


class HistoryRepository:
    def __init__(self, db: SQLiteManager) -> None:
        self.db = db

    def record_play(self, video: VideoInfo, watched_position: int | None = None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connection() as conn:
            existing = conn.execute(
                """
                SELECT id, play_count FROM history
                WHERE video_id = ? OR (webpage_url IS NOT NULL AND webpage_url = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (video.video_id, video.webpage_url),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE history
                    SET title = ?, source_site = ?, webpage_url = ?, uploader = ?, thumbnail = ?, duration = ?,
                        watched_position = COALESCE(?, watched_position), play_count = ?, last_played_at = ?
                    WHERE id = ?
                    """,
                    (
                        video.title,
                        detect_source_site(video.webpage_url, video.source_site),
                        video.webpage_url,
                        video.uploader,
                        video.thumbnail,
                        video.duration,
                        watched_position,
                        int(existing["play_count"] or 0) + 1,
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO history (
                        video_id, title, source_site, webpage_url, uploader, thumbnail, duration,
                        watched_position, play_count, last_played_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        video.video_id,
                        video.title,
                        detect_source_site(video.webpage_url, video.source_site),
                        video.webpage_url,
                        video.uploader,
                        video.thumbnail,
                        video.duration,
                        watched_position or 0,
                        now,
                        now,
                    ),
                )

    def watched_position(self, video_id: str, webpage_url: str = "") -> float:
        clean_id = str(video_id or "").strip()
        clean_url = str(webpage_url or "").strip()
        if not clean_id and not clean_url:
            return 0.0
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT watched_position FROM history
                WHERE video_id = ? OR (webpage_url IS NOT NULL AND webpage_url = ?)
                ORDER BY id DESC LIMIT 1
                """,
                (clean_id, clean_url),
            ).fetchone()
        try:
            return max(0.0, float(row["watched_position"] or 0.0)) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    def update_watched_position(self, video_id: str, position: float, duration: float = 0.0, webpage_url: str = "") -> None:
        clean_id = str(video_id or "").strip()
        clean_url = str(webpage_url or "").strip()
        if not clean_id and not clean_url:
            return
        try:
            watched = max(0.0, float(position or 0.0))
            total = max(0.0, float(duration or 0.0))
        except (TypeError, ValueError):
            return
        with self.db.connection() as conn:
            conn.execute(
                """
                UPDATE history SET watched_position = ?, duration = CASE WHEN ? > 0 THEN ? ELSE duration END
                WHERE video_id = ? OR (webpage_url IS NOT NULL AND webpage_url = ?)
                """,
                (watched, total, total, clean_id, clean_url),
            )
    def remove(self, video_id: str) -> int:
        """删除某个视频的全部历史行，返回实际删除数。"""
        clean_id = str(video_id or "").strip()
        if not clean_id:
            return 0
        with self.db.connection() as conn:
            cursor = conn.execute("DELETE FROM history WHERE video_id = ?", (clean_id,))
            return int(cursor.rowcount or 0)

    def remove_many(self, video_ids: list[str]) -> int:
        """用一条语句批量删除，返回实际删除的历史行数。"""
        ids = list(
            dict.fromkeys(
                str(video_id or "").strip()
                for video_id in list(video_ids or [])
                if str(video_id or "").strip()
            )
        )
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.db.connection() as conn:
            cursor = conn.execute(f"DELETE FROM history WHERE video_id IN ({placeholders})", ids)
            return int(cursor.rowcount or 0)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            normalized_limit = max(0, int(limit))
        except (TypeError, ValueError):
            normalized_limit = 50
        if normalized_limit == 0:
            return []
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT video_id, title, source_site, webpage_url, uploader, thumbnail, duration,
                       watched_position, play_count, last_played_at
                FROM history
                ORDER BY last_played_at DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_site"] = detect_source_site(item.get("webpage_url", ""), item.get("source_site", ""))
            result.append(item)
        return result
