from __future__ import annotations

from datetime import datetime
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4

from database.sqlite_manager import SQLiteManager
from resolver.models import PlaylistEntry, SavedPlaylist


class PlaylistRepository:
    def __init__(self, db: SQLiteManager) -> None:
        self.db = db

    def all_playlists(self) -> list[SavedPlaylist]:
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT playlist_key, name, source_url, source_type, auto_play_next, created_at, updated_at
                FROM playlist_library
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [
            SavedPlaylist(
                playlist_key=str(row["playlist_key"]),
                name=str(row["name"]),
                source_url=str(row["source_url"] or ""),
                source_type=str(row["source_type"] or "manual"),
                auto_play_next=bool(row["auto_play_next"]),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )
            for row in rows
        ]

    def get_playlist(self, playlist_key: str) -> SavedPlaylist | None:
        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT playlist_key, name, source_url, source_type, auto_play_next, created_at, updated_at
                FROM playlist_library
                WHERE playlist_key = ?
                LIMIT 1
                """,
                (playlist_key,),
            ).fetchone()
            if row is None:
                return None

            entry_rows = conn.execute(
                """
                SELECT playlist_id, video_id, title, webpage_url, uploader, duration, thumbnail, position, availability
                FROM playlist_item
                WHERE playlist_key = ?
                ORDER BY position ASC, id ASC
                """,
                (playlist_key,),
            ).fetchall()

        return SavedPlaylist(
            playlist_key=str(row["playlist_key"]),
            name=str(row["name"]),
            source_url=str(row["source_url"] or ""),
            source_type=str(row["source_type"] or "manual"),
            auto_play_next=bool(row["auto_play_next"]),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            entries=[self._row_to_entry(item, playlist_key=str(row["playlist_key"])) for item in entry_rows],
        )

    def save_playlist(
        self,
        *,
        name: str,
        entries: Iterable[PlaylistEntry],
        source_url: str = "",
        source_type: str = "manual",
        auto_play_next: bool = True,
        playlist_key: str | None = None,
    ) -> str:
        playlist_name = str(name or "").strip()
        if not playlist_name:
            raise ValueError("playlist name is required")

        playlist_entries = [self._clone_entry(entry) for entry in entries]
        if not playlist_entries:
            raise ValueError("playlist entries are required")

        key = playlist_key or uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")

        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT id, created_at FROM playlist_library WHERE playlist_key = ? LIMIT 1",
                (key,),
            ).fetchone()
            if existing:
                created_at = str(existing["created_at"] or now)
                conn.execute(
                    """
                    UPDATE playlist_library
                    SET name = ?, source_url = ?, source_type = ?, auto_play_next = ?, updated_at = ?
                    WHERE playlist_key = ?
                    """,
                    (playlist_name, source_url, source_type, 1 if auto_play_next else 0, now, key),
                )
                conn.execute("DELETE FROM playlist_item WHERE playlist_key = ?", (key,))
            else:
                created_at = now
                conn.execute(
                    """
                    INSERT INTO playlist_library (
                        playlist_key, name, source_url, source_type, auto_play_next, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, playlist_name, source_url, source_type, 1 if auto_play_next else 0, created_at, now),
                )

            conn.executemany(
                """
                INSERT INTO playlist_item (
                    playlist_key, playlist_id, video_id, title, webpage_url, uploader, duration,
                    thumbnail, position, availability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        key,
                        entry.playlist_id,
                        entry.video_id,
                        entry.title,
                        entry.webpage_url,
                        entry.uploader,
                        entry.duration,
                        entry.thumbnail,
                        int(entry.position),
                        entry.availability,
                        now,
                    )
                    for entry in playlist_entries
                ],
            )

        return key

    def delete_playlist(self, playlist_key: str) -> None:
        with self.db.connection() as conn:
            conn.execute("DELETE FROM playlist_item WHERE playlist_key = ?", (playlist_key,))
            conn.execute("DELETE FROM playlist_library WHERE playlist_key = ?", (playlist_key,))

    def rename_playlist(self, playlist_key: str, name: str) -> None:
        playlist_name = str(name or "").strip()
        if not playlist_name:
            raise ValueError("playlist name is required")
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE playlist_library SET name = ?, updated_at = ? WHERE playlist_key = ?",
                (playlist_name, now, playlist_key),
            )

    def set_auto_play_next(self, playlist_key: str, enabled: bool) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE playlist_library SET auto_play_next = ?, updated_at = ? WHERE playlist_key = ?",
                (1 if enabled else 0, now, playlist_key),
            )

    @staticmethod
    def _clone_entry(entry: PlaylistEntry) -> PlaylistEntry:
        return PlaylistEntry(
            playlist_id=str(entry.playlist_id),
            video_id=str(entry.video_id),
            title=str(entry.title),
            webpage_url=str(entry.webpage_url),
            source_site=str(entry.source_site or _detect_source_site(str(entry.webpage_url))),
            uploader=str(entry.uploader),
            duration=int(entry.duration or 0),
            thumbnail=str(entry.thumbnail),
            position=int(entry.position or 0),
            availability=str(entry.availability or ""),
        )

    @staticmethod
    def _row_to_entry(row, playlist_key: str) -> PlaylistEntry:
        return PlaylistEntry(
            playlist_id=str(row["playlist_id"] or playlist_key),
            video_id=str(row["video_id"] or ""),
            title=str(row["title"] or ""),
            webpage_url=str(row["webpage_url"] or ""),
            source_site=_detect_source_site(str(row["webpage_url"] or "")),
            uploader=str(row["uploader"] or ""),
            duration=int(row["duration"] or 0),
            thumbnail=str(row["thumbnail"] or ""),
            position=int(row["position"] or 0),
            availability=str(row["availability"] or ""),
        )


def _detect_source_site(url: str) -> str:
    host = urlparse(str(url or "").strip()).netloc.lower()
    if host.endswith("bilibili.com") or host.endswith("b23.tv"):
        return "bilibili"
    return "youtube"
