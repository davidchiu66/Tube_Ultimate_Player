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

    def test_xml_subtitle_never_reaches_the_selection_path(self) -> None:
        """弹幕在解析阶段就被排除，不再需要「选中后再拦一次」的兜底。"""
        video = VideoInfo(
            "video",
            "Video",
            subtitles=SubtitleParser.parse(
                {"danmaku": [{"ext": "xml", "url": "https://comment.bilibili.com/1.xml"}]},
                {},
            ),
        )

        self.assertEqual(video.subtitles, {})

    def _state(self, video: VideoInfo):
        self.added: list[str] = []
        self.messages: list[str] = []
        self.started: list[object] = []
        self.cleared: list[bool] = []
        state = SimpleNamespace(
            current_video=video,
            _subtitle_request_id=0,
            _shutting_down=False,
            config=SimpleNamespace(effective_proxy=lambda: ("未使用代理", "")),
            thread_pool=SimpleNamespace(start=lambda worker, *_a: self.started.append(worker)),
            mpv=SimpleNamespace(
                add_subtitle=self.added.append,
                clear_subtitles=lambda: self.cleared.append(True),
            ),
            toast=SimpleNamespace(show_message=self.messages.append),
        )
        # worker 的信号要连到真实槽上，用未绑定方法补出绑定版本。
        state._subtitle_ready = lambda *args: MainWindow._subtitle_ready(state, *args)
        state._subtitle_failed = lambda *args: MainWindow._subtitle_failed(state, *args)
        return state

    def test_empty_key_clears_subtitles_without_a_worker(self) -> None:
        state = self._state(VideoInfo("video", "Video"))

        MainWindow._change_subtitle(state, "")

        self.assertEqual(self.cleared, [True])
        self.assertEqual(self.started, [])

    def test_unusable_track_is_reported_instead_of_loaded(self) -> None:
        video = VideoInfo(
            "video",
            "Video",
            subtitles={"en:manual": SubtitleInfo(language="en", ext="srt")},
        )
        state = self._state(video)

        MainWindow._change_subtitle(state, "en:manual")

        self.assertEqual(self.started, [])
        self.assertEqual(self.added, [])
        self.assertEqual(len(self.messages), 1)
        self.assertIn("没有可用内容", self.messages[0])

    def test_usable_track_is_handed_to_a_worker(self) -> None:
        subtitle = SubtitleInfo(language="ai-zh", ext="srt", data="1\n00:00:01,000 --> 00:00:02,000\n你好\n")
        video = VideoInfo("BV1", "Video", subtitles={"ai-zh:manual": subtitle})
        state = self._state(video)

        MainWindow._change_subtitle(state, "ai-zh:manual")

        self.assertEqual(len(self.started), 1)
        worker = self.started[0]
        self.assertEqual(worker.key, "ai-zh:manual")
        self.assertEqual(worker.video_id, "BV1")
        self.assertIs(worker.subtitle, subtitle)
        # 字幕要等 worker 落盘后才交给 mpv，绝不能在 UI 线程里直接加载。
        self.assertEqual(self.added, [])

    def test_stale_worker_result_is_ignored(self) -> None:
        state = self._state(VideoInfo("video", "Video"))
        state._subtitle_request_id = 7

        MainWindow._subtitle_ready(state, 3, "en:manual", "C:/tmp/en.srt")
        self.assertEqual(self.added, [])

        MainWindow._subtitle_ready(state, 7, "en:manual", "C:/tmp/en.srt")
        self.assertEqual(self.added, ["C:/tmp/en.srt"])


if __name__ == "__main__":
    unittest.main()
