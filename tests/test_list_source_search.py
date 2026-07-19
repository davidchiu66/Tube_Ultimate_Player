from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QHeaderView

from database.favorite_repository import FavoriteRepository
from database.history_repository import HistoryRepository
from database.sqlite_manager import SQLiteManager
from download.models import DownloadTask
from resolver.models import VideoInfo
from ui.download_page import DownloadPage
from ui.favorite_page import FavoritePage
from ui.history_page import HistoryPage


class ListSourceSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.video = VideoInfo(
            video_id="BV1test",
            title="B站测试视频",
            source_site="bilibili",
            uploader="测试作者",
            duration=125,
            webpage_url="https://www.bilibili.com/video/BV1test",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_favorite_and_history_store_source_site(self) -> None:
        favorites = FavoriteRepository(self.db)
        history = HistoryRepository(self.db)
        favorites.add_video_info(self.video)
        history.record_play(self.video)

        self.assertEqual(favorites.all()[0]["source_site"], "bilibili")
        self.assertEqual(history.recent()[0]["source_site"], "bilibili")

    def test_list_pages_filter_by_source(self) -> None:
        favorites = FavoriteRepository(self.db)
        history = HistoryRepository(self.db)
        favorites.add_video_info(self.video)
        history.record_play(self.video)

        favorite_page = FavoritePage(favorites)
        history_page = HistoryPage(history)
        favorite_page.search_edit.setText("Bilibili")
        history_page.search_edit.setText("Bilibili")

        self.assertFalse(favorite_page.list_widget.isRowHidden(0))
        self.assertFalse(history_page.list_widget.isRowHidden(0))
        self.assertEqual(favorite_page.list_widget.item(0, 1).text(), "Bilibili")
        self.assertEqual(history_page.list_widget.item(0, 1).text(), "Bilibili")
        self.assertEqual(history_page.list_widget.item(0, 2).text(), "测试作者")

    def test_download_page_filters_source_and_stretches_content_columns(self) -> None:
        page = DownloadPage()
        page.add_task(
            DownloadTask(
                url=self.video.webpage_url,
                title=self.video.title,
                video_id=self.video.video_id,
                source_site=self.video.source_site,
            )
        )
        page.search_edit.setText("Bilibili")

        self.assertFalse(page.table.isRowHidden(0))
        self.assertEqual(page.table.item(0, 1).text(), "Bilibili")
        header = page.table.horizontalHeader()
        self.assertEqual(header.sectionResizeMode(0), QHeaderView.ResizeMode.Stretch)
        self.assertEqual(header.sectionResizeMode(7), QHeaderView.ResizeMode.Stretch)


class DatabaseMigrationTests(unittest.TestCase):
    def test_managed_connection_is_closed_after_context_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SQLiteManager(Path(temp_dir) / "managed.sqlite3")
            with db.connection() as conn:
                conn.execute("SELECT 1").fetchone()

            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_existing_tables_gain_source_site_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE history (
                        id INTEGER PRIMARY KEY, video_id TEXT, title TEXT, webpage_url TEXT,
                        thumbnail TEXT, duration INTEGER, watched_position INTEGER,
                        play_count INTEGER, last_played_at TEXT, created_at TEXT
                    );
                    CREATE TABLE favorite (
                        id INTEGER PRIMARY KEY, video_id TEXT, title TEXT, webpage_url TEXT,
                        uploader TEXT, duration INTEGER, thumbnail TEXT,
                        created_at TEXT, updated_at TEXT
                    );
                    """
                )
            conn.close()

            db = SQLiteManager(path)
            conn = db.connect()
            history_columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
            favorite_columns = {row[1] for row in conn.execute("PRAGMA table_info(favorite)")}
            conn.close()
            del db

        self.assertIn("source_site", history_columns)
        self.assertIn("source_site", favorite_columns)


if __name__ == "__main__":
    unittest.main()
