"""字幕：全量枚举 + 落地成 mpv 能加载的文件。

两个真实数据形状（都在本机用 yt-dlp 实测过）：
- B 站：`subtitles` 里是 ai-zh / ai-en 等 6 条，**没有 url**，SRT 正文内联在
  `data` 字段；另有一条 `danmaku`(xml) 其实是弹幕。原实现只认 url，于是 6 条 AI
  字幕全被丢掉，只剩弹幕，选中后报「不支持的 XML 字幕」。
- YouTube：手动 30 条 + 自动 4842 条（机翻到各种语言），每条带可读 name。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resolver.models import SubtitleInfo
from resolver.subtitle_parser import SubtitleParser
from services import subtitle_service
from services.subtitle_service import materialize_subtitle, subtitle_cache_path


BILIBILI_SUBTITLES = {
    "danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/40376009123.xml"}],
    "ai-zh": [{"ext": "srt", "data": "1\n00:00:00,300 --> 00:00:02,480\n中文内容\n"}],
    "ai-en": [{"ext": "srt", "data": "1\n00:00:00,380 --> 00:00:01,382\nEnglish\n"}],
    "ai-ja": [{"ext": "srt", "data": "1\n00:00:00,380 --> 00:00:01,382\n日本語\n"}],
}

YOUTUBE_SUBTITLES = {
    "en": [
        {"ext": "json3", "url": "https://x/en.json3", "name": "English"},
        {"ext": "srt", "url": "https://x/en.srt", "name": "English"},
        {"ext": "vtt", "url": "https://x/en.vtt", "name": "English"},
    ],
    "zh-TW": [
        {"ext": "ttml", "url": "https://x/tw.ttml", "name": "Chinese (Taiwan)"},
        {"ext": "srt", "url": "https://x/tw.srt", "name": "Chinese (Taiwan)"},
    ],
    "ar": [{"ext": "srt", "url": "https://x/ar.srt", "name": "Arabic"}],
}


class ParserTests(unittest.TestCase):
    def test_bilibili_inline_subtitles_are_kept(self) -> None:
        parsed = SubtitleParser.parse(BILIBILI_SUBTITLES, {})

        self.assertEqual(len(parsed), 3)
        self.assertTrue(all(item.data for item in parsed.values()))
        self.assertTrue(all(not item.url for item in parsed.values()))

    def test_danmaku_is_excluded(self) -> None:
        parsed = SubtitleParser.parse(BILIBILI_SUBTITLES, {})

        self.assertNotIn("danmaku:manual", parsed)
        self.assertTrue(all("danmaku" not in key for key in parsed))

    def test_ai_tracks_are_labelled_and_sorted_chinese_first(self) -> None:
        parsed = SubtitleParser.parse(BILIBILI_SUBTITLES, {})

        first = next(iter(parsed.values()))
        self.assertEqual(first.language, "ai-zh")
        self.assertIn("AI 字幕", first.label)
        self.assertIn("中文", first.label)

    def test_preferred_extension_is_chosen(self) -> None:
        parsed = SubtitleParser.parse(YOUTUBE_SUBTITLES, {})

        # srt 排在 vtt 之前，json3/ttml 不被选中。
        self.assertEqual({item.ext for item in parsed.values()}, {"srt"})

    def test_manual_before_auto_and_chinese_before_others(self) -> None:
        parsed = SubtitleParser.parse(YOUTUBE_SUBTITLES, {"ja": [{"ext": "srt", "url": "https://x/ja.srt", "name": "Japanese"}]})

        order = [(key, item.is_auto) for key, item in parsed.items()]
        self.assertEqual(order[0][0], "zh-TW:manual")
        self.assertEqual(order[1][0], "en:manual")
        self.assertTrue(order[-1][1], "自动字幕必须排在最后")

    def test_display_name_from_ytdlp_is_used(self) -> None:
        parsed = SubtitleParser.parse(YOUTUBE_SUBTITLES, {})

        labels = [item.label for item in parsed.values()]
        self.assertIn("Chinese (Taiwan) [zh-TW] · 字幕", labels)

    def test_entries_without_content_are_dropped(self) -> None:
        parsed = SubtitleParser.parse({"en": [{"ext": "srt"}], "fr": [{"ext": "srt", "url": " "}]}, {})

        self.assertEqual(parsed, {})

    def test_unsupported_only_language_is_skipped(self) -> None:
        parsed = SubtitleParser.parse({"en": [{"ext": "json3", "url": "https://x/en.json3"}]}, {})

        self.assertEqual(parsed, {})


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(subtitle_service, "SUBTITLE_CACHE_DIR", Path(self.temp.name) / "subtitles")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_inline_data_needs_no_network(self) -> None:
        subtitle = SubtitleInfo(language="ai-zh", ext="srt", data="1\n00:00:01,000 --> 00:00:02,000\n你好\n")

        with patch.object(subtitle_service, "_download", side_effect=AssertionError("不应联网")):
            path = Path(materialize_subtitle(subtitle, "BV1"))

        self.assertTrue(path.is_file())
        self.assertIn("你好", path.read_text(encoding="utf-8"))

    def test_url_track_is_downloaded_once_then_cached(self) -> None:
        subtitle = SubtitleInfo(language="en", ext="srt", url="https://x/en.srt")
        calls: list[str] = []

        def fake_download(url, *, proxy, headers):
            calls.append(url)
            return "1\n00:00:01,000 --> 00:00:02,000\nhello\n"

        with patch.object(subtitle_service, "_download", side_effect=fake_download):
            first = materialize_subtitle(subtitle, "vid")
            second = materialize_subtitle(subtitle, "vid")

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_tracks_of_the_same_language_do_not_collide(self) -> None:
        manual = SubtitleInfo(language="en", ext="srt", url="https://x/manual.srt")
        auto = SubtitleInfo(language="en", ext="srt", url="https://x/auto.srt", is_auto=True)

        self.assertNotEqual(subtitle_cache_path("vid", manual), subtitle_cache_path("vid", auto))

    def test_unusable_track_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            materialize_subtitle(SubtitleInfo(language="en", ext="srt"), "vid")

    def test_empty_download_raises(self) -> None:
        subtitle = SubtitleInfo(language="en", ext="srt", url="https://x/en.srt")

        with patch.object(subtitle_service, "_download", return_value="   \n"):
            with self.assertRaises(RuntimeError):
                materialize_subtitle(subtitle, "vid")

    def test_cache_name_is_filesystem_safe(self) -> None:
        subtitle = SubtitleInfo(language="zh-Hans/../x", ext="s rt", url="https://x/a.srt")

        name = subtitle_cache_path("BV1?*", subtitle).name
        for char in '\\/:*?"<>|':
            self.assertNotIn(char, name)


if __name__ == "__main__":
    unittest.main()
