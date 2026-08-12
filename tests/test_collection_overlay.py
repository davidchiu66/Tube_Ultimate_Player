from __future__ import annotations

import os
import threading
import unittest
from collections import OrderedDict
from types import MethodType, SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from resolver.models import PlaylistEntry, PlaylistInfo, VideoInfo
from resolver.site_resolver import BilibiliResolver, SiteResolver, _collection_base_key
from resolver.youtube_resolver import YoutubeResolver
from ui.main_window import MainWindow
from ui.player_page import PlayerPage
from ui.playlist_overlay import MIN_PANEL_WIDTH, PANEL_MARGIN, PANEL_WIDTH, PlaylistOverlay


def _entry(video_id: str, *, site: str = "bilibili") -> PlaylistEntry:
    return PlaylistEntry(
        playlist_id="collection",
        video_id=video_id,
        title=video_id,
        webpage_url=f"https://example.com/{video_id}",
        source_site=site,
    )


def _collection(*video_ids: str) -> PlaylistInfo:
    return PlaylistInfo(
        playlist_id="bilibili:ugcseason:9",
        title="测试合集",
        webpage_url="https://space.bilibili.com/1/channel/collectiondetail?sid=9",
        source_site="bilibili",
        source_type="collection",
        entries=[_entry(video_id) for video_id in video_ids],
    )


def _view_payload(*, with_season: bool, pages: int = 1) -> dict:
    data: dict = {
        "title": "稿件标题",
        "pic": "//i.example/cover.jpg",
        "owner": {"name": "UP主"},
        "pages": [
            {"page": index, "part": f"第 {index} 页", "duration": 60 + index}
            for index in range(1, pages + 1)
        ],
    }
    if with_season:
        data["ugc_season"] = {
            "id": 9,
            "mid": 1,
            "title": "测试合集",
            "cover": "//i.example/season.jpg",
            "sections": [
                {
                    "episodes": [
                        {
                            "bvid": "BV1aaaaaaaaa",
                            "aid": 11,
                            "title": "第一集",
                            "arc": {"duration": 100, "pic": "//i.example/1.jpg"},
                            "page": {"page": 1},
                        },
                        {
                            "bvid": "BV1bbbbbbbbb",
                            "aid": 12,
                            "title": "第二集",
                            "arc": {"duration": 200, "pic": "//i.example/2.jpg"},
                            "page": {"page": 1},
                        },
                    ]
                }
            ],
        }
    return {"data": data}


class _RecordingBilibiliResolver(BilibiliResolver):
    def __init__(self, payload: dict) -> None:  # noqa: D107 - 只为测试构造
        self.payload = payload
        self.requests: list[str] = []

    def _request_json(self, url: str, **_kwargs) -> dict:
        self.requests.append(url)
        return self.payload


