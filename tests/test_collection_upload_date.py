"""左侧滑出的合集列表要带上传日期。

`--flat-playlist` 默认不返回日期字段，必须显式加 `--extractor-args
youtubetab:approximate_date`。首页/创作者页命令一直带着这个参数，播放列表命令漏了，
于是合集列表只有标题、右侧播放列表却有日期 —— 差别就在这一个参数上。
渲染层（PlaylistItemWidget）本来就会显示日期，所以这是数据缺失，不是显示问题。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from resolver.models import VideoInfo
from resolver.youtube_resolver import _APPROXIMATE_DATE_ARGS, YoutubeResolver
from services.config_service import ConfigService
from ui.text_elision import format_upload_date


APPROXIMATE_DATE_FLAG = _APPROXIMATE_DATE_ARGS[1]


def entry(video_id: str, *, timestamp: int | None = None, upload_date: str = "") -> dict:
    payload: dict = {
        "id": video_id,
        "title": f"视频 {video_id}",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "duration": 61,
        "uploader": "Author",
    }
    if timestamp is not None:
        payload["timestamp"] = timestamp
    if upload_date:
        payload["upload_date"] = upload_date
    return payload


class PlaylistCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "default.json").write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=root / "default.json", user_path=root / "user.json")
        self.config.set("youtube.cookie_browser", "")
        self.resolver = YoutubeResolver.__new__(YoutubeResolver)
        self.resolver.config = self.config
        self.resolver.ytdlp_path = Path("yt-dlp")

    def test_playlist_command_requests_approximate_dates(self) -> None:
        command = self.resolver._build_playlist_command("https://www.youtube.com/playlist?list=PL1")

        self.assertIn(APPROXIMATE_DATE_FLAG, command)
        index = command.index(APPROXIMATE_DATE_FLAG)
        self.assertEqual(command[index - 1], "--extractor-args")

    def test_home_command_still_requests_them(self) -> None:
        """两条命令必须一致，否则同一份列表在不同入口下带的字段又要分叉。"""
        command = self.resolver._build_home_command("https://www.youtube.com/", 20)

        self.assertIn(APPROXIMATE_DATE_FLAG, command)

    def test_flag_is_passed_once_per_command(self) -> None:
        command = self.resolver._build_playlist_command("https://www.youtube.com/playlist?list=PL1")

        self.assertEqual(command.count(APPROXIMATE_DATE_FLAG), 1)
        self.assertEqual(command.count("--extractor-args"), 1)


class CollectionEntryDateTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "default.json").write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=root / "default.json", user_path=root / "user.json")
        self.config.set("youtube.cookie_browser", "")
        self.resolver = YoutubeResolver.__new__(YoutubeResolver)
        self.resolver.config = self.config
        self.resolver.ytdlp_path = Path("yt-dlp")
        self.commands: list[list[str]] = []

    def _stub_ytdlp(self, payload: dict) -> None:
        def fake_run(command, _url, _attempt):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        self.resolver._run_ytdlp = fake_run

    def test_timestamp_becomes_the_entry_upload_date(self) -> None:
        # 1770000000 -> 2026-02-02 UTC；approximate_date 回的正是这种 timestamp。
        self._stub_ytdlp(
            {
                "id": "PL1",
                "title": "合集",
                "entries": [entry("abcdefghijk", timestamp=1770000000)],
            }
        )
        video = VideoInfo(
            video_id="abcdefghijk",
            title="当前",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abcdefghijk&list=PL1",
        )

        playlist = self.resolver.resolve_collection(video)

        self.assertIsNotNone(playlist)
        self.assertEqual(playlist.source_type, "collection")
        self.assertTrue(playlist.entries[0].upload_date)
        # 渲染层用 format_upload_date 显示年月日，必须能格式化出来。
        self.assertTrue(format_upload_date(playlist.entries[0].upload_date))

    def test_explicit_upload_date_is_kept(self) -> None:
        self._stub_ytdlp(
            {
                "id": "PL1",
                "title": "合集",
                "entries": [entry("abcdefghijk", upload_date="20260210")],
            }
        )
        video = VideoInfo(
            video_id="abcdefghijk",
            title="当前",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abcdefghijk&list=PL1",
        )

        playlist = self.resolver.resolve_collection(video)

        self.assertEqual(playlist.entries[0].upload_date, "20260210")

    def test_collection_resolution_asks_for_dates(self) -> None:
        self._stub_ytdlp(
            {
                "id": "PL1",
                "title": "合集",
                "entries": [entry("abcdefghijk", timestamp=1770000000)],
            }
        )
        video = VideoInfo(
            video_id="abcdefghijk",
            title="当前",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abcdefghijk&list=PL1",
        )

        self.resolver.resolve_collection(video)

        self.assertTrue(self.commands)
        self.assertIn(APPROXIMATE_DATE_FLAG, self.commands[0])

    def test_entries_without_any_date_still_resolve(self) -> None:
        """站点不给日期时只是没有日期，不该让整个合集加载失败。"""
        self._stub_ytdlp({"id": "PL1", "title": "合集", "entries": [entry("abcdefghijk")]})
        video = VideoInfo(
            video_id="abcdefghijk",
            title="当前",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abcdefghijk&list=PL1",
        )

        playlist = self.resolver.resolve_collection(video)

        self.assertEqual(len(playlist.entries), 1)
        self.assertEqual(playlist.entries[0].upload_date, "")


class PlaylistItemMetaTests(unittest.TestCase):
    """两侧列表共用 PlaylistItemWidget，日期只要在数据里就会被渲染出来。"""

    def test_meta_line_contains_the_formatted_date(self) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtNetwork import QNetworkAccessManager
        from PySide6.QtWidgets import QApplication

        from resolver.models import PlaylistEntry
        from ui.playlist_overlay import PlaylistItemWidget

        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        item = PlaylistEntry(
            playlist_id="youtube:playlist:PL1",
            video_id="abcdefghijk",
            title="视频",
            webpage_url="https://www.youtube.com/watch?v=abcdefghijk",
            source_site="youtube",
            uploader="Author",
            duration=61,
            upload_date="20260210",
        )

        widget = PlaylistItemWidget(
            item, 0, QNetworkAccessManager(), SimpleNamespace(load=lambda *_a, **_k: None)
        )

        self.assertIn(format_upload_date("20260210"), widget.meta_label.text())


if __name__ == "__main__":
    unittest.main()
