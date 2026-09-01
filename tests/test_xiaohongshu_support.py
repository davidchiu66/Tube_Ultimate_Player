from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from download.command_builder import build_download_task
from resolver.site_resolver import SiteResolver
from resolver.youtube_resolver import _current_video_id_for_site
from services.config_service import ConfigService
from services.logging_service import sanitize_command
from services.site_registry import site_for_url, site_label
from ui.startup_cookie_guide_dialog import StartupCookieGuideDialog
from ui.toolbar import PlayerToolbar


def _home_item(note_id: str = "6a75f708000000002701ede8") -> dict:
    return {
        "id": note_id,
        "type": "video",
        "title": "测试视频",
        "desc": "说明",
        "user": {"nickname": "测试作者", "user_id": "u1"},
        "images_list": [{"url": "https://example.test/cover.jpg"}],
        "xsec_token": "secret-token",
    }


class XiaohongshuSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _config(self, root: Path) -> ConfigService:
        return ConfigService(
            default_path=Path("config/default_config.json"),
            user_path=root / "user.json",
        )

    def test_site_detection_and_stable_id(self) -> None:
        url = "https://www.xiaohongshu.com/explore/6a75f708000000002701ede8"
        self.assertEqual(site_for_url(url), "xiaohongshu")
        self.assertEqual(site_for_url("https://xhslink.com/a"), "xiaohongshu")
        self.assertEqual(site_label("xiaohongshu"), "小红书")
        self.assertEqual(
            _current_video_id_for_site(url, "xiaohongshu"),
            "xiaohongshu:6a75f708000000002701ede8",
        )

    def test_compact_toolbar_uses_single_letter_site_labels(self) -> None:
        toolbar = PlayerToolbar()
        labels = [toolbar.source_selector._radios[key].text() for key in ("bilibili", "youtube", "douyin", "tiktok", "xiaohongshu")]
        self.assertEqual(labels, ["B", "Y", "D", "T", "X"])

    def test_home_item_keeps_security_context_and_metadata(self) -> None:
        video = SiteResolver._xiaohongshu_home_item(_home_item(), context="home")

        self.assertIsNotNone(video)
        self.assertEqual(video.video_id, "xiaohongshu:6a75f708000000002701ede8")
        self.assertEqual(video.uploader, "测试作者")
        self.assertEqual(video.thumbnail, "https://example.test/cover.jpg")
        self.assertIn("xsec_token=secret-token", video.webpage_url)
        self.assertIn("xsec_source=pc_feed", video.webpage_url)

    def test_search_item_uses_video_filter_context(self) -> None:
        item = {
            "id": "6a75f708000000002701ede9",
            "xsec_token": "search-token",
            "note_card": {
                "type": "video",
                "display_title": "搜索视频",
                "user": {"nickname": "搜索作者"},
                "cover": {"url_default": "https://example.test/search.jpg"},
            },
        }
        video = SiteResolver._xiaohongshu_home_item(item, context="search")

        self.assertEqual(video.title, "搜索视频")
        self.assertIn("xsec_source=pc_search", video.webpage_url)

    def test_non_video_note_is_filtered(self) -> None:
        item = _home_item()
        item["type"] = "normal"
        self.assertIsNone(SiteResolver._xiaohongshu_home_item(item, context="home"))

    def test_home_and_search_use_browser_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            items = [_home_item(f"6a75f708000000002701ed{index:02x}") for index in range(20)]
            calls: list[tuple] = []
            resolver.set_xiaohongshu_browser_client(
                SimpleNamespace(
                    request_home=lambda page, size, **kwargs: (
                        calls.append(("home", page, size, kwargs)),
                        {"items": items, "has_more": True},
                    )[1],
                    request_search=lambda keyword, page, size, **kwargs: (
                        calls.append(("search", keyword, page, size, kwargs)),
                        {"items": items, "has_more": False},
                    )[1],
                )
            )

            home, home_more = resolver.fetch_home_videos(1, 56, source="xiaohongshu")
            search, search_more = resolver.search_videos("测试", 1, 56, source="xiaohongshu")

            self.assertEqual(len(home), 20)
            self.assertTrue(home_more)
            self.assertEqual(len(search), 20)
            self.assertFalse(search_more)
            self.assertEqual(calls[0][:3], ("home", 1, 20))
            self.assertEqual(calls[1][:4], ("search", "测试", 1, 20))

    def test_browser_detail_fallback_builds_playable_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            payload = {
                "url": "https://www.xiaohongshu.com/explore/6a75f708000000002701ede8",
                "note": {
                    "noteId": "6a75f708000000002701ede8",
                    "title": "详情视频",
                    "desc": "详情",
                    "user": {"userId": "u1", "nickname": "作者"},
                    "imageList": [{"urlDefault": "https://example.test/cover.jpg"}],
                    "video": {
                        "media": {
                            "stream": {
                                "h264": [{
                                    "masterUrl": "https://example.test/video.mp4",
                                    "width": 1080,
                                    "height": 1920,
                                    "fps": 30,
                                    "videoCodec": "h264",
                                    "audioCodec": "aac",
                                    "duration": 30000,
                                    "size": 12345,
                                }]
                            }
                        }
                    },
                },
            }

            video = resolver._video_info_from_xiaohongshu_detail(payload, payload["url"])

            self.assertEqual(video.source_site, "xiaohongshu")
            self.assertEqual(video.video_id, "xiaohongshu:6a75f708000000002701ede8")
            self.assertTrue(video.qualities)
            self.assertTrue(video.raw_info["_tube_player_browser_fallback"])

            label = next(iter(video.qualities))
            task = build_download_task(video, label, resolver.config)
            self.assertEqual(task.source_site, "xiaohongshu")
            self.assertEqual(task.download_url, "https://example.test/video.mp4")

    def test_creator_playlist_maps_user_posted_video_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            current = resolver._xiaohongshu_home_item(_home_item(), context="home")
            current_info = resolver._video_info_from_xiaohongshu_detail(
                {
                    "url": current.webpage_url,
                    "note": {
                        "noteId": "6a75f708000000002701ede8",
                        "title": current.title,
                        "user": {"userId": "632ac779000000002303b89a", "nickname": "测试作者"},
                        "video": {"media": {"stream": {"h264": [{"masterUrl": "https://example.test/v.mp4", "width": 720, "height": 1280}]}}},
                    },
                },
                current.webpage_url,
            )
            current_info.creator_id = "632ac779000000002303b89a"
            current_info.channel_id = current_info.creator_id
            current_info.creator_url = "https://www.xiaohongshu.com/user/profile/632ac779000000002303b89a"
            item = _home_item("6a75f708000000002701edef")
            item["user"] = {"userId": current_info.creator_id, "nickname": "测试作者"}
            resolver.set_xiaohongshu_browser_client(
                SimpleNamespace(request_creator=lambda *_args, **_kwargs: {"items": [item], "has_more": False})
            )

            creator_url, entries = resolver._fetch_xiaohongshu_creator_videos(current_info, 10)

            self.assertIn("/user/profile/632ac779000000002303b89a", creator_url)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source_site, "xiaohongshu")
            self.assertEqual(entries[0].uploader, "测试作者")

    def test_creator_playlist_does_not_reuse_feed_token_for_user_endpoint(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._xiaohongshu_item_store = lambda *_args: None
        item = _home_item("6a75f708000000002701edef")
        item["user"] = {"userId": "u1", "nickname": "作者"}
        calls: list[dict] = []
        resolver._xiaohongshu_browser_client = SimpleNamespace(
            request_creator=lambda *args, **kwargs: (
                calls.append(kwargs), {"items": [item], "has_more": False}
            )[1]
        )
        video = SimpleNamespace(
            source_site="xiaohongshu",
            creator_id="u1",
            channel_id="u1",
            creator_url="",
            webpage_url="https://www.xiaohongshu.com/explore/abc?xsec_source=pc_feed&xsec_token=feed-token",
            video_id="xiaohongshu:current",
            title="当前",
            uploader="作者",
            duration=1,
            thumbnail="",
            raw_info={},
        )

        resolver._fetch_xiaohongshu_creator_videos(video, 10)

        self.assertEqual(calls[0]["token"], "")
        self.assertEqual(calls[0]["source"], "pc_user")

    def test_creator_empty_first_response_is_retried(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver._xiaohongshu_item_store = lambda *_args: None
        calls = []
        item = _home_item("6a75f708000000002701edf0")
        item["user"] = {"userId": "u1", "nickname": "作者"}
        resolver._xiaohongshu_browser_client = SimpleNamespace(
            request_creator=lambda *args, **kwargs: (
                calls.append(kwargs), {"items": [] if len(calls) == 1 else [item], "has_more": False}
            )[1]
        )
        video = SimpleNamespace(
            source_site="xiaohongshu", creator_id="u1", channel_id="u1", creator_url="",
            webpage_url="https://www.xiaohongshu.com/explore/abc?xsec_source=pc_feed",
        )

        _url, entries = resolver._fetch_xiaohongshu_creator_videos(video, 10)

        self.assertEqual(len(calls), 2)
        self.assertEqual(len(entries), 1)

    def test_creator_profile_url_is_detected_as_playlist(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        self.assertEqual(
            resolver.detect_url_kind(
                "https://www.xiaohongshu.com/user/profile/632ac779000000002303b89a"
                "?xsec_token=token&xsec_source=pc_user"
            ),
            "playlist",
        )

    def test_creator_profile_playlist_uses_browser_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            item = _home_item("6a75f708000000002701edef")
            item["user"] = {"userId": "632ac779000000002303b89a", "nickname": "作者"}
            resolver.set_xiaohongshu_browser_client(
                SimpleNamespace(request_creator=lambda *_args, **_kwargs: {"items": [item], "has_more": False})
            )

            playlist = resolver.resolve_playlist(
                "https://www.xiaohongshu.com/user/profile/632ac779000000002303b89a"
                "?xsec_token=token&xsec_source=pc_user"
            )

            self.assertEqual(playlist.source_site, "xiaohongshu")
            self.assertEqual(len(playlist.entries), 1)
            self.assertEqual(playlist.entries[0].uploader, "作者")

    def test_resolve_creator_playlist_dispatches_xiaohongshu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            item = _home_item("6a75f708000000002701edf1")
            item["user"] = {"userId": "u1", "nickname": "作者"}
            resolver.set_xiaohongshu_browser_client(
                SimpleNamespace(request_creator=lambda *_args, **_kwargs: {"items": [item], "has_more": False})
            )
            video = SimpleNamespace(
                source_site="xiaohongshu", creator_id="u1", channel_id="u1", creator_url="",
                webpage_url="https://www.xiaohongshu.com/explore/abc?xsec_source=pc_feed",
                video_id="xiaohongshu:current", title="当前", uploader="作者", duration=1,
                thumbnail="", raw_info={},
            )

            playlist = resolver.resolve_creator_playlist(video, limit=10)

            self.assertIsNotNone(playlist)
            self.assertEqual(len(playlist.entries), 2)
            self.assertEqual(playlist.source_site, "xiaohongshu")

    def test_bare_note_url_fails_fast_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            resolver.youtube = SimpleNamespace(resolve=lambda _url: (_ for _ in ()).throw(RuntimeError("blocked")))
            resolver.set_xiaohongshu_browser_client(
                SimpleNamespace(request_note_detail=lambda _url: self.fail("bare URL should not wait for browser fallback"))
            )

            with self.assertRaisesRegex(RuntimeError, "xsec_token"):
                resolver.resolve("https://www.xiaohongshu.com/explore/6a75f708000000002701ede8")

    def test_cookie_config_is_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self._config(root)
            cookie = root / "xhs.txt"
            cookie.write_text("Cookie: web_session=x", encoding="utf-8")
            config.set("cookies.xiaohongshu.file", str(cookie))

            self.assertEqual(
                config.cookie_file_for_url("https://www.xiaohongshu.com/explore/a"),
                str(cookie),
            )
            self.assertNotEqual(
                config.cookie_file_for_url("https://www.youtube.com/watch?v=a"),
                str(cookie),
            )

    def test_xiaohongshu_media_urls_are_upgraded_to_https(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolver = SiteResolver(self._config(Path(temp_dir)))
            video = resolver._video_info_from_xiaohongshu_detail(
                {
                    "url": "https://www.xiaohongshu.com/explore/abc",
                    "note": {
                        "noteId": "abc",
                        "video": {"media": {"stream": {"h264": [{"masterUrl": "http://sns-video.xhscdn.com/a.mp4", "width": 720, "height": 1280}]}}},
                    },
                },
                "https://www.xiaohongshu.com/explore/abc",
            )

            resolver._normalize_xiaohongshu_media_urls(video)

            self.assertTrue(next(iter(video.qualities.values())).video_url.startswith("https://"))

    def test_startup_guide_hide_choice_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self._config(root)
            dialog = StartupCookieGuideDialog(config)
            dialog.hide_checkbox.setChecked(True)
            dialog.reject()
            dialog.deleteLater()

            reloaded = self._config(root)
            self.assertTrue(reloaded.get("ui.hide_startup_cookie_guide", False))

    def test_startup_guide_without_checkbox_does_not_hide(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self._config(root)
            dialog = StartupCookieGuideDialog(config)
            dialog.reject()
            dialog.deleteLater()

            reloaded = self._config(root)
            self.assertFalse(reloaded.get("ui.hide_startup_cookie_guide", False))

    def test_xiaohongshu_security_context_is_redacted_from_commands(self) -> None:
        command = sanitize_command([
            "yt-dlp",
            "https://www.xiaohongshu.com/explore/abc?xsec_token=secret&xsec_source=pc_feed",
            "https://sns-video-v6.xhscdn.com/stream/file.mp4?sign=secret",
        ])

        joined = " ".join(command)
        self.assertNotIn("secret", joined)
        self.assertIn("<security-context>", joined)
        self.assertIn("<media-url>", joined)


if __name__ == "__main__":
    unittest.main()
