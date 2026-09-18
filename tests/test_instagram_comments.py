from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from resolver.models import CommentInfo, VideoInfo
from resolver.site_resolver import SiteResolver
from resolver.youtube_resolver import YoutubeResolver, _current_video_id_for_site, _detect_source_site
from services.config_service import ConfigService
from services.site_registry import SITE_KEYS, site_for_url
from services import instagram_browser_service
from ui.playlist_overlay import CommentItemWidget, PlaylistOverlay


class InstagramSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_instagram_is_between_tiktok_and_xiaohongshu(self) -> None:
        self.assertLess(SITE_KEYS.index("tiktok"), SITE_KEYS.index("instagram"))
        self.assertLess(SITE_KEYS.index("instagram"), SITE_KEYS.index("xiaohongshu"))
        self.assertEqual(site_for_url("https://www.instagram.com/reel/ABC_123/"), "instagram")
        url = "https://www.instagram.com/reel/ABC_123/"
        self.assertEqual(_detect_source_site(url), "instagram")
        self.assertEqual(_current_video_id_for_site(url, "instagram"), "instagram:ABC_123")

    def test_instagram_home_uses_authenticated_main_feed(self) -> None:
        self.assertEqual(instagram_browser_service._HOME_URL, "https://www.instagram.com/")

    def test_instagram_html_parser_keeps_unique_reel_post_links(self) -> None:
        html = r'''<a href="/reel/AAA/">one</a>
        <a href="https:\/\/www.instagram.com\/p\/BBB/">two</a>
        <a href="/reel/AAA/">duplicate</a>'''

        videos = SiteResolver._instagram_videos_from_html(html)

        self.assertEqual([item.video_id for item in videos], ["instagram:AAA", "instagram:BBB"])
        self.assertEqual(videos[0].webpage_url, "https://www.instagram.com/reel/AAA/")

    def test_instagram_home_and_search_use_browser_client_when_available(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.config = object()
        resolver._instagram_browser_client = type(
            "Client",
            (),
            {
                "request_home": lambda self, page, size: {
                    "items": [{"id": "AAA", "title": "首页", "url": "https://www.instagram.com/reel/AAA/"}],
                    "has_more": False,
                },
                "request_search": lambda self, keyword, page, size: {
                    "items": [{"id": "BBB", "title": keyword, "url": "https://www.instagram.com/reel/BBB/"}],
                    "has_more": False,
                },
            },
        )()

        home, home_more = resolver._fetch_instagram_home(1, 20)
        search, search_more = resolver._search_instagram("净水器", 1, 20)

        self.assertEqual(home[0].video_id, "instagram:AAA")
        self.assertFalse(home_more)
        self.assertEqual(search[0].title, "净水器")
        self.assertFalse(search_more)

    def test_ytdlp_comment_payload_is_mapped_to_comment_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "default.json"
            default_path.write_text("{}", encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=root / "user.json")
            config.set("youtube.cookie_browser", "")
            resolver = YoutubeResolver.__new__(YoutubeResolver)
            resolver.config = config
            resolver.ytdlp_path = Path("yt-dlp")
            payload = {
                "comments": [
                    {
                        "id": "c1",
                        "author": "作者",
                        "text": "很棒",
                        "like_count": 3,
                        "timestamp": 1700000000,
                    }
                ]
            }
            result = subprocess.CompletedProcess(
                ["yt-dlp"], 0, stdout=json.dumps(payload), stderr=""
            )
            video = VideoInfo(
                "video",
                "视频",
                source_site="youtube",
                webpage_url="https://www.youtube.com/watch?v=abcdefghijk",
            )
            with patch.object(resolver, "_run_ytdlp", return_value=result) as run:
                comments, cursor, has_more = resolver.fetch_comments(video)

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].comment_id, "c1")
        self.assertEqual(comments[0].author_name, "作者")
        self.assertEqual(comments[0].like_count, 3)
        self.assertEqual(cursor, "")
        self.assertFalse(has_more)
        self.assertIn("--write-comments", run.call_args.args[0])

    def test_youtube_comments_expose_offset_cursor_for_more_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "default.json"
            default_path.write_text("{}", encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=root / "user.json")
            config.set("youtube.cookie_browser", "")
            resolver = YoutubeResolver.__new__(YoutubeResolver)
            resolver.config = config
            resolver.ytdlp_path = Path("yt-dlp")
            payload = {
                "comments": [
                    {"id": f"c{index}", "author": f"作者{index}", "text": f"评论{index}"}
                    for index in range(41)
                ]
            }
            result = subprocess.CompletedProcess(["yt-dlp"], 0, stdout=json.dumps(payload), stderr="")
            video = VideoInfo(
                "video",
                "视频",
                source_site="youtube",
                webpage_url="https://www.youtube.com/watch?v=abcdefghijk",
            )
            with patch.object(resolver, "_run_ytdlp", return_value=result) as run:
                comments, cursor, has_more = resolver.fetch_comments(video, cursor="20", limit=20)

        self.assertEqual([item.comment_id for item in comments], [f"c{index}" for index in range(20, 40)])
        self.assertEqual(cursor, "40")
        self.assertTrue(has_more)
        command = run.call_args.args[0]
        self.assertIn("youtube:max_comments=41,all,all,all,all;comment_sort=top", command)

    def test_bilibili_comments_use_fast_paged_api(self) -> None:
        resolver = SiteResolver.__new__(SiteResolver)
        resolver.bilibili = type(
            "Bilibili",
            (),
            {"fetch_comments": lambda self, video, cursor="", limit=20: ([], "2", True)},
        )()
        resolver.youtube = object()
        video = VideoInfo(
            "bilibili:BV1siYL6zEzX",
            "视频",
            source_site="bilibili",
            webpage_url="https://www.bilibili.com/video/BV1siYL6zEzX",
        )

        comments, cursor, has_more = resolver.fetch_comments(video)

        self.assertEqual(comments, [])
        self.assertEqual(cursor, "2")
        self.assertTrue(has_more)


class PlaylistCommentsTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_comments_are_loaded_lazily_on_tab_selection(self) -> None:
        overlay = PlaylistOverlay()
        requests: list[bool] = []
        overlay.comments_requested.connect(lambda: requests.append(True))

        self.assertEqual(overlay.tabs.currentIndex(), 0)
        overlay.tabs.setCurrentIndex(1)
        overlay.tabs.setCurrentIndex(1)

        self.assertEqual(requests, [True])
        overlay.close()

    def test_comment_rows_use_structured_widget_and_grow_for_long_text(self) -> None:
        overlay = PlaylistOverlay()
        overlay.resize(430, 600)
        overlay.show()
        overlay.set_comments(
            [
                CommentInfo(
                    "c1",
                    "v1",
                    "bilibili",
                    "作者",
                    "第一行\n第二行\n第三行",
                    like_count=2,
                    reply_count=1,
                    created_at="刚刚",
                )
            ],
            video_id="v1",
        )

        item = overlay.comments_list_widget.item(0)
        widget = overlay.comments_list_widget.itemWidget(item)
        self.assertIsInstance(widget, CommentItemWidget)
        self.assertEqual(widget.author_label.text(), "作者")
        self.assertIn("赞 2", widget.meta_label.text())
        self.assertGreaterEqual(item.sizeHint().height(), 88)
        overlay.close()

    def test_appending_comments_keeps_previous_page_last_comment_anchored(self) -> None:
        overlay = PlaylistOverlay()
        overlay.resize(430, 600)
        overlay.show()
        overlay.tabs.setCurrentIndex(1)
        first_page = [
            CommentInfo(f"c{index}", "v1", "bilibili", "作者", f"评论 {index}")
            for index in range(20)
        ]
        overlay.set_comments(first_page, video_id="v1")
        self.app.processEvents()
        overlay.comments_list_widget.verticalScrollBar().setValue(0)
        overlay.comments_list_widget.verticalScrollBar().setValue(
            overlay.comments_list_widget.verticalScrollBar().maximum()
        )
        old_maximum = overlay.comments_list_widget.verticalScrollBar().maximum()
        overlay.set_comments(
            first_page + [
                CommentInfo(f"c{index}", "v1", "bilibili", "作者", f"评论 {index}")
                for index in range(20, 40)
            ],
            cursor="40",
            has_more=True,
            video_id="v1",
            anchor_to_previous_page_end=True,
        )
        self.app.processEvents()
        scrollbar = overlay.comments_list_widget.verticalScrollBar()
        self.assertLess(old_maximum, scrollbar.maximum())
        self.assertEqual(scrollbar.value(), old_maximum)
        overlay.close()


if __name__ == "__main__":
    unittest.main()
