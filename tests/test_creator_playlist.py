from __future__ import annotations

import os
import subprocess
import threading
import time
import unittest
from collections import OrderedDict
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QObject, QThreadPool, QTimer, Slot
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import QApplication

from resolver.models import PlaylistEntry, PlaylistInfo, VideoInfo
from resolver.site_resolver import (
    BilibiliResolver,
    SiteResolver,
    _creator_entry_from_bilibili_archive,
)
from resolver.youtube_resolver import YoutubeResolver, _creator_videos_url
from ui.main_window import MainWindow
from ui.playlist_overlay import PlaylistItemWidget
from workers.creator_videos_worker import CreatorVideosWorker


def _entry(video_id: str, url: str, *, site: str = "youtube") -> PlaylistEntry:
    return PlaylistEntry(
        playlist_id="source",
        video_id=video_id,
        title=video_id,
        webpage_url=url,
        source_site=site,
        uploader="Author",
    )


class _CountingResolver:
    def __init__(self, creator_url: str, entries: list[PlaylistEntry]) -> None:
        self.creator_url = creator_url
        self.entries = entries
        self.calls = 0

    def fetch_creator_videos(self, _video: VideoInfo, _limit: int):
        self.calls += 1
        return self.creator_url, list(self.entries)


def _site_resolver(source) -> SiteResolver:
    resolver = SiteResolver.__new__(SiteResolver)
    resolver.youtube = source
    resolver.bilibili = source
    resolver._creator_cache = OrderedDict()
    resolver._creator_cache_lock = threading.Lock()
    resolver._config_fingerprint = lambda: "test"
    return resolver


class CreatorPlaylistTests(unittest.TestCase):
    def test_youtube_flat_creator_response_is_parsed(self) -> None:
        resolver = YoutubeResolver.__new__(YoutubeResolver)
        resolver.config = SimpleNamespace(cookie_file=lambda: "")
        resolver._build_home_command = lambda url, limit, **_kwargs: [url, str(limit)]
        resolver._run_ytdlp = lambda command, _url, _attempt: subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"entries": [{"id": "abcdefghijk", "title": "Other", '
                '"url": "https://www.youtube.com/watch?v=abcdefghijk"}]}'
            ),
            stderr="",
        )
        video = VideoInfo(
            video_id="12345678901",
            title="Current",
            source_site="youtube",
            creator_id="UC123",
            creator_url="https://www.youtube.com/@author",
        )

        creator_url, entries = resolver.fetch_creator_videos(video, 50)

        self.assertEqual(creator_url, "https://www.youtube.com/@author/videos")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].video_id, "abcdefghijk")

    def test_bilibili_wbi_creator_response_is_parsed(self) -> None:
        resolver = BilibiliResolver.__new__(BilibiliResolver)
        resolver.ytdlp = SimpleNamespace(fetch_creator_videos=lambda *_args: self.fail("fallback used"))
        resolver._preferred_cookie_header = lambda _url: ""
        resolver._sign_wbi_params = lambda params, cookie_header="": params
        resolver._request_json = lambda *_args, **_kwargs: {
            "data": {
                "list": {
                    "vlist": [
                        {
                            "bvid": "BV1abcdefghi",
                            "title": "Other",
                            "author": "UP",
                            "length": "03:20",
                        }
                    ]
                }
            }
        }
        video = VideoInfo(
            video_id="bilibili:BV1jihgfedca",
            title="Current",
            source_site="bilibili",
            creator_id="123",
            creator_url="https://space.bilibili.com/123",
        )

        creator_url, entries = resolver.fetch_creator_videos(video, 50)

        self.assertEqual(creator_url, "https://space.bilibili.com/123")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].duration, 200)

    def test_current_video_is_first_and_results_are_cached(self) -> None:
        source = _CountingResolver(
            "https://www.youtube.com/@author/videos",
            [
                _entry("current", "https://www.youtube.com/watch?v=current"),
                _entry("other-1", "https://www.youtube.com/watch?v=other-1"),
                _entry("other-2", "https://www.youtube.com/watch?v=other-2"),
            ],
        )
        resolver = _site_resolver(source)
        video = VideoInfo(
            video_id="current",
            title="Current",
            source_site="youtube",
            uploader="Author",
            creator_id="channel-id",
            creator_url="https://www.youtube.com/@author",
            webpage_url="https://www.youtube.com/watch?v=current",
        )

        playlist = resolver.resolve_creator_playlist(video, 50)
        cached = resolver.resolve_creator_playlist(video, 50)

        self.assertIsNotNone(playlist)
        self.assertEqual([item.video_id for item in playlist.entries], ["current", "other-1", "other-2"])
        self.assertEqual(playlist.current_video_id, "current")
        self.assertEqual(playlist.source_type, "creator")
        self.assertEqual(source.calls, 1)
        self.assertIsNot(playlist, cached)

    def test_bilibili_multi_page_current_archive_is_deduplicated(self) -> None:
        bvid = "BV1abcdefghi"
        other_bvid = "BV1jihgfedc"
        source = _CountingResolver(
            "https://space.bilibili.com/123/video",
            [
                _entry(f"bilibili:{bvid}", f"https://www.bilibili.com/video/{bvid}", site="bilibili"),
                _entry(
                    f"bilibili:{other_bvid}",
                    f"https://www.bilibili.com/video/{other_bvid}",
                    site="bilibili",
                ),
            ],
        )
        resolver = _site_resolver(source)
        video = VideoInfo(
            video_id=f"bilibili:{bvid}:p2",
            title="Current P2",
            source_site="bilibili",
            uploader="UP",
            creator_id="123",
            creator_url="https://space.bilibili.com/123",
            webpage_url=f"https://www.bilibili.com/video/{bvid}?p=2",
        )

        playlist = resolver.resolve_creator_playlist(video, 50)

        self.assertEqual(len(playlist.entries), 2)
        self.assertEqual(playlist.entries[0].video_id, f"bilibili:{bvid}:p2")
        self.assertEqual(playlist.entries[1].video_id, f"bilibili:{other_bvid}")

    def test_bilibili_archive_conversion(self) -> None:
        entry = _creator_entry_from_bilibili_archive(
            {
                "bvid": "BV1abcdefghi",
                "title": "Example",
                "author": "UP",
                "length": "01:02:03",
                "pic": "//i.example/cover.jpg",
            },
            "creator",
            1,
        )

        self.assertEqual(entry.duration, 3723)
        self.assertEqual(entry.thumbnail, "https://i.example/cover.jpg")
        self.assertEqual(entry.uploader, "UP")

    def test_creator_urls_are_normalized_to_video_tabs(self) -> None:
        youtube = VideoInfo(
            video_id="v",
            title="V",
            creator_id="UC123",
            creator_url="https://www.youtube.com/@author",
        )
        bilibili = VideoInfo(
            video_id="b",
            title="B",
            source_site="bilibili",
            creator_id="123",
            creator_url="https://space.bilibili.com/123",
        )

        self.assertEqual(_creator_videos_url(youtube), "https://www.youtube.com/@author/videos")
        self.assertEqual(_creator_videos_url(bilibili), "https://space.bilibili.com/123/video")

    def test_stale_request_token_is_rejected(self) -> None:
        state = SimpleNamespace(
            _creator_playlist_generation=5,
            current_video=SimpleNamespace(video_id="current"),
            current_playlist=None,
        )

        self.assertTrue(MainWindow._is_creator_playlist_request_current(state, 5, "current"))
        self.assertFalse(MainWindow._is_creator_playlist_request_current(state, 4, "current"))
        state.current_playlist = PlaylistInfo("p", "P", "")
        self.assertFalse(MainWindow._is_creator_playlist_request_current(state, 5, "current"))

    def test_current_creator_request_always_reports_completion(self) -> None:
        messages: list[str] = []
        applied: list[PlaylistInfo] = []
        state = SimpleNamespace(
            _creator_playlist_generation=5,
            current_video=SimpleNamespace(video_id="current"),
            current_playlist=None,
            current_playlist_auto_play=True,
            toast=SimpleNamespace(show_message=messages.append),
            _find_playlist_index=MainWindow._find_playlist_index,
            _activate_playlist=lambda playlist, **_kwargs: applied.append(playlist),
        )
        state._is_creator_playlist_request_current = MethodType(
            MainWindow._is_creator_playlist_request_current,
            state,
        )
        playlist = PlaylistInfo(
            "p",
            "Author 的视频",
            "https://example.com",
            entries=[
                _entry("current", "https://example.com/current"),
                _entry("other", "https://example.com/other"),
            ],
        )

        MainWindow._creator_playlist_loaded(state, 5, "current", playlist)
        MainWindow._creator_playlist_loaded(state, 5, "current", None)

        self.assertEqual(applied, [playlist])
        self.assertEqual(messages[0], "已加载制作者视频列表，共 2 条")
        self.assertEqual(messages[1], "未找到该制作者的其他可用视频")

    def test_missing_creator_identity_reports_failure(self) -> None:
        messages: list[str] = []
        state = SimpleNamespace(toast=SimpleNamespace(show_message=messages.append))
        video = VideoInfo("video", "Video", source_site="youtube")

        MainWindow._schedule_creator_playlist(state, video)

        self.assertEqual(messages, ["无法识别视频制作者，未加载作者视频列表"])


