from __future__ import annotations

import unittest
from types import SimpleNamespace

from player.mpv_player import MpvError, MpvPlayer
from resolver.models import VideoInfo, VideoQuality
from ui.main_window import MainWindow


def _quality(label: str, url: str) -> VideoQuality:
    return VideoQuality(
        label=label,
        height=1080,
        width=1920,
        fps=30,
        vcodec="h264",
        acodec="aac",
        ext="mp4",
        format_id=label,
        video_url=url,
    )


class MpvLoadAutoplayTests(unittest.TestCase):
    def _state(self) -> SimpleNamespace:
        properties: list[tuple[str, str]] = []
        commands: list[tuple[str, ...]] = []
        state = SimpleNamespace(
            _last_eof=True,
            _last_load_request=None,
            apply_network_options=lambda _headers: None,
            set_property_string=lambda name, value: properties.append((name, value)),
            command=lambda *args: commands.append(args),
        )
        state.resume = lambda: MpvPlayer.resume(state)
        state.properties = properties
        state.commands = commands
        return state

    def test_new_media_explicitly_resumes_after_loadfile(self) -> None:
        state = self._state()

        MpvPlayer.load(state, "https://example.com/next.mp4")

        self.assertEqual(state.commands, [("loadfile", "https://example.com/next.mp4", "replace")])
        self.assertIn(("pause", "no"), state.properties)
        self.assertFalse(state._last_eof)

    def test_load_can_preserve_paused_state(self) -> None:
        state = self._state()

        MpvPlayer.load(state, "https://example.com/quality.mp4", autoplay=False)

        self.assertNotIn(("pause", "no"), state.properties)


class MpvOptionCompatibilityTests(unittest.TestCase):
    def test_missing_optional_fast_profile_does_not_abort_initialization(self) -> None:
        options: list[str] = []

        class FakeLib:
            @staticmethod
            def mpv_set_option_string(_handle, key: bytes, _value: bytes) -> int:
                name = key.decode("utf-8")
                options.append(name)
                return -1 if name == "profile" else 0

        def check(result: int, message: str) -> None:
            if result < 0:
                raise MpvError(message)

        state = SimpleNamespace(
            video_widget=SimpleNamespace(winId=lambda: 1),
            config=SimpleNamespace(
                get=lambda _key, default=None: default,
                effective_proxy=lambda: ("", ""),
            ),
            _handle=object(),
            _lib=FakeLib(),
            _check=check,
        )

        MpvPlayer._configure_before_initialize(state)

        self.assertIn("profile", options)
        self.assertIn("wid", options)


class QualitySwitchAutoplayTests(unittest.TestCase):
    def _state(self, *, paused: bool, finished: bool = False) -> tuple[SimpleNamespace, list[dict]]:
        load_calls: list[dict] = []
        quality = _quality("1080p", "https://example.com/1080.mp4")
        mpv = SimpleNamespace(
            position=lambda: 42.0,
            get_bool=lambda name: paused if name == "pause" else False,
            load=lambda video_url, audio_url, **kwargs: load_calls.append(
                {"video_url": video_url, "audio_url": audio_url, **kwargs}
            ),
        )
        state = SimpleNamespace(
            current_video=VideoInfo(
                "video",
                "Video",
                qualities={"1080p": quality},
                http_headers={"Referer": "https://example.com"},
            ),
            current_quality_label="720p",
            _playback_finished=finished,
            mpv=mpv,
            _set_playback_finished=lambda value: setattr(state, "_playback_finished", value),
        )
        return state, load_calls

    def test_quality_switch_preserves_manual_pause(self) -> None:
        state, load_calls = self._state(paused=True)

        MainWindow._change_quality(state, "1080p")

        self.assertFalse(load_calls[0]["autoplay"])
        self.assertEqual(load_calls[0]["start_position"], 42.0)

    def test_quality_switch_restarts_after_eof(self) -> None:
        state, load_calls = self._state(paused=True, finished=True)

        MainWindow._change_quality(state, "1080p")

        self.assertTrue(load_calls[0]["autoplay"])
        self.assertEqual(load_calls[0]["start_position"], 0.0)


if __name__ == "__main__":
    unittest.main()
