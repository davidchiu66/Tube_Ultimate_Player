from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.history_repository import HistoryRepository
from database.playback_resume_repository import PlaybackResumeRepository
from database.sqlite_manager import SQLiteManager
from resolver.models import VideoInfo
from ui.main_window import _scan_local_video_files


class PlaybackResumeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.resume = PlaybackResumeRepository(self.db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_local_resume_round_trip_and_metadata(self) -> None:
        path = Path(self.temp_dir.name) / "clip.mp4"
        path.write_bytes(b"video")

        self.resume.save_local(path, 42.5, 120, title="clip.mp4")
        record = self.resume.get_local(path)

        self.assertIsNotNone(record)
        self.assertEqual(record["watched_position"], 42.5)
        self.assertEqual(record["file_size"], 5)

    def test_local_resume_clear(self) -> None:
        path = Path(self.temp_dir.name) / "clip.mp4"
        path.write_bytes(b"video")
        self.resume.save_local(path, 10, 100)

        self.resume.clear_local(path)

        self.assertIsNone(self.resume.get_local(path))


class HistoryResumeTests(unittest.TestCase):
    def test_update_position_does_not_increment_play_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryRepository(SQLiteManager(Path(temp_dir) / "test.sqlite3"))
            video = VideoInfo(video_id="v1", title="Video", webpage_url="https://example.com/v1", duration=100)
            history.record_play(video)
            history.update_watched_position("v1", 35, 100)
            history.record_play(video)
            row = history.recent(1)[0]

        self.assertEqual(row["watched_position"], 35)
        self.assertEqual(row["play_count"], 2)

    def test_resume_falls_back_to_webpage_url_when_video_id_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = HistoryRepository(SQLiteManager(Path(temp_dir) / "test.sqlite3"))
            first = VideoInfo(video_id="old-id", title="Video", webpage_url="https://example.com/watch?id=1", duration=100)
            second = VideoInfo(video_id="new-id", title="Video", webpage_url=first.webpage_url, duration=100)
            history.record_play(first)
            history.update_watched_position("old-id", 35, 100, first.webpage_url)

            position = history.watched_position(second.video_id, second.webpage_url)
            history.record_play(second)
            rows = history.recent(5)

        self.assertEqual(position, 35)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["watched_position"], 35)

    def test_force_flush_uses_last_observed_position_when_mpv_has_reset(self) -> None:
        class FakeHistory:
            def __init__(self) -> None:
                self.saved = []

            def update_watched_position(self, *args) -> None:
                self.saved.append(args)

        from ui.main_window import MainWindow

        state = type("State", (), {})()
        state._resume_media_key = "video:v1"
        state._resume_video_url = "https://example.com/v1"
        state._resume_last_position = 30.0
        state._resume_latest_position = 30.0
        state._resume_last_write_at = 0.0
        state._resume_ignore_until = 0.0
        state._resume_write_interval = 5.0
        state.mpv = type("Mpv", (), {"position": lambda _self: 1.0, "duration": lambda _self: 100.0})()
        state.history = FakeHistory()

        MainWindow._flush_playback_resume(state)

        self.assertEqual(state.history.saved[0][1], 30.0)


class LocalDirectoryScanTests(unittest.TestCase):
    def test_scan_filters_temp_files_and_sorts_naturally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ("第10集.mp4", "第2集.mp4", "cover.jpg", ".hidden.mp4", "draft.mp4.part"):
                (root / name).write_bytes(b"x")

            files = _scan_local_video_files(root / "第10集.mp4")

        self.assertEqual([path.name for path in files], ["第2集.mp4", "第10集.mp4"])


if __name__ == "__main__":
    unittest.main()
