"""字幕：全量枚举 + 落地成 mpv 能加载的文件。

两个真实数据形状（都在本机用 yt-dlp 实测过）：
- B 站：`subtitles` 里是 ai-zh / ai-en 等 6 条，**没有 url**，SRT 正文内联在
  `data` 字段；另有一条 `danmaku`(xml) 其实是弹幕。原实现只认 url，于是 6 条 AI
  字幕全被丢掉，只剩弹幕，选中后报「不支持的 XML 字幕」。
- YouTube：手动 30 条 + 自动 4842 条（机翻到各种语言），每条带可读 name。
"""

from __future__ import annotations

import logging
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from resolver.models import SubtitleInfo
from resolver.subtitle_parser import SubtitleParser
from resolver.youtube_resolver import YoutubeResolver
from services import subtitle_service
from services.config_service import ConfigService
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

    def test_unusual_format_is_kept_instead_of_silently_dropped(self) -> None:
        # P3：只有 json3/srv3 这类私有格式时也要交出轨道，宁可 mpv 报错也不静默消失。
        parsed = SubtitleParser.parse({"en": [{"ext": "json3", "url": "https://x/en.json3"}]}, {})

        self.assertEqual(list(parsed), ["en:manual"])
        self.assertEqual(parsed["en:manual"].ext, "json3")

    def test_srv3_is_preferred_over_json3(self) -> None:
        parsed = SubtitleParser.parse(
            {
                "en": [
                    {"ext": "json3", "url": "https://x/en.json3"},
                    {"ext": "srv3", "url": "https://x/en.srv3"},
                ]
            },
            {},
        )

        self.assertEqual(parsed["en:manual"].ext, "srv3")

    def test_danmaku_xml_is_still_excluded(self) -> None:
        parsed = SubtitleParser.parse(
            {
                "danmaku": [{"ext": "xml", "url": "https://x/danmaku.xml"}],
                "en": [{"ext": "xml", "url": "https://x/en.xml"}],
            },
            {},
        )

        self.assertEqual(parsed, {})

    def test_configured_languages_drive_the_order(self) -> None:
        tracks = {
            "en": [{"ext": "srt", "url": "https://x/en.srt"}],
            "ja": [{"ext": "srt", "url": "https://x/ja.srt"}],
            "zh-Hans": [{"ext": "srt", "url": "https://x/zh.srt"}],
        }

        default_order = list(SubtitleParser.parse(tracks, {}))
        japanese_first = list(SubtitleParser.parse(tracks, {}, preferred_languages=["ja", "en"]))

        self.assertEqual(default_order[0], "zh-Hans:manual")
        self.assertEqual(japanese_first[0], "ja:manual")
        self.assertEqual(japanese_first[1], "en:manual")

    def test_empty_language_config_falls_back_to_builtin_order(self) -> None:
        tracks = {
            "en": [{"ext": "srt", "url": "https://x/en.srt"}],
            "zh-Hans": [{"ext": "srt", "url": "https://x/zh.srt"}],
        }

        self.assertEqual(
            list(SubtitleParser.parse(tracks, {}, preferred_languages=[])),
            list(SubtitleParser.parse(tracks, {})),
        )


class ParseObservabilityTests(unittest.TestCase):
    """P4：靠 raw_manual / raw_auto / parsed 区分"站点没给"与"解析器丢了"。"""

    def _debug_lines(self, subtitles: dict, automatic_captions: dict) -> list[str]:
        with self.assertLogs("tube_player.resolver", level=logging.DEBUG) as captured:
            SubtitleParser.parse(subtitles, automatic_captions)
        return [line for line in captured.output if "subtitle parse" in line]

    def test_site_returned_nothing(self) -> None:
        lines = self._debug_lines({}, {})

        self.assertIn("raw_manual=0 raw_auto=0 parsed=0", lines[0])

    def test_counts_cover_both_dictionaries(self) -> None:
        lines = self._debug_lines(YOUTUBE_SUBTITLES, {"ja": [{"ext": "srt", "url": "https://x/ja.srt"}]})

        self.assertIn("raw_manual=3 raw_auto=1 parsed=4", lines[0])

    def test_parser_dropping_everything_is_visible(self) -> None:
        lines = self._debug_lines({"danmaku": [{"ext": "xml", "url": "https://x/d.xml"}]}, {})

        self.assertIn("raw_manual=1 raw_auto=0 parsed=0", lines[0])


