from __future__ import annotations

import unittest
from types import SimpleNamespace

from resolver.models import SubtitleInfo, VideoInfo
from resolver.subtitle_parser import SubtitleParser
from ui.main_window import MainWindow


class SubtitleHandlingTests(unittest.TestCase):
    def test_xml_danmaku_is_not_exposed_as_subtitle(self) -> None:
        parsed = SubtitleParser.parse(
            {"danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]},
            {},
        )
        self.assertEqual(parsed, {})

    def test_supported_subtitle_is_preferred_over_xml(self) -> None:
        parsed = SubtitleParser.parse(
            {
                "zh": [
                    {"ext": "xml", "url": "https://comment.bilibili.com/1.xml"},
                    {"ext": "vtt", "url": "https://example.com/subtitle.vtt"},
                ]
            },
            {},
        )
        subtitle = next(iter(parsed.values()))
        self.assertEqual(subtitle.ext, "vtt")

    def test_xml_subtitle_guard_does_not_call_mpv(self) -> None:
        added: list[str] = []
        messages: list[str] = []
        video = VideoInfo(
            "video",
            "Video",
            subtitles={
                "danmaku": SubtitleInfo(
                    language="danmaku",
                    ext="xml",
                    url="https://comment.bilibili.com/1.xml",
                )
            },
        )
        state = SimpleNamespace(
            current_video=video,
            mpv=SimpleNamespace(add_subtitle=added.append, clear_subtitles=lambda: None),
            toast=SimpleNamespace(show_message=messages.append),
        )

        MainWindow._change_subtitle(state, "danmaku")

        self.assertEqual(added, [])
        self.assertEqual(messages, ["Bilibili XML 弹幕不是标准字幕，已忽略"])


if __name__ == "__main__":
    unittest.main()