class _CountingThumbnailCache:
    def __init__(self) -> None:
        self.calls = 0

    def load(self, *_args, **_kwargs) -> None:
        self.calls += 1


class PlaylistThumbnailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_thumbnail_is_lazy_and_requested_only_once(self) -> None:
        cache = _CountingThumbnailCache()
        widget = PlaylistItemWidget(
            _entry("video", "https://www.youtube.com/watch?v=video"),
            0,
            QNetworkAccessManager(),
            cache,
        )

        self.assertEqual(cache.calls, 0)
        widget.ensure_thumbnail_loaded()
        widget.ensure_thumbnail_loaded()
        self.assertEqual(cache.calls, 1)


class _CreatorWorkerReceiver(QObject):
    def __init__(self, loop: QEventLoop) -> None:
        super().__init__()
        self.loop = loop
        self.result = None

    @Slot(int, str, object)
    def receive(self, generation: int, video_id: str, playlist: PlaylistInfo | None) -> None:
        self.result = (generation, video_id, playlist)
        self.loop.quit()


class CreatorWorkerSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_worker_result_is_delivered_to_bound_receiver(self) -> None:
        loop = QEventLoop()
        receiver = _CreatorWorkerReceiver(loop)
        worker_holder: list[CreatorVideosWorker] = []
        thread_pool = QThreadPool.globalInstance()
        resolver = SimpleNamespace(
            resolve_creator_playlist=lambda _video, _limit: (
                time.sleep(0.05) or PlaylistInfo("p", "P", "https://example.com")
            )
        )

        def start_worker() -> None:
            worker = CreatorVideosWorker(
                resolver,
                VideoInfo("video", "Video", creator_id="creator"),
                generation=7,
            )
            worker_holder.append(worker)
            worker.signals.success.connect(receiver.receive)
            thread_pool.start(worker, -1)

        start_worker()
        QTimer.singleShot(2000, loop.quit)
        loop.exec()
        self.assertTrue(thread_pool.waitForDone(1000))

        self.assertIsNotNone(receiver.result)
        self.assertEqual(receiver.result[:2], (7, "video"))


if __name__ == "__main__":
    unittest.main()