class ResolveCommandTests(unittest.TestCase):
    """P2：`--dump-single-json` 下四个字幕参数是空转的，不该再传。"""

    @staticmethod
    def _command() -> list[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=Path(temp_dir) / "user.json",
            )
            resolver = YoutubeResolver.__new__(YoutubeResolver)
            resolver.config = config
            resolver.ytdlp_path = Path("yt-dlp")
            return resolver._build_command("https://www.youtube.com/watch?v=1")

    def test_subtitle_flags_are_gone(self) -> None:
        command = self._command()

        for flag in ("--write-subs", "--write-auto-subs", "--sub-langs", "--sub-format"):
            self.assertNotIn(flag, command)

    def test_the_rest_of_the_command_is_untouched(self) -> None:
        command = self._command()

        self.assertIn("--dump-single-json", command)
        self.assertIn("--skip-download", command)
        self.assertIn("--no-playlist", command)
        self.assertIn("--geo-bypass", command)
        self.assertEqual(command[-1], "https://www.youtube.com/watch?v=1")


class DownloadRetryTests(unittest.TestCase):
    """429：YouTube 机翻字幕接口按 IP 限流，重试几次能救回来，救不回来要说清原因。"""

    TRANSLATED_URL = "https://www.youtube.com/api/timedtext?v=abc&kind=asr&lang=en&tlang=zh-Hans"
    PLAIN_URL = "https://www.youtube.com/api/timedtext?v=abc&lang=en"

    def setUp(self) -> None:
        sleeper = patch.object(subtitle_service.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    @staticmethod
    def _http_error(status: int, headers: dict | None = None) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("https://x", status, "boom", headers or {}, None)

    def _download(self, url: str, side_effect) -> str:
        with patch.object(subtitle_service, "_download_once", side_effect=side_effect) as once:
            self.attempts = once
            return subtitle_service._download(url, proxy="", headers=None)

    def test_transient_429_is_retried_and_succeeds(self) -> None:
        payload = self._download(
            self.TRANSLATED_URL,
            [self._http_error(429), "1\n00:00:01,000 --> 00:00:02,000\n你好\n"],
        )

        self.assertIn("你好", payload)
        self.assertEqual(self.attempts.call_count, 2)

    def test_attempts_are_capped(self) -> None:
        with self.assertRaises(RuntimeError):
            self._download(self.TRANSLATED_URL, self._http_error(429))

        self.assertEqual(self.attempts.call_count, subtitle_service.DOWNLOAD_ATTEMPTS)

    def test_translated_track_gets_an_actionable_message(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self._download(self.TRANSLATED_URL, self._http_error(429))

        message = str(raised.exception)
        self.assertIn("429", message)
        self.assertIn("机器翻译", message)
        self.assertIn("原文字幕", message)

    def test_plain_track_message_does_not_blame_translation(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self._download(self.PLAIN_URL, self._http_error(429))

        message = str(raised.exception)
        self.assertIn("429", message)
        self.assertNotIn("机器翻译", message)

    def test_expired_signature_is_reported_without_retrying(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            self._download(self.PLAIN_URL, self._http_error(403))

        self.assertEqual(self.attempts.call_count, 1)
        self.assertIn("重新解析", str(raised.exception))

    def test_server_errors_are_retried(self) -> None:
        payload = self._download(self.PLAIN_URL, [self._http_error(503), "ok"])

        self.assertEqual(payload, "ok")
        self.assertEqual(self.attempts.call_count, 2)

    def test_retry_after_header_is_honoured_within_the_cap(self) -> None:
        self._download(self.PLAIN_URL, [self._http_error(429, {"Retry-After": "4"}), "ok"])

        delay = self.sleep.call_args[0][0]
        self.assertGreaterEqual(delay, 4.0)
        self.assertLessEqual(delay, 4.0 + 0.4)

    def test_absurd_retry_after_is_clamped(self) -> None:
        self._download(self.PLAIN_URL, [self._http_error(429, {"Retry-After": "600"}), "ok"])

        delay = self.sleep.call_args[0][0]
        self.assertLessEqual(delay, subtitle_service.MAX_RETRY_WAIT_SECONDS + 0.4)

    def test_translated_tracks_are_labelled(self) -> None:
        translated = SubtitleInfo(language="zh-Hans", ext="srt", url=self.TRANSLATED_URL, is_auto=True)
        original = SubtitleInfo(language="en", ext="srt", url=self.PLAIN_URL, is_auto=True)

        self.assertTrue(translated.is_translated)
        self.assertIn("机翻", translated.label)
        self.assertFalse(original.is_translated)
        self.assertIn("自动", original.label)


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
