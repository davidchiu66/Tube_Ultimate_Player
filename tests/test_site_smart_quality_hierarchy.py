from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from database.playlist_repository import PlaylistRepository
from database.sqlite_manager import SQLiteManager
from resolver.models import PlaybackQualityHint, PlaylistInfo, PlaylistSection, VideoQuality
from resolver.quality_selector import select_quality_by_hint
from resolver.site_resolver import BilibiliResolver
from services.config_service import ConfigService
from services.network_quality_service import NetworkMeasurement, NetworkMeasurementCache, select_quality_for_bandwidth
from ui.main_window import MainWindow
from ui.playlist_overlay import PlaylistOverlay
from ui.settings_page import SettingsPage


def quality(label: str, height: int, fps: int = 30, tbr: float | None = None) -> VideoQuality:
    return VideoQuality(label, height, height * 16 // 9, fps, "h264", "aac", "mp4", label, f"https://cdn.test/{label}", tbr=tbr)


class SiteConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_site_values_are_isolated_and_legacy_youtube_fallback_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "default.json"
            default_path.write_text(json.dumps({"player": {"default_quality": "medium"}, "content": {"default_home": "bilibili"}, "youtube": {"cookie_browser": "edge", "cookie_browser_profile": "Profile 2"}}), encoding="utf-8")
            config = ConfigService(default_path, root / "user.json")
            self.assertEqual(config.default_quality_mode("youtube"), "medium")
            self.assertEqual(config.explicit_cookie_browser_for_site("youtube"), "edge:Profile 2")
            self.assertEqual(config.explicit_cookie_browser_for_site("bilibili"), "")
            config.set("player.default_quality_by_site.youtube", "smart")
            config.set("player.default_quality_by_site.bilibili", "high")
            config.set("cookies.bilibili.browser", "firefox")
            self.assertEqual(config.default_quality_mode("youtube"), "smart")
            self.assertEqual(config.default_quality_mode("bilibili"), "high")
            self.assertEqual(config.explicit_cookie_browser_for_site("bilibili"), "firefox")

    def test_settings_switch_keeps_independent_site_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            defaults = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
            defaults["download"]["save_dir"] = str(root / "downloads")
            default_path = root / "default.json"
            default_path.write_text(json.dumps(defaults), encoding="utf-8")
            config = ConfigService(default_path, root / "user.json")
            page = SettingsPage(config)
            self.addCleanup(page.deleteLater)
            page.default_quality_combo.setCurrentIndex(page.default_quality_combo.findData("low"))
            page.site_config_youtube.setChecked(True)
            page.default_quality_combo.setCurrentIndex(page.default_quality_combo.findData("smart"))
            page.save()
            self.assertEqual(config.default_quality_mode("bilibili"), "low")
            self.assertEqual(config.default_quality_mode("youtube"), "smart")
            self.assertEqual(config.get("player.default_quality"), "low")


class QualitySelectionTests(unittest.TestCase):
    def test_inherited_quality_uses_lower_height_on_equal_distance(self) -> None:
        qualities = {"720p": quality("720p", 720), "1080p": quality("1080p", 1080)}
        selected = select_quality_by_hint(qualities, PlaybackQualityHint("900p", 900, 30))
        self.assertEqual(selected.label, "720p")

    def test_bandwidth_uses_bitrate_budget(self) -> None:
        qualities = {
            "480p": quality("480p", 480, tbr=900),
            "720p": quality("720p", 720, tbr=2200),
            "1080p": quality("1080p", 1080, tbr=5000),
        }
        self.assertEqual(select_quality_for_bandwidth(qualities, 5000).label, "720p")
        self.assertEqual(select_quality_for_bandwidth(qualities, None).label, "720p")

    def test_bandwidth_can_select_high_format_when_only_its_bitrate_is_missing(self) -> None:
        qualities = {
            "720p": quality("720p", 720, tbr=2200),
            "1080p": quality("1080p", 1080, tbr=5000),
            "2160p": quality("2160p", 2160, tbr=None),
        }
        self.assertEqual(select_quality_for_bandwidth(qualities, 50000).label, "2160p")

    def test_smart_mode_starts_probe_instead_of_reporting_no_quality(self) -> None:
        video = SimpleNamespace(
            source_site="youtube",
            webpage_url="https://www.youtube.com/watch?v=1",
            http_headers={},
            qualities={"1080p": quality("1080p", 1080)},
        )
        started: list[tuple] = []
        state = SimpleNamespace(
            config=SimpleNamespace(
                default_quality_label_override=lambda _site: "",
                default_quality_mode=lambda _site: "smart",
                effective_proxy=lambda: ("direct", ""),
            ),
            _pending_quality_hint=None,
            _pending_smart_kbps=None,
            _network_measurements=NetworkMeasurementCache(),
        )

        def start_probe(*args) -> None:
            state._pending_smart_video = video
            started.append(args)

        state._start_network_probe = start_probe
        selected = MainWindow._select_default_quality(state, video)
        self.assertIsNone(selected)
        self.assertEqual(len(started), 1)

    def test_medium_counts_actual_quality_entries(self) -> None:
        qualities = {
            "2160p": quality("2160p", 2160),
            "1080p60": quality("1080p60", 1080, 60),
            "1080p": quality("1080p", 1080, 30),
            "720p": quality("720p", 720),
            "480p": quality("480p", 480),
        }
        from resolver.quality_selector import select_quality_by_tier

        self.assertEqual(select_quality_by_tier(qualities, "medium").label, "1080p")

    def test_measurement_cache_separates_proxy_routes_and_expires(self) -> None:
        cache = NetworkMeasurementCache(ttl_seconds=0.01)
        url = "https://cdn.test/video"
        cache.put(NetworkMeasurement("youtube", "cdn.test", "proxy-a", 8000, time.monotonic()), url)
        self.assertEqual(cache.get("youtube", url, "proxy-a").kbps, 8000)
        self.assertIsNone(cache.get("youtube", url, "proxy-b"))
        time.sleep(0.02)
        self.assertIsNone(cache.get("youtube", url, "proxy-a"))


class CollectionHierarchyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _playlist(self) -> PlaylistInfo:
        season = {
            "id": 7,
            "mid": 9,
            "title": "纪录片",
            "sections": [
                {"id": 1, "title": "第一季", "episodes": [{"bvid": "BV1AA", "title": "第一集", "arc": {"duration": 60}}]},
                {"id": 2, "title": "第二季", "episodes": [{"bvid": "BV2BB", "title": "第二集", "arc": {"duration": 70}}]},
            ],
        }
        playlist = BilibiliResolver._collection_from_ugc_season(
            object.__new__(BilibiliResolver), season, "https://www.bilibili.com/video/BV1AA", bvid="BV1AA", aid=""
        )
        assert playlist is not None
        return playlist

    def test_bilibili_sections_are_preserved_with_flat_compatibility_entries(self) -> None:
        playlist = self._playlist()
        self.assertEqual([section.title for section in playlist.sections], ["第一季", "第二季"])
        self.assertEqual([entry.title for entry in playlist.entries], ["第一集", "第二集"])
        self.assertEqual(playlist.entries[1].section_id, "2")
        self.assertEqual(playlist.current_section_id, "1")

    def test_bilibili_multi_page_album_becomes_episode_level_section(self) -> None:
        season = {
            "id": 7,
            "title": "纪录片",
            "sections": [
                {
                    "id": 1,
                    "title": "正片",
                    "episodes": [
                        {
                            "id": 99,
                            "bvid": "BV1TzyEByEfY",
                            "title": "钱学森",
                            "arc": {"duration": 120, "pic": "//img.test/a.jpg"},
                            "pages": [
                                {"page": 1, "part": "钱学森 (1)", "duration": 60},
                                {"page": 2, "part": "钱学森 (2)", "duration": 60},
                            ],
                        },
                        {"id": 100, "bvid": "BV177yEBfEoZ", "title": "其他专辑", "page": {"page": 1, "duration": 70}},
                    ],
                }
            ],
        }
        playlist = BilibiliResolver._collection_from_ugc_season(
            object.__new__(BilibiliResolver), season, "https://www.bilibili.com/video/BV1TzyEByEfY", bvid="BV1TzyEByEfY", aid=""
        )
        assert playlist is not None
        self.assertEqual([section.title for section in playlist.sections], ["钱学森", "其他专辑"])
        self.assertEqual(len(playlist.sections[0].entries), 2)
        self.assertEqual(playlist.entries[0].webpage_url, "https://www.bilibili.com/video/BV1TzyEByEfY?p=1")
        self.assertEqual(playlist.current_section_id, "99")

    def test_section_metadata_round_trips_through_sqlite(self) -> None:
        playlist = self._playlist()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = PlaylistRepository(SQLiteManager(Path(temp_dir) / "test.sqlite3"))
            key = repo.save_playlist(name=playlist.title, entries=playlist.entries, source_type="collection")
            saved = repo.get_playlist(key)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.entries[0].section_title, "第一季")
        self.assertEqual(saved.entries[1].section_position, 2)

    def test_overlay_opens_section_before_emitting_episode(self) -> None:
        playlist = self._playlist()
        overlay = PlaylistOverlay(side="left")
        self.addCleanup(overlay.deleteLater)
        played: list[int] = []
        overlay.entry_activated.connect(played.append)
        overlay.set_playlist(playlist)
        self.assertEqual(overlay.list_widget.count(), 2)
        overlay._open_section(1)
        self.assertEqual(overlay.title_label.text(), "第二季")
        item = overlay.list_widget.item(0)
        overlay._double_clicked(item)
        self.assertEqual(played, [1])

    def test_overlay_auto_opens_current_video_section(self) -> None:
        playlist = self._playlist()
        overlay = PlaylistOverlay(side="left")
        self.addCleanup(overlay.deleteLater)
        overlay.set_playlist(playlist, current_index=1)
        self.assertEqual(overlay.title_label.text(), "第二季")
        self.assertEqual(overlay.back_button.text(), "返回合集")
        self.assertEqual(overlay.list_widget.count(), 1)
        self.assertEqual(overlay._active_section_id, "2")


if __name__ == "__main__":
    unittest.main()
