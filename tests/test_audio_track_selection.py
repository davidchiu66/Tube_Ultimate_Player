"""R9 音轨选择的回归测试（验收标准 1-21）。

夹具全部离线合成，形状照抄真实 yt-dlp 输出：多语言视频给"纯视频 DASH + 每语言一条
音频 + 已混音 HLS"，单语言视频给"progressive 18 + 已混音 96 + 高档纯视频"。
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMessageBox

from download import command_builder
from download.command_builder import build_download_task
from resolver.models import MUXED_AUDIO_TRACK_ID, AudioTrack, VideoInfo
from resolver.quality_selector import QualitySelector
from resolver.youtube_resolver import YoutubeResolver
from services.locale_service import (
    match_audio_language,
    parse_language_tag,
    system_language_tag,
)
from ui.main_window import MainWindow
from ui.player_page import PlayerPage
from ui.widgets import NoScrollComboBox

# (语言码, language_preference, format_note)。en 是原声，ru 是站点默认——真实缺陷
# 里播出来的就是俄语；id 的码率最高，用来钉住"不再按 score_audio 裸选"。
MULTI_LANGUAGES = [
    ("en", 10, "English (United States) original, medium"),
    ("ru", 5, "Russian, medium"),
    ("id", -1, "Indonesian, medium"),
    ("zh-Hans", -1, "Chinese (Simplified), medium"),
    ("zh-Hant", -1, "Chinese (Traditional), medium"),
    ("ja", -1, "Japanese, medium"),
    ("ko", -1, "Korean, medium"),
    ("es-US", -1, "Spanish (United States), medium"),
    ("fr", -1, "French, medium"),
    ("de", -1, "German, medium"),
    ("pt-BR", -1, "Portuguese (Brazil), medium"),
    ("ar", -1, "Arabic, medium"),
    ("it", -1, "Italian, medium"),
    ("th", -1, "Thai, medium"),
    ("vi", -1, "Vietnamese, medium"),
    ("hi", -1, "Hindi, medium"),
    ("ms", -1, "Malay, medium"),
    ("tr", -1, "Turkish, medium"),
    ("pl", -1, "Polish, medium"),
    ("nl", -1, "Dutch, medium"),
    ("uk", -1, "Ukrainian, medium"),
    ("he", -1, "Hebrew, medium"),
    ("sv", -1, "Swedish, medium"),
    ("bn", -1, "Bengali, medium"),
]
# DRC 变体的码率略高于原轨，不剔掉就会盖过同语言的原轨。
DRC_LANGUAGES = ["en", "ru", "zh-Hans", "ja"]


def audio_fmt(format_id: str, language: str, preference: int, note: str, abr: float) -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.test/audio/{format_id}",
        "acodec": "opus",
        "vcodec": "none",
        "language": language,
        "language_preference": preference,
        "format_note": note,
        "abr": abr,
        "tbr": abr,
        "filesize": int(abr * 1000),
        "ext": "webm",
    }


def video_only_fmt(format_id: str, height: int, tbr: float, vcodec: str = "avc1.640028") -> dict:
    return {
        "format_id": format_id,
        "url": f"https://example.test/video/{format_id}",
        "acodec": "none",
        "vcodec": vcodec,
        "height": height,
        "width": height * 16 // 9,
        "fps": 30,
        "tbr": tbr,
        "filesize": int(tbr * 1000),
        "ext": "mp4",
    }


def muxed_fmt(format_id: str, height: int, tbr: float, language: str = "ru") -> dict:
    """已混音 HLS：音频语言焊死在流里，这是"播出来是俄语"的来源。"""
    return {
        "format_id": format_id,
        "url": f"https://example.test/muxed/{format_id}.m3u8",
        "protocol": "m3u8_native",
        "acodec": "mp4a.40.2",
        "vcodec": "avc1.4d401f",
        "height": height,
        "width": height * 16 // 9,
        "fps": 30,
        "tbr": tbr,
        "language": language,
        "ext": "mp4",
    }


def multi_language_formats() -> list[dict]:
    formats = [
        video_only_fmt("137", 1080, 3000),
        video_only_fmt("248", 1080, 2500, "vp09.00.40.08"),
        video_only_fmt("271", 1440, 6000, "vp09.00.50.08"),
        video_only_fmt("313", 2160, 12000, "vp09.00.50.08"),
        muxed_fmt("96", 1080, 4000),
        muxed_fmt("95", 720, 2000),
    ]
    for index, (language, preference, note) in enumerate(MULTI_LANGUAGES):
        abr = 160.0 if language == "id" else 129.0
        formats.append(audio_fmt(f"251-{index}", language, preference, note, abr))
    for language in DRC_LANGUAGES:
        index = [lang for lang, _, _ in MULTI_LANGUAGES].index(language)
        note = MULTI_LANGUAGES[index][2]
        formats.append(
            audio_fmt(f"251-{index}-drc", language, MULTI_LANGUAGES[index][1], note, 132.0)
        )
    return formats


def single_language_formats() -> list[dict]:
    """1080p 由已混音的 96 胜出（tbr 更高），1440p/2160p 只有纯视频。"""
    progressive = muxed_fmt("18", 360, 600, language="")
    progressive["protocol"] = "https"
    return [
        progressive,
        muxed_fmt("96", 1080, 4000, language=""),
        video_only_fmt("137", 1080, 3000),
        video_only_fmt("271", 1440, 6000, "vp09.00.50.08"),
        video_only_fmt("313", 2160, 12000, "vp09.00.50.08"),
        audio_fmt("251", "", -1, "medium", 129.0),
    ]


def bilibili_formats() -> list[dict]:
    """B 站给三档码率、无语言码，应折叠成一条（取最高档 30280）。"""
    return [
        video_only_fmt("30112", 1080, 3000),
        audio_fmt("30216", "", -1, "", 64.0),
        audio_fmt("30232", "", -1, "", 132.0),
        audio_fmt("30280", "", -1, "", 192.0),
    ]


def make_video(formats: list[dict], preferred: str = "zh-Hans-CN") -> VideoInfo:
    tracks = QualitySelector.select_audio_tracks(formats, preferred)
    return VideoInfo(
        video_id="vid",
        title="示例视频",
        webpage_url="https://example.test/watch?v=vid",
        duration=600,
        qualities=QualitySelector.select_all(formats, audio_tracks=tracks),
        audio_tracks=dict(tracks),
    )


def _control_positions(page: PlayerPage, combos: tuple) -> dict:
    """返回每个下拉在控制行里的次序号。

    三个下拉各自被 `_control_group()` 包进一条子布局再加进控制行，所以取的是
    子布局在控制行里的下标——控件在页面未显示时没有几何信息，只能顺着布局走。
    """
    targets = set(combos)
    for row in page.findChildren(QHBoxLayout):
        positions: dict = {}
        for index in range(row.count()):
            group = row.itemAt(index).layout()
            if group is None:
                continue
            for slot in range(group.count()):
                widget = group.itemAt(slot).widget()
                if widget in targets:
                    positions[widget] = index
        if len(positions) == len(targets):
            return positions
    return {}


class SelectAudioTracksTests(unittest.TestCase):
    def test_multi_language_yields_one_track_per_language(self) -> None:
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), "zh-Hans-CN")

        # 24 种语言 → 24 条，4 条 DRC 全部被折叠掉。
        self.assertEqual(len(tracks), len(MULTI_LANGUAGES))
        self.assertFalse([key for key in tracks if key.endswith("-drc")])
        self.assertEqual(len({track.language for track in tracks.values()}), len(MULTI_LANGUAGES))

    def test_drc_variant_never_wins_its_language(self) -> None:
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), "en-US")
        english = [t for t in tracks.values() if t.language == "en"]

        self.assertEqual(len(english), 1)
        self.assertEqual(english[0].track_id, "251-0")

    def test_all_drc_group_keeps_the_drc_track(self) -> None:
        formats = [
            audio_fmt("251-9-drc", "de", -1, "German, medium", 132.0),
            audio_fmt("251-0", "en", 10, "English original, medium", 129.0),
        ]
        tracks = QualitySelector.select_audio_tracks(formats, "de-DE")

        # 整组都是 DRC 时不能把这门语言整个丢掉。
        self.assertIn("251-9-drc", tracks)

    def test_single_language_returns_that_one_track(self) -> None:
        tracks = QualitySelector.select_audio_tracks(single_language_formats(), "zh-Hans-CN")

        self.assertEqual(list(tracks), ["251"])

    def test_bilibili_bitrates_collapse_to_the_best(self) -> None:
        tracks = QualitySelector.select_audio_tracks(bilibili_formats(), "zh-Hans-CN")

        self.assertEqual(list(tracks), ["30280"])

    def test_formats_without_audio_return_empty(self) -> None:
        formats = [video_only_fmt("137", 1080, 3000), video_only_fmt("271", 1440, 6000)]

        self.assertEqual(QualitySelector.select_audio_tracks(formats, "zh-Hans-CN"), {})

    def test_track_label_drops_bitrate_tier_and_original_marker(self) -> None:
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), "zh-Hans-CN")
        by_language = {track.language: track for track in tracks.values()}

        self.assertEqual(by_language["en"].label, "英语（原声）")
        self.assertEqual(by_language["ru"].label, "俄语")
        self.assertEqual(by_language["zh-Hans"].label, "简体中文")
        # 表内没有的语言回退 yt-dlp 的可读名，且不带码率档位。
        self.assertEqual(by_language["pl"].label, "Polish")
        self.assertEqual(by_language["es-US"].label, "Spanish (United States)")

    def test_bitrate_only_note_is_not_used_as_a_name(self) -> None:
        tracks = QualitySelector.select_audio_tracks(bilibili_formats(), "zh-Hans-CN")

        self.assertEqual(next(iter(tracks.values())).name, "")


class DefaultTrackOrderTests(unittest.TestCase):
    def _first_language(self, preferred: str) -> str:
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), preferred)
        return next(iter(tracks.values())).language

    def test_system_language_wins(self) -> None:
        self.assertEqual(self._first_language("zh-Hans-CN"), "zh-Hans")
        self.assertEqual(self._first_language("ja-JP"), "ja")
        self.assertEqual(self._first_language("en-US"), "en")

    def test_traditional_chinese_is_not_folded_into_simplified(self) -> None:
        self.assertEqual(self._first_language("zh-Hant-TW"), "zh-Hant")

    def test_territory_variant_matches_its_language(self) -> None:
        # 夹具里只有 es-US / pt-BR，系统是 es-ES / pt-PT 时按同语言回退。
        self.assertEqual(self._first_language("es-ES"), "es-US")
        self.assertEqual(self._first_language("pt-PT"), "pt-BR")

    def test_unmatched_system_falls_back_to_site_default(self) -> None:
        # 夹具没有斯瓦希里语 → 退到站点默认轨（language_preference == 5）。
        first = self._first_language("sw-KE")
        self.assertEqual(first, "ru")

    def test_site_default_outranks_original(self) -> None:
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), "sw-KE")
        languages = [track.language for track in tracks.values()]

        self.assertEqual(languages[:2], ["ru", "en"])

    def test_original_wins_when_no_site_default_exists(self) -> None:
        formats = [
            audio_fmt("251-3", "zh-Hans", -1, "Chinese (Simplified), medium", 129.0),
            audio_fmt("251-0", "en", 10, "English original, medium", 129.0),
        ]
        tracks = QualitySelector.select_audio_tracks(formats, "sw-KE")

        self.assertEqual(next(iter(tracks.values())).language, "en")

    def test_indonesian_bitrate_no_longer_decides(self) -> None:
        # 缺陷复现点：id 的 abr 最高，裸 score_audio 会选它。
        for preferred in ("zh-Hans-CN", "en-US", "sw-KE"):
            with self.subTest(preferred=preferred):
                self.assertNotEqual(self._first_language(preferred), "id")

class LocaleServiceTests(unittest.TestCase):
    def test_system_language_tag_keeps_script(self) -> None:
        # QLocale.name() 会丢掉 script（zh_CN），所以这里必须自己拼。
        self.assertEqual(system_language_tag(QLocale("zh_Hans_CN")), "zh-Hans-CN")
        self.assertEqual(system_language_tag(QLocale("zh_Hant_TW")), "zh-Hant-TW")

    def test_system_language_tag_strips_implicit_script(self) -> None:
        # en/ja/ko 只有一种书写系统，带上 Latn/Jpan 只会让匹配变窄。
        for tag, expected in (("en_US", "en-US"), ("ja_JP", "ja-JP"), ("ko_KR", "ko-KR")):
            with self.subTest(tag=tag):
                self.assertEqual(system_language_tag(QLocale(tag)), expected)

    def test_parse_language_tag_only_treats_four_letters_as_script(self) -> None:
        self.assertEqual(parse_language_tag("en-US"), ("en", ""))
        self.assertEqual(parse_language_tag("zh-Hans-CN"), ("zh", "hans"))
        self.assertEqual(parse_language_tag("zh"), ("zh", ""))
        self.assertEqual(parse_language_tag(""), ("", ""))

    def test_match_prefers_exact_tag(self) -> None:
        languages = ["en", "en-US", "zh-Hans"]

        self.assertEqual(match_audio_language(languages, "en-US"), "en-US")

    def test_match_ignores_territory(self) -> None:
        self.assertEqual(match_audio_language(["zh-Hans", "zh-Hant"], "zh-Hans-CN"), "zh-Hans")

    def test_match_accepts_neutral_code_when_script_agrees(self) -> None:
        self.assertEqual(match_audio_language(["zh", "ja"], "zh-Hans-CN"), "zh")

    def test_match_prefers_neutral_over_conflicting_script(self) -> None:
        # 简体系统在"繁体"和"中性中文"之间要挑中性的那条（第 3 级）。
        self.assertEqual(match_audio_language(["zh-Hant", "zh"], "zh-Hans-CN"), "zh")

    def test_match_takes_other_script_over_other_language(self) -> None:
        # 只有繁体可选时仍然取繁体（第 4 级）：同语系远好过整个换一种语言。
        self.assertEqual(match_audio_language(["ru", "zh-Hant"], "zh-Hans-CN"), "zh-Hant")

    def test_match_never_crosses_language(self) -> None:
        self.assertEqual(match_audio_language(["ru", "tr", "id"], "zh-Hans-CN"), "")

    def test_match_falls_back_to_same_language(self) -> None:
        self.assertEqual(match_audio_language(["pt-BR"], "pt-PT"), "pt-BR")

    def test_match_returns_empty_when_language_absent(self) -> None:
        self.assertEqual(match_audio_language(["en", "ru"], "sw-KE"), "")

    def test_match_tolerates_empty_inputs(self) -> None:
        self.assertEqual(match_audio_language([], "zh-Hans-CN"), "")
        self.assertEqual(match_audio_language(["en"], ""), "")


class SelectAllTests(unittest.TestCase):
    def test_multi_language_switches_to_video_only(self) -> None:
        formats = multi_language_formats()
        tracks = QualitySelector.select_audio_tracks(formats, "zh-Hans-CN")
        qualities = QualitySelector.select_all(formats, audio_tracks=tracks)

        # A1：≥2 种语言 → 1080p 改用纯视频 137，不再让已混音的 96 焊死语言。
        self.assertEqual(qualities["1080p"].format_id, "137")
        self.assertEqual(qualities["1080p"].acodec, "opus")

    def test_multi_language_attaches_the_default_track(self) -> None:
        formats = multi_language_formats()
        tracks = QualitySelector.select_audio_tracks(formats, "zh-Hans-CN")
        qualities = QualitySelector.select_all(formats, audio_tracks=tracks)
        default_track = next(iter(tracks.values()))

        for label in ("1080p", "1440p", "2160p"):
            with self.subTest(label=label):
                self.assertEqual(qualities[label].audio_format_id, default_track.track_id)
                self.assertEqual(qualities[label].audio_url, default_track.url)

    def test_muxed_address_is_kept_for_the_no_transcode_option(self) -> None:
        formats = multi_language_formats()
        tracks = QualitySelector.select_audio_tracks(formats, "zh-Hans-CN")
        qualities = QualitySelector.select_all(formats, audio_tracks=tracks)

        # C1：本档位有已混音变体才给地址；1440p 没有就留空。
        self.assertEqual(qualities["1080p"].muxed_video_url, "https://example.test/muxed/96.m3u8")
        self.assertIsNone(qualities["1440p"].muxed_video_url)

    def test_single_language_selection_is_unchanged(self) -> None:
        formats = single_language_formats()
        tracks = QualitySelector.select_audio_tracks(formats, "zh-Hans-CN")
        qualities = QualitySelector.select_all(formats, audio_tracks=tracks)

        # 单语言不进 A1：1080p 仍是已混音的 96，高档仍挂 251。
        self.assertEqual(qualities["1080p"].format_id, "96")
        self.assertIsNone(qualities["1080p"].audio_url)
        self.assertIsNone(qualities["1080p"].audio_format_id)
        self.assertIsNone(qualities["1080p"].muxed_video_url)
        for label in ("1440p", "2160p"):
            with self.subTest(label=label):
                self.assertEqual(qualities[label].audio_format_id, "251")

    def test_single_language_matches_a_call_without_audio_tracks(self) -> None:
        formats = single_language_formats()
        tracks = QualitySelector.select_audio_tracks(formats, "zh-Hans-CN")
        with_tracks = QualitySelector.select_all(formats, audio_tracks=tracks)
        without_tracks = QualitySelector.select_all(formats, audio_tracks={})

        self.assertEqual(with_tracks, without_tracks)

class PlayerPageAudioComboTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.addCleanup(self.page.deleteLater)
        self.page.set_playback_available(True)

    def test_audio_combo_sits_between_quality_and_subtitle(self) -> None:
        positions = _control_positions(
            self.page,
            (self.page.quality_combo, self.page.audio_combo, self.page.subtitle_combo),
        )

        self.assertEqual(len(positions), 3, "三个下拉应在同一行控件里")
        self.assertLess(positions[self.page.quality_combo], positions[self.page.audio_combo])
        self.assertLess(positions[self.page.audio_combo], positions[self.page.subtitle_combo])

        # 三个下拉都必须是 NoScrollComboBox：控制面板会自动隐藏，滚轮误触的代价一样。
        for combo in (self.page.quality_combo, self.page.audio_combo, self.page.subtitle_combo):
            with self.subTest(combo=combo.objectName()):
                self.assertIsInstance(combo, NoScrollComboBox)

    def test_multi_language_video_fills_the_combo(self) -> None:
        video = make_video(multi_language_formats())

        self.page.update_video_info(video, "1080p")

        # 24 条音轨 + 「随画面（免转码）」
        self.assertEqual(self.page.audio_combo.count(), len(MULTI_LANGUAGES) + 1)
        self.assertEqual(self.page.audio_combo.currentIndex(), 0)
        self.assertEqual(self.page.audio_combo.currentData(), next(iter(video.audio_tracks)))
        self.assertTrue(self.page.audio_combo.isEnabled())

    def test_no_transcode_option_is_last_and_uses_the_sentinel(self) -> None:
        self.page.update_video_info(make_video(multi_language_formats()), "1080p")
        last = self.page.audio_combo.count() - 1

        self.assertEqual(self.page.audio_combo.itemText(last), "随画面（免转码）")
        self.assertEqual(self.page.audio_combo.itemData(last), MUXED_AUDIO_TRACK_ID)

    def test_no_transcode_option_is_absent_without_a_muxed_variant(self) -> None:
        self.page.update_video_info(make_video(multi_language_formats()), "1440p")

        self.assertLess(self.page.audio_combo.findData(MUXED_AUDIO_TRACK_ID), 0)

    def test_single_language_video_shows_a_disabled_placeholder(self) -> None:
        self.page.update_video_info(make_video(single_language_formats()), "1080p")

        self.assertEqual(self.page.audio_combo.count(), 1)
        self.assertEqual(self.page.audio_combo.itemText(0), "默认音轨")
        self.assertEqual(self.page.audio_combo.currentData(), "")
        self.assertFalse(self.page.audio_combo.isEnabled())

    def test_local_file_clears_the_combo(self) -> None:
        self.page.update_video_info(make_video(multi_language_formats()), "1080p")

        self.page.update_local_file_info("F:/movies/demo.mkv")

        self.assertEqual(self.page.audio_combo.count(), 1)
        self.assertFalse(self.page.audio_combo.isEnabled())

    def test_casting_disables_the_combo(self) -> None:
        self.page.update_video_info(make_video(multi_language_formats()), "1080p")
        self.page.set_cast_state(True)

        self.assertFalse(self.page.audio_combo.isEnabled())

    def test_populating_does_not_emit(self) -> None:
        emitted: list[str] = []
        self.page.audio_track_changed.connect(emitted.append)

        self.page.update_video_info(make_video(multi_language_formats()), "1080p")

        self.assertEqual(emitted, [])

    def test_choosing_a_track_emits_its_id(self) -> None:
        video = make_video(multi_language_formats())
        self.page.update_video_info(video, "1080p")
        emitted: list[str] = []
        self.page.audio_track_changed.connect(emitted.append)

        self.page.audio_combo.setCurrentIndex(2)

        self.assertEqual(emitted, [list(video.audio_tracks)[2]])

    def test_wheel_without_focus_does_not_switch_tracks(self) -> None:
        self.page.update_video_info(make_video(multi_language_formats()), "1080p")
        emitted: list[str] = []
        self.page.audio_track_changed.connect(emitted.append)

        event = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        self.page.audio_combo.wheelEvent(event)

        # 换轨要重载整条流，未聚焦时滚轮必须无效。
        self.assertEqual(self.page.audio_combo.currentIndex(), 0)
        self.assertEqual(emitted, [])

class MainWindowTrackSwitchTests(unittest.TestCase):
    """用 SimpleNamespace 打桩，只驱动 MainWindow 的未绑定方法，不构造真窗口。"""

    def _state(self, *, paused: bool = False) -> tuple[SimpleNamespace, list[dict]]:
        load_calls: list[dict] = []
        video = make_video(multi_language_formats())
        state = SimpleNamespace(
            current_video=video,
            current_quality_label="1080p",
            current_audio_track_id=next(iter(video.audio_tracks)),
            _playback_finished=False,
            mpv=SimpleNamespace(
                position=lambda: 42.0,
                get_bool=lambda name: paused if name == "pause" else False,
                load=lambda video_url, audio_url, **kwargs: load_calls.append(
                    {"video_url": video_url, "audio_url": audio_url, **kwargs}
                ),
            ),
            _set_playback_finished=lambda value: setattr(state, "_playback_finished", value),
        )
        state._current_audio_track = lambda: MainWindow._current_audio_track(state)
        state._current_stream_urls = lambda quality: MainWindow._current_stream_urls(state, quality)
        return state, load_calls

    def test_default_track_is_the_first_of_the_ordered_table(self) -> None:
        video = make_video(multi_language_formats())

        selected = MainWindow._select_default_audio_track(SimpleNamespace(), video)

        self.assertEqual(selected, next(iter(video.audio_tracks)))

    def test_default_track_is_empty_without_tracks(self) -> None:
        video = make_video([video_only_fmt("137", 1080, 3000)])

        self.assertEqual(MainWindow._select_default_audio_track(SimpleNamespace(), video), "")

    def test_switching_keeps_picture_and_position(self) -> None:
        state, load_calls = self._state(paused=True)
        target = list(state.current_video.audio_tracks)[3]
        quality = state.current_video.qualities["1080p"]

        MainWindow._change_audio_track(state, target)

        self.assertEqual(state.current_audio_track_id, target)
        self.assertEqual(load_calls[0]["video_url"], quality.video_url)
        self.assertEqual(
            load_calls[0]["audio_url"], state.current_video.audio_tracks[target].url
        )
        self.assertEqual(load_calls[0]["start_position"], 42.0)
        self.assertFalse(load_calls[0]["autoplay"])

    def test_switching_while_playing_keeps_playing(self) -> None:
        state, load_calls = self._state(paused=False)

        MainWindow._change_audio_track(state, list(state.current_video.audio_tracks)[2])

        self.assertTrue(load_calls[0]["autoplay"])

    def test_switching_to_the_same_track_is_a_no_op(self) -> None:
        state, load_calls = self._state()

        MainWindow._change_audio_track(state, state.current_audio_track_id)

        self.assertEqual(load_calls, [])

    def test_unknown_track_is_ignored(self) -> None:
        state, load_calls = self._state()

        MainWindow._change_audio_track(state, "251-nonexistent")

        self.assertEqual(load_calls, [])
        self.assertEqual(state.current_audio_track_id, next(iter(state.current_video.audio_tracks)))

    def test_no_transcode_sentinel_returns_to_the_muxed_stream(self) -> None:
        state, load_calls = self._state()
        quality = state.current_video.qualities["1080p"]

        MainWindow._change_audio_track(state, MUXED_AUDIO_TRACK_ID)

        self.assertEqual(load_calls[0]["video_url"], quality.muxed_video_url)
        self.assertIsNone(load_calls[0]["audio_url"])

    def test_failed_switch_rolls_back_and_reports(self) -> None:
        state, _ = self._state()
        previous = state.current_audio_track_id

        def boom(*_args, **_kwargs):
            raise RuntimeError("mpv 挂了")

        state.mpv.load = boom
        with patch.object(
            QMessageBox, "critical", return_value=QMessageBox.StandardButton.Ok
        ) as box, self.assertLogs("tube_player.ui", level="ERROR"):
            MainWindow._change_audio_track(state, list(state.current_video.audio_tracks)[1])

        self.assertEqual(state.current_audio_track_id, previous)
        self.assertEqual(box.call_args[0][1], "切换音轨失败")

    def test_quality_switch_keeps_the_selected_track(self) -> None:
        """需求 7：换清晰度不该把音轨弹回默认。"""
        state, load_calls = self._state()
        target = list(state.current_video.audio_tracks)[4]
        MainWindow._change_audio_track(state, target)
        load_calls.clear()

        MainWindow._change_quality(state, "1440p")

        self.assertEqual(state.current_audio_track_id, target)
        self.assertEqual(
            load_calls[0]["audio_url"], state.current_video.audio_tracks[target].url
        )
        self.assertEqual(
            load_calls[0]["video_url"], state.current_video.qualities["1440p"].video_url
        )

    def test_cast_codec_follows_the_selected_track(self) -> None:
        state, _ = self._state()
        state._cast_audio_codec = lambda quality: MainWindow._cast_audio_codec(state, quality)
        quality = state.current_video.qualities["1080p"]

        self.assertEqual(state._cast_audio_codec(quality), "opus")

        state.current_audio_track_id = MUXED_AUDIO_TRACK_ID
        # 哨兵不是真音轨 → 回落到清晰度自带的编码，投屏才不会按错的编码判定转码。
        self.assertEqual(state._cast_audio_codec(quality), quality.acodec)

    def _cast_state(self, quality_label: str) -> tuple[SimpleNamespace, list[str]]:
        """打桩到"缺 FFmpeg 就返回"那一步为止，不进入选设备流程。"""
        messages: list[str] = []
        state, _ = self._state()
        state.current_quality_label = quality_label
        state._dlna_device = None
        state._dlna_cast_pending = False
        state.current_local_media_path = ""
        state.toast = SimpleNamespace(show_message=messages.append)
        state.ffmpeg_install_service = SimpleNamespace(effective_ffmpeg_dir=lambda: "")
        return state, messages

    def test_cast_without_ffmpeg_points_at_the_no_transcode_option(self) -> None:
        """验收 29：本档位有已混音变体时，提示要给出 C1 的出路。"""
        state, messages = self._cast_state("1080p")
        self.assertTrue(state.current_video.qualities["1080p"].muxed_video_url)

        MainWindow._show_cast_dialog(state)

        self.assertEqual(len(messages), 1)
        self.assertIn("随画面（免转码）", messages[0])

    def test_cast_hint_stays_plain_without_a_muxed_variant(self) -> None:
        """2160p 没有已混音变体，指路到一个选不出来的选项只会误导。"""
        state, messages = self._cast_state("2160p")
        self.assertIsNone(state.current_video.qualities["2160p"].muxed_video_url)

        MainWindow._show_cast_dialog(state)

        self.assertEqual(len(messages), 1)
        self.assertNotIn("随画面", messages[0])
        self.assertIn("FFmpeg", messages[0])


class DownloadFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.video = make_video(multi_language_formats())
        self.config = SimpleNamespace(download_dir=lambda: ".")

    def _build(self, audio_format_id: str, *, ffmpeg: bool = True):
        with patch.object(command_builder, "_ffmpeg_available", return_value=ffmpeg):
            return build_download_task(self.video, "1080p", self.config, audio_format_id)

    def test_selected_track_joins_the_format_selector(self) -> None:
        target = list(self.video.audio_tracks)[3]

        task = self._build(target)

        quality = self.video.qualities["1080p"]
        self.assertTrue(task.format_selector.startswith(f"{quality.format_id}+{target}"))

    def test_empty_id_keeps_the_quality_default(self) -> None:
        task = self._build("")

        quality = self.video.qualities["1080p"]
        self.assertTrue(
            task.format_selector.startswith(f"{quality.format_id}+{quality.audio_format_id}")
        )

    def test_sentinel_and_unknown_ids_fall_back_to_the_default(self) -> None:
        quality = self.video.qualities["1080p"]
        expected = f"{quality.format_id}+{quality.audio_format_id}"

        for audio_id in (MUXED_AUDIO_TRACK_ID, "251-nonexistent"):
            with self.subTest(audio_id=audio_id):
                self.assertTrue(self._build(audio_id).format_selector.startswith(expected))

    def test_without_ffmpeg_it_still_degrades_to_a_single_file(self) -> None:
        task = self._build(list(self.video.audio_tracks)[3], ffmpeg=False)

        self.assertEqual(task.format_selector, "best")
        self.assertIn("单文件降级", task.quality_label)

    def test_expected_bytes_uses_the_selected_track_size(self) -> None:
        target = list(self.video.audio_tracks)[3]

        task = self._build(target)

        quality = self.video.qualities["1080p"]
        self.assertEqual(
            task.expected_bytes,
            (quality.filesize or 0) + (self.video.audio_tracks[target].filesize or 0),
        )


class PreferredAudioLanguageTests(unittest.TestCase):
    """验收 14：配置里填了具体语言码就跳过系统语言。"""

    def _resolver(self, configured: str) -> SimpleNamespace:
        state = SimpleNamespace(
            config=SimpleNamespace(get=lambda _key, default=None: configured)
        )
        return state

    def test_configured_code_skips_the_system_language(self) -> None:
        with patch("resolver.youtube_resolver.system_language_tag", return_value="zh-Hans-CN"):
            language = YoutubeResolver._preferred_audio_language(self._resolver("en"))

        self.assertEqual(language, "en")

    def test_auto_falls_through_to_the_system_language(self) -> None:
        for configured in ("auto", "", "   "):
            with self.subTest(configured=configured):
                with patch(
                    "resolver.youtube_resolver.system_language_tag", return_value="zh-Hans-CN"
                ):
                    language = YoutubeResolver._preferred_audio_language(
                        self._resolver(configured)
                    )

                self.assertEqual(language, "zh-Hans-CN")

    def test_missing_config_key_still_uses_the_system_language(self) -> None:
        state = SimpleNamespace(config=SimpleNamespace(get=lambda _key, default=None: default))

        with patch("resolver.youtube_resolver.system_language_tag", return_value="ja-JP"):
            self.assertEqual(YoutubeResolver._preferred_audio_language(state), "ja-JP")

    def test_configured_code_reaches_the_selector(self) -> None:
        """配置的语言码要真的换掉默认轨，而不是只从这个方法返回。"""
        tracks = QualitySelector.select_audio_tracks(multi_language_formats(), "en")

        self.assertEqual(next(iter(tracks.values())).language, "en")


if __name__ == "__main__":
    unittest.main()