class BilibiliCollectionTests(unittest.TestCase):
    @staticmethod
    def _video(url: str) -> VideoInfo:
        return VideoInfo(video_id="bilibili:BV1bbbbbbbbb", title="第二集", source_site="bilibili", webpage_url=url)

    def test_ugc_season_becomes_a_collection(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=True))

        playlist = resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb"))

        self.assertIsNotNone(playlist)
        self.assertEqual(playlist.playlist_id, "bilibili:ugcseason:9")
        self.assertEqual(playlist.source_type, "collection")
        self.assertEqual([item.video_id for item in playlist.entries], ["bilibili:BV1aaaaaaaaa", "bilibili:BV1bbbbbbbbb"])
        self.assertEqual([item.position for item in playlist.entries], [1, 2])
        self.assertEqual(playlist.entries[0].thumbnail, "https://i.example/1.jpg")

    def test_collection_url_is_stable_across_episodes(self) -> None:
        # 合集地址不能是"当前这一集"的地址，否则保存后换集就认不出是同一个合集。
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=True))

        playlist = resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb"))

        self.assertEqual(playlist.webpage_url, "https://space.bilibili.com/1/channel/collectiondetail?sid=9")

    def test_current_episode_is_highlighted(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=True))

        playlist = resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb?p=1"))

        self.assertEqual(playlist.current_video_id, "bilibili:BV1bbbbbbbbb")

    def test_only_one_view_request_is_issued(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=False, pages=3))

        resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb"))

        self.assertEqual(len(resolver.requests), 1)

    def test_multi_page_video_falls_back_to_pages(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=False, pages=3))

        playlist = resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb"))

        self.assertIsNotNone(playlist)
        self.assertEqual(playlist.playlist_id, "bilibili:pages:BV1bbbbbbbbb")
        self.assertEqual(playlist.source_type, "collection")
        self.assertEqual(len(playlist.entries), 3)
        self.assertTrue(all(item.playlist_id == "bilibili:pages:BV1bbbbbbbbb" for item in playlist.entries))

    def test_single_page_video_without_season_has_no_collection(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=False, pages=1))

        self.assertIsNone(resolver.resolve_collection(self._video("https://www.bilibili.com/video/BV1bbbbbbbbb")))

    def test_video_without_identifier_never_requests(self) -> None:
        resolver = _RecordingBilibiliResolver(_view_payload(with_season=True))

        self.assertIsNone(resolver.resolve_collection(self._video("https://www.bilibili.com/")))
        self.assertEqual(resolver.requests, [])

    def test_page_suffix_is_ignored_when_matching(self) -> None:
        self.assertEqual(_collection_base_key("bilibili:BV1a:p2"), "bilibili:BV1a")
        self.assertEqual(_collection_base_key("bilibili:BV1a"), "bilibili:BV1a")
        self.assertEqual(_collection_base_key("youtube:pcast"), "youtube:pcast")


class YoutubeCollectionTests(unittest.TestCase):
    @staticmethod
    def _resolver(playlist: PlaylistInfo) -> YoutubeResolver:
        resolver = YoutubeResolver.__new__(YoutubeResolver)
        resolver.resolve_playlist_generic = lambda _url: playlist
        return resolver

    def test_list_parameter_drives_the_collection(self) -> None:
        source = PlaylistInfo(
            playlist_id="PL123",
            title="Mix",
            webpage_url="https://www.youtube.com/playlist?list=PL123",
            source_site="youtube",
            entries=[_entry("abc", site="youtube"), _entry("def", site="youtube")],
        )
        resolver = self._resolver(source)
        video = VideoInfo(
            video_id="abc",
            title="Abc",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc&list=PL123",
        )

        playlist = resolver.resolve_collection(video)

        self.assertEqual(playlist.playlist_id, "youtube:playlist:PL123")
        self.assertEqual(playlist.source_type, "collection")
        self.assertEqual(playlist.current_video_id, "abc")
        self.assertTrue(all(item.playlist_id == "youtube:playlist:PL123" for item in playlist.entries))

    def test_plain_video_has_no_collection(self) -> None:
        resolver = self._resolver(PlaylistInfo("PL", "Mix", ""))
        video = VideoInfo(
            video_id="abc",
            title="Abc",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc",
        )

        self.assertIsNone(resolver.resolve_collection(video))

    def test_empty_playlist_is_reported_as_no_collection(self) -> None:
        resolver = self._resolver(PlaylistInfo("PL123", "Mix", "", entries=[]))
        video = VideoInfo(
            video_id="abc",
            title="Abc",
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc&list=PL123",
        )

        self.assertIsNone(resolver.resolve_collection(video))


class _CountingCollectionSource:
    def __init__(self, playlist: PlaylistInfo | None) -> None:
        self.playlist = playlist
        self.calls = 0

    def resolve_collection(self, _video: VideoInfo) -> PlaylistInfo | None:
        self.calls += 1
        return self.playlist


