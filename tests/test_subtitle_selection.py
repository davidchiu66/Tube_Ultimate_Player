"""字幕选择的界面行为：下拉框短名单 + 完整列表对话框。"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from resolver.models import SubtitleInfo, VideoInfo  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.player_page import SUBTITLE_MORE_SENTINEL, SUBTITLE_SHORTLIST, PlayerPage  # noqa: E402
from ui.subtitle_dialog import SubtitlePickerDialog  # noqa: E402
from ui.toast import Toast  # noqa: E402


def make_subtitles(count: int) -> dict[str, SubtitleInfo]:
    return {
        f"lang{index}:manual": SubtitleInfo(
            language=f"lang{index}",
            ext="srt",
            url=f"https://x/{index}.srt",
            name=f"Language {index}",
        )
        for index in range(count)
    }


def make_video(subtitles: dict[str, SubtitleInfo]) -> VideoInfo:
    return VideoInfo(video_id="vid", title="标题", duration=60, subtitles=subtitles)


class ShortlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.addCleanup(self.page.deleteLater)

    def test_small_list_has_no_more_entry(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(3)), "1080p")

        # 关闭 + 3 条
        self.assertEqual(self.page.subtitle_combo.count(), 4)
        self.assertLess(self.page.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL), 0)

    def test_large_list_is_truncated_with_a_more_entry(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(4872)), "1080p")

        # 关闭 + 短名单 + 「更多字幕…」
        self.assertEqual(self.page.subtitle_combo.count(), SUBTITLE_SHORTLIST + 2)
        self.assertGreater(self.page.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL), 0)
        self.assertIn("4872", self.page.subtitle_combo.itemText(self.page.subtitle_combo.count() - 1))

    def test_switching_video_resets_the_list(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(50)), "1080p")
        self.page.update_video_info(make_video(make_subtitles(2)), "1080p")

        self.assertEqual(self.page.subtitle_combo.count(), 3)
        self.assertLess(self.page.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL), 0)

    def test_choosing_a_shortlist_item_emits_its_key(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(5)), "1080p")
        emitted: list[str] = []
        self.page.subtitle_changed.connect(emitted.append)

        self.page.subtitle_combo.setCurrentIndex(2)

        self.assertEqual(emitted, ["lang1:manual"])

    def test_picker_result_is_inserted_and_emitted(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(4872)), "1080p")
        emitted: list[str] = []
        self.page.subtitle_changed.connect(emitted.append)

        with patch.object(SubtitlePickerDialog, "exec", return_value=1):
            with patch.object(SubtitlePickerDialog, "selected_key", return_value="lang3000:manual"):
                self.page.subtitle_combo.setCurrentIndex(
                    self.page.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL)
                )

        self.assertEqual(emitted, ["lang3000:manual"])
        self.assertEqual(self.page.subtitle_combo.currentData(), "lang3000:manual")

    def test_cancelling_the_picker_keeps_the_previous_choice(self) -> None:
        self.page.update_video_info(make_video(make_subtitles(4872)), "1080p")
        self.page.subtitle_combo.setCurrentIndex(1)
        previous = self.page.subtitle_combo.currentData()
        emitted: list[str] = []
        self.page.subtitle_changed.connect(emitted.append)

        with patch.object(SubtitlePickerDialog, "exec", return_value=0):
            self.page.subtitle_combo.setCurrentIndex(
                self.page.subtitle_combo.findData(SUBTITLE_MORE_SENTINEL)
            )

        self.assertEqual(emitted, [])
        self.assertEqual(self.page.subtitle_combo.currentData(), previous)


class EmptySubtitleStateTests(unittest.TestCase):
    """P1：没有字幕轨时要说清是"站点没给"，而不是让用户对着"关闭"猜。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.addCleanup(self.page.deleteLater)

    def test_no_subtitles_shows_an_explicit_item(self) -> None:
        self.page.update_video_info(make_video({}), "1080p")

        self.assertEqual(self.page.subtitle_combo.count(), 1)
        self.assertEqual(self.page.subtitle_combo.itemText(0), "无可用字幕")
        self.assertEqual(self.page.subtitle_combo.currentData(), "")

    def test_no_subtitles_shows_in_the_meta_line(self) -> None:
        self.page.update_video_info(make_video({}), "1080p")
        self.assertIn("无字幕", self.page.meta_label.text())

        self.page.update_video_info(make_video(make_subtitles(3)), "1080p")
        self.assertIn("字幕 3 个", self.page.meta_label.text())

    def test_combo_stays_disabled_without_tracks(self) -> None:
        self.page.update_video_info(make_video({}), "1080p")
        self.page.set_playback_available(True)

        self.assertFalse(self.page.subtitle_combo.isEnabled())

    def test_switching_back_to_a_subtitled_video_restores_the_combo(self) -> None:
        self.page.update_video_info(make_video({}), "1080p")
        self.page.set_playback_available(True)

        self.page.update_video_info(make_video(make_subtitles(2)), "1080p")

        self.assertTrue(self.page.subtitle_combo.isEnabled())
        self.assertEqual(self.page.subtitle_combo.itemText(0), "关闭")
        self.assertEqual(self.page.subtitle_combo.count(), 3)

    def test_local_file_has_no_subtitle_tracks(self) -> None:
        self.page.update_local_file_info("D:/movie.mp4")

        self.assertEqual(self.page.subtitle_combo.count(), 1)
        self.assertEqual(self.page.subtitle_combo.itemText(0), "无可用字幕")


class PickerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, subtitles: dict[str, SubtitleInfo]) -> SubtitlePickerDialog:
        dialog = SubtitlePickerDialog(subtitles, current_key="")
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_all_tracks_are_listed(self) -> None:
        dialog = self._dialog(make_subtitles(300))

        self.assertEqual(dialog.list_widget.count(), 300)

    def test_search_matches_readable_name_and_code(self) -> None:
        subtitles = {
            "zh-TW:manual": SubtitleInfo(language="zh-TW", ext="srt", url="u", name="Chinese (Taiwan)"),
            "ai-zh:manual": SubtitleInfo(language="ai-zh", ext="srt", data="d"),
            "en:manual": SubtitleInfo(language="en", ext="srt", url="u", name="English"),
        }
        dialog = self._dialog(subtitles)

        dialog.search_edit.setText("zh")
        visible = [
            dialog.list_widget.item(row).text()
            for row in range(dialog.list_widget.count())
            if not dialog.list_widget.item(row).isHidden()
        ]
        self.assertEqual(len(visible), 2)

        dialog.search_edit.setText("中文")
        visible = [
            dialog.list_widget.item(row).text()
            for row in range(dialog.list_widget.count())
            if not dialog.list_widget.item(row).isHidden()
        ]
        self.assertEqual(len(visible), 1)

    def test_hidden_selection_is_not_returned(self) -> None:
        dialog = self._dialog(make_subtitles(5))
        dialog.list_widget.setCurrentRow(0)
        dialog.search_edit.setText("Language 4")

        self.assertEqual(dialog.selected_key(), "")


class SubtitleFailureToastTests(unittest.TestCase):
    """429 的处置建议有两三句话，提示框不能把它裁成一行、也不能 3 秒就消失。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _toast(self) -> Toast:
        host = QWidget()
        host.resize(1200, 700)
        self.addCleanup(host.deleteLater)
        return Toast(host)

    def test_long_message_gets_room_for_every_line(self) -> None:
        toast = self._toast()
        long_message = (
            "字幕加载失败：字幕接口暂时限流（HTTP 429）。这条是机器翻译字幕，"
            "YouTube 的翻译接口按 IP 限量，已自动重试 3 次仍未成功。"
            "建议改选原文字幕（如 English），或等一两分钟再试。"
        )

        toast.show_message("短提示")
        short_height = toast.height()
        toast.show_message(long_message)

        self.assertLessEqual(toast.width(), 420)
        self.assertGreater(toast.height(), short_height)
        self.assertGreaterEqual(toast.height(), toast.heightForWidth(toast.width()))

    def test_short_message_still_uses_the_default_timeout(self) -> None:
        state = SimpleNamespace(
            _subtitle_request_id=7,
            toast=SimpleNamespace(shown=[]),
        )
        state.toast.show_message = lambda message, timeout_ms=3000: state.toast.shown.append(
            (message, timeout_ms)
        )

        MainWindow._subtitle_failed(state, 7, "en:manual", "字幕内容为空")

        self.assertEqual(state.toast.shown[0][1], 3000)

    def test_rate_limit_message_stays_on_screen_longer(self) -> None:
        state = SimpleNamespace(_subtitle_request_id=7, toast=SimpleNamespace(shown=[]))
        state.toast.show_message = lambda message, timeout_ms=3000: state.toast.shown.append(
            (message, timeout_ms)
        )

        MainWindow._subtitle_failed(
            state,
            7,
            "zh-Hans:auto",
            "字幕接口暂时限流（HTTP 429）。这条是机器翻译字幕，建议改选原文字幕（如 English），或等一两分钟再试。",
        )

        self.assertGreater(state.toast.shown[0][1], 3000)


if __name__ == "__main__":
    unittest.main()
