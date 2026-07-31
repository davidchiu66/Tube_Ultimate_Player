"""E1 验证：视频更新时间的解析、格式化与自动迁移。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.playlist_repository import PlaylistRepository
from database.sqlite_manager import SQLiteManager
from resolver.models import PlaylistEntry
from resolver.site_resolver import _bilibili_upload_date
from resolver.youtube_resolver import _youtube_upload_date
from ui.text_elision import format_upload_date


class FormatUploadDateTests(unittest.TestCase):
    def test_valid_date_is_dashed(self) -> None:
        self.assertEqual(format_upload_date("20260728"), "2026-07-28")

    def test_empty_and_invalid_return_blank(self) -> None:
        for value in ("", None, "2026", "abcdefgh", "20261340", "00000000"):
            self.assertEqual(format_upload_date(value), "")


class BilibiliUploadDateTests(unittest.TestCase):
    def test_pubdate_is_converted(self) -> None:
        # 2026-07-28 12:00 本地时间对应的 unix 秒随时区变化，这里断言换算成功且是 8 位。
        result = _bilibili_upload_date({"pubdate": 1785000000})
        self.assertEqual(len(result), 8)
        self.assertTrue(result.isdigit())

    def test_field_priority_and_fallback(self) -> None:
        self.assertEqual(_bilibili_upload_date({"senddate": 1785000000}), _bilibili_upload_date({"pubdate": 1785000000}))
        self.assertEqual(_bilibili_upload_date({}), "")
        self.assertEqual(_bilibili_upload_date({"pubdate": 0}), "")
        self.assertEqual(_bilibili_upload_date({"pubdate": "not-a-number"}), "")


class YoutubeUploadDateTests(unittest.TestCase):
    def test_upload_date_used_directly(self) -> None:
        self.assertEqual(_youtube_upload_date({"upload_date": "20260728"}), "20260728")

    def test_timestamp_fallback(self) -> None:
        result = _youtube_upload_date({"timestamp": 1785000000})
        self.assertEqual(len(result), 8)

    def test_release_timestamp_fallback(self) -> None:
        result = _youtube_upload_date({"release_timestamp": 1785000000})
        self.assertEqual(len(result), 8)

    def test_missing_returns_blank(self) -> None:
        self.assertEqual(_youtube_upload_date({}), "")
        self.assertEqual(_youtube_upload_date({"upload_date": "bad"}), "")


class PlaylistUploadDateMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "tube.db"

    def _legacy_schema(self) -> None:
        # 建一个不含 upload_date 列的旧版 playlist_item，验证自动迁移。
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE playlist_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                source_url TEXT, source_type TEXT NOT NULL DEFAULT 'manual',
                auto_play_next INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE playlist_item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_key TEXT NOT NULL, playlist_id TEXT NOT NULL,
                video_id TEXT NOT NULL, title TEXT NOT NULL, webpage_url TEXT NOT NULL,
                uploader TEXT, duration INTEGER DEFAULT 0, thumbnail TEXT,
                position INTEGER NOT NULL, availability TEXT, created_at TEXT NOT NULL
            );
            """
        )
        conn.close()

    def test_migration_adds_column_and_roundtrips(self) -> None:
        self._legacy_schema()
        manager = SQLiteManager(self.db_path)
        manager.initialize()

        columns = self._columns(manager)
        self.assertIn("upload_date", columns)

        repo = PlaylistRepository(manager)
        key = repo.save_playlist(
            name="片单",
            entries=[
                PlaylistEntry(
                    playlist_id="p",
                    video_id="v1",
                    title="视频一",
                    webpage_url="https://www.bilibili.com/video/BV1",
                    upload_date="20260728",
                )
            ],
        )
        loaded = repo.get_playlist(key)
        self.assertEqual(loaded.entries[0].upload_date, "20260728")

    def _columns(self, manager: SQLiteManager) -> set[str]:
        with manager.connection() as conn:
            return {str(row[1]) for row in conn.execute("PRAGMA table_info(playlist_item)").fetchall()}


if __name__ == "__main__":
    unittest.main()