class CollectionCacheTests(unittest.TestCase):
    @staticmethod
    def _site_resolver(source) -> SiteResolver:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.youtube = source
        resolver.bilibili = source
        resolver._collection_cache = OrderedDict()
        resolver._collection_cache_lock = threading.Lock()
        resolver._config_fingerprint = lambda source="": "test"
        return resolver

    @staticmethod
    def _video() -> VideoInfo:
        return VideoInfo(
            video_id="bilibili:BV1bbbbbbbbb",
            title="第二集",
            source_site="bilibili",
            webpage_url="https://www.bilibili.com/video/BV1bbbbbbbbb",
        )

    def test_result_is_cached_and_copied(self) -> None:
        source = _CountingCollectionSource(_collection("a", "b"))
        resolver = self._site_resolver(source)

        first = resolver.resolve_collection_playlist(self._video())
        second = resolver.resolve_collection_playlist(self._video())

        self.assertEqual(source.calls, 1)
        self.assertEqual([item.video_id for item in second.entries], ["a", "b"])
        self.assertIsNot(first, second)
        self.assertIsNot(first.entries[0], second.entries[0])

    def test_absence_of_a_collection_is_cached_too(self) -> None:
        source = _CountingCollectionSource(None)
        resolver = self._site_resolver(source)

        self.assertIsNone(resolver.resolve_collection_playlist(self._video()))
        self.assertIsNone(resolver.resolve_collection_playlist(self._video()))
        self.assertEqual(source.calls, 1)

    def test_unknown_site_is_skipped(self) -> None:
        source = _CountingCollectionSource(_collection("a"))
        resolver = self._site_resolver(source)
        video = VideoInfo(video_id="x", title="X", source_site="vimeo", webpage_url="https://vimeo.com/1")

        self.assertIsNone(resolver.resolve_collection_playlist(video))
        self.assertEqual(source.calls, 0)


class CollectionOverlayGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.page.resize(1200, 700)
        self.left = self.page.collection_overlay
        self.right = self.page.playlist_overlay

    def tearDown(self) -> None:
        self.page.close()

    def test_left_panel_is_named_and_titled_for_collections(self) -> None:
        self.assertEqual(self.left.objectName(), "CollectionOverlay")
        self.assertEqual(self.left.list_widget.objectName(), "CollectionOverlayList")
        self.assertEqual(self.left.title_label.text(), "合集列表")
        self.assertEqual(self.left.meta_label.text(), "当前视频不属于任何合集")

    def test_left_hot_zone_is_on_the_left_edge(self) -> None:
        self.assertTrue(self.left._is_in_hot_zone(QPoint(4, 300)))
        self.assertFalse(self.left._is_in_hot_zone(QPoint(600, 300)))
        self.assertTrue(self.right._is_in_hot_zone(QPoint(1196, 300)))
        self.assertFalse(self.right._is_in_hot_zone(QPoint(4, 300)))

    def test_left_panel_slides_in_from_the_left(self) -> None:
        self.page.set_collection_available(True)

        self.left.show_overlay(animated=False)
        self.assertEqual(self.left.x(), PANEL_MARGIN)

        self.left.hide_overlay(animated=False)
        self.assertLess(self.left.x(), 0)

    def test_panels_are_mutually_exclusive(self) -> None:
        self.page.set_collection_available(True)
        self.right.set_context_available(True)

        self.left.show_overlay(animated=False)
        self.assertTrue(self.left.is_open())

        self.right.show_overlay(animated=False)
        self.assertTrue(self.right.is_open())
        self.assertFalse(self.left.is_open())

    def test_empty_panel_can_still_open_when_context_is_available(self) -> None:
        self.assertFalse(self.left.has_available_content())

        self.page.set_collection_available(True)
        self.left.show_overlay(animated=False)
        self.assertTrue(self.left.is_open())

        self.page.set_collection_available(False)
        self.assertFalse(self.left.is_open())

    def test_narrow_host_clamps_the_panel_and_item_hints(self) -> None:
        self.page.set_collection_context(_collection("a", "b"), current_index=0)
        # 隐藏窗口不会派发 resizeEvent，显示出来才能走真实的 resize → relayout 链路。
        self.page.show()

        self.page.resize(600, 500)
        self.assertEqual(self.left.width(), max(MIN_PANEL_WIDTH, 600 // 2 - 20))
        self.assertEqual(self.left.list_widget.item(0).sizeHint().width(), self.left.width() - 28)

        self.page.resize(1600, 500)
        self.assertEqual(self.left.width(), PANEL_WIDTH)
        self.assertEqual(self.left.list_widget.item(0).sizeHint().width(), PANEL_WIDTH - 28)

    def test_both_panels_relayout_on_fullscreen_switch(self) -> None:
        self.page.resize(700, 500)

        self.page.set_fullscreen(True)

        expected = max(MIN_PANEL_WIDTH, 700 // 2 - 20)
        self.assertEqual(self.left.width(), expected)
        self.assertEqual(self.right.width(), expected)

    def test_collection_signals_reach_the_page(self) -> None:
        activated: list[int] = []
        auto_play: list[bool] = []
        self.page.collection_entry_requested.connect(activated.append)
        self.page.collection_auto_play_changed.connect(auto_play.append)

        self.left.entry_activated.emit(3)
        self.left.auto_play_checkbox.setChecked(True)

        self.assertEqual(activated, [3])
        self.assertEqual(auto_play, [True])

    def test_shortcut_step_follows_the_collection_when_no_playlist(self) -> None:
        steps: list[int] = []
        self.page.collection_entry_requested.connect(steps.append)
        self.page._shortcut_context_active = lambda: True
        self.page.set_collection_context(_collection("a", "b"), current_index=0)

        self.page._shortcut_playlist_step(1)

        self.assertEqual(steps, [1])


class MainWindowCollectionWiringTests(unittest.TestCase):
    @staticmethod
    def _state(**overrides):
        activated: list[tuple] = []
        cleared: list[bool] = []
        state = SimpleNamespace(
            _collection_generation=3,
            current_video=SimpleNamespace(video_id="vid"),
            current_collection=None,
            current_collection_index=-1,
            current_collection_key="",
            current_collection_auto_play=False,
            _active_queue="",
            playlists=SimpleNamespace(all_playlists=lambda: []),
            _find_playlist_index=MainWindow._find_playlist_index,
            _activate_collection=lambda playlist, **kwargs: activated.append((playlist, kwargs)),
            _start_collection_worker=lambda *_a, **_k: None,
            _activated=activated,
            _cleared=cleared,
        )
        state._clear_collection_context = lambda *, keep_available=False: cleared.append(keep_available)
        state._is_collection_request_current = MethodType(MainWindow._is_collection_request_current, state)
        state._find_saved_collection = MethodType(MainWindow._find_saved_collection, state)
        for key, value in overrides.items():
            setattr(state, key, value)
        return state

    def test_collection_result_is_applied_with_the_current_index(self) -> None:
        state = self._state()
        playlist = _collection("other", "vid")

        MainWindow._collection_loaded(state, 3, "vid", playlist)

        applied, kwargs = state._activated[0]
        self.assertIs(applied, playlist)
        self.assertEqual(kwargs["current_index"], 1)
        self.assertEqual(kwargs["collection_key"], "")

    def test_missing_collection_keeps_the_panel_available_for_the_empty_state(self) -> None:
        state = self._state()

        MainWindow._collection_loaded(state, 3, "vid", None)

        self.assertEqual(state._activated, [])
        self.assertEqual(state._cleared, [True])

    def test_stale_result_is_dropped(self) -> None:
        state = self._state()

        MainWindow._collection_loaded(state, 2, "vid", _collection("vid"))
        MainWindow._collection_loaded(state, 3, "other-video", _collection("vid"))

        self.assertEqual(state._activated, [])
        self.assertEqual(state._cleared, [])

    def test_saved_collection_is_reused_for_the_same_source(self) -> None:
        saved = SimpleNamespace(
            playlist_key="key-1",
            source_type="collection",
            source_url="https://space.bilibili.com/1/channel/collectiondetail?sid=9",
            auto_play_next=True,
        )
        state = self._state(playlists=SimpleNamespace(all_playlists=lambda: [saved]))

        MainWindow._collection_loaded(state, 3, "vid", _collection("vid"))

        _applied, kwargs = state._activated[0]
        self.assertEqual(kwargs["collection_key"], "key-1")
        self.assertTrue(kwargs["auto_play_next"])

    def test_probe_failure_is_silent(self) -> None:
        messages: list[str] = []
        state = self._state(toast=SimpleNamespace(show_message=messages.append))

        MainWindow._collection_failed(state, 3, "vid", "boom")

        self.assertEqual(messages, [])
        self.assertEqual(state._cleared, [True])

    def test_probe_keeps_the_panel_when_still_inside_the_collection(self) -> None:
        moved: list[int] = []
        state = self._state(
            current_collection=_collection("a", "vid"),
            player_page=SimpleNamespace(set_collection_current_index=moved.append),
            resolver=SimpleNamespace(normalize_source=SiteResolver.normalize_source),
        )
        video = VideoInfo(video_id="vid", title="Vid", source_site="bilibili")

        MainWindow._schedule_collection_probe(state, video)

        self.assertEqual(moved, [1])
        self.assertEqual(state.current_collection_index, 1)
        self.assertEqual(state._cleared, [])

    def test_probe_collapses_the_panel_when_leaving_the_collection(self) -> None:
        state = self._state(
            current_collection=_collection("a", "b"),
            player_page=SimpleNamespace(set_collection_current_index=lambda _index: None),
            resolver=SimpleNamespace(normalize_source=SiteResolver.normalize_source),
        )
        video = VideoInfo(video_id="vid", title="Vid", source_site="bilibili")

        MainWindow._schedule_collection_probe(state, video)

        self.assertEqual(state._cleared, [False])

    def test_only_collection_typed_saves_reach_the_left_panel(self) -> None:
        saved_playlist = SimpleNamespace(playlist_key="p", source_type="playlist", name="列表")
        saved_collection = SimpleNamespace(playlist_key="c", source_type="collection", name="合集")
        left: list[tuple] = []
        right: list[tuple] = []
        state = SimpleNamespace(
            playlists=SimpleNamespace(all_playlists=lambda: [saved_playlist, saved_collection]),
            current_playlist_key="p",
            current_collection_key="c",
            _created_page=lambda _name: None,
            player_page=SimpleNamespace(
                set_playlist_saved_items=lambda items, key: right.append((items, key)),
                set_collection_saved_items=lambda items, key: left.append((items, key)),
            ),
        )

        MainWindow._refresh_saved_playlists(state)

        self.assertEqual(right[0][0], [saved_playlist, saved_collection])
        self.assertEqual(left[0], ([saved_collection], "c"))


class AutoplayArbitrationTests(unittest.TestCase):
    @staticmethod
    def _state(active_queue: str):
        played: list[str] = []
        return SimpleNamespace(
            _active_queue=active_queue,
            current_playlist=_collection("p1", "p2"),
            current_playlist_index=0,
            current_playlist_auto_play=True,
            current_collection=_collection("c1", "c2"),
            current_collection_index=0,
            current_collection_auto_play=True,
            _play_playlist_entry=lambda *_a, **_k: played.append("playlist"),
            _play_collection_entry=lambda *_a, **_k: played.append("collection"),
            _set_playback_finished=lambda _value: played.append("stop"),
            _played=played,
        )

    def _finish(self, state) -> None:
        state._advance_playlist_queue = MethodType(MainWindow._advance_playlist_queue, state)
        state._advance_collection_queue = MethodType(MainWindow._advance_collection_queue, state)
        MainWindow._handle_playback_finished(state)

    def test_collection_drives_autoplay_when_it_started_playback(self) -> None:
        state = self._state("collection")

        self._finish(state)

        self.assertEqual(state._played, ["collection"])

    def test_playlist_keeps_priority_by_default(self) -> None:
        state = self._state("")

        self._finish(state)

        self.assertEqual(state._played, ["playlist"])

    def test_collection_is_the_fallback_when_the_playlist_is_exhausted(self) -> None:
        state = self._state("playlist")
        state.current_playlist_index = 1

        self._finish(state)

        self.assertEqual(state._played, ["collection"])

    def test_autoplay_off_on_both_sides_stops(self) -> None:
        state = self._state("collection")
        state.current_playlist_auto_play = False
        state.current_collection_auto_play = False

        self._finish(state)

        self.assertEqual(state._played, ["stop"])


if __name__ == "__main__":
    unittest.main()
