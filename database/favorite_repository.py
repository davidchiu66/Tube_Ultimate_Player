from __future__ import annotations

from datetime import datetime
from typing import Any

from database.sqlite_manager import SQLiteManager
from resolver.models import HomeVideo, VideoInfo
from resolver.source_utils import detect_source_site


class FavoriteRepository:
    def __init__(self, db: SQLiteManager) -> None:
        self.db = db

    def add_video_info(self, video: VideoInfo) -> bool:
        return self._upsert(
            video_id=video.video_id,
            title=video.title,
            webpage_url=video.webpage_url,
            source_site=video.source_site,
            uploader=video.uploader,
            duration=video.duration,
            thumbnail=video.thumbnail,
        )

    def add_home_video(self, video: HomeVideo) -> bool:
        return self._upsert(
            video_id=video.video_id,
            title=video.title,
            webpage_url=video.webpage_url,
            source_site=video.source_site,
            uploader=video.uploader,
            duration=video.duration,
            thumbnail=video.thumbnail,
        )

    def remove(self, video_id: str) -> None:
        with self.db.connection() as conn:
            conn.execute("DELETE FROM favorite WHERE video_id = ?", (video_id,))

    def is_favorite(self, video_id: str) -> bool:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM favorite WHERE video_id = ? LIMIT 1",
                (video_id,),
            ).fetchone()
        return row is not None

    def favorite_ids(self) -> set[str]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT video_id FROM favorite").fetchall()
        return {str(row["video_id"]) for row in rows}

    def all(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT video_id, title, source_site, webpage_url, uploader, duration, thumbnail, created_at, updated_at
                FROM favorite
                ORDER BY updated_at DESC, id DESC
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

    def _upsert(
        self,
        *,
        video_id: str,
        title: str,
        webpage_url: str,
        source_site: str,
        uploader: str,
        duration: int,
        thumbnail: str,
    ) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT id FROM favorite WHERE video_id = ? LIMIT 1",
                (video_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE favorite
                    SET title = ?, source_site = ?, webpage_url = ?, uploader = ?, duration = ?, thumbnail = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (title, detect_source_site(webpage_url, source_site), webpage_url, uploader, duration, thumbnail, now, existing["id"]),
                )
                return False

            conn.execute(
                """
                INSERT INTO favorite (
                    video_id, title, source_site, webpage_url, uploader, duration, thumbnail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, title, detect_source_site(webpage_url, source_site), webpage_url, uploader, duration, thumbnail, now, now),
            )
            return True
