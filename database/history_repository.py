from __future__ import annotations

from datetime import datetime
from typing import Any

from database.sqlite_manager import SQLiteManager
from resolver.models import VideoInfo
from resolver.source_utils import detect_source_site


class HistoryRepository:
    def __init__(self, db: SQLiteManager) -> None:
        self.db = db

    def record_play(self, video: VideoInfo, watched_position: int = 0) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as conn:
            existing = conn.execute(
                "SELECT id, play_count FROM history WHERE video_id = ? ORDER BY id DESC LIMIT 1",
                (video.video_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE history
                    SET title = ?, source_site = ?, webpage_url = ?, uploader = ?, thumbnail = ?, duration = ?,
                        watched_position = ?, play_count = ?, last_played_at = ?
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
                        watched_position,
                        now,
                        now,
                    ),
                )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT video_id, title, source_site, webpage_url, uploader, thumbnail, duration,
                       watched_position, play_count, last_played_at
                FROM history
                ORDER BY last_played_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_site"] = detect_source_site(item.get("webpage_url", ""), item.get("source_site", ""))
            result.append(item)
        return result
