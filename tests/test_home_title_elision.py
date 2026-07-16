from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from ui.text_elision import elide_multiline_text


class HomeTitleElisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_long_chinese_title_is_limited_to_three_lines(self) -> None:
        label = QLabel()
        title = (
            "新版汽水音乐兼容适配安卓车机，不限制车型品牌，也不限制原厂还是后改车机，"
            "支持优盘、手机、电脑等多种方式安装"
        )

        result = elide_multiline_text(label, title, 120, 3)

        self.assertEqual(len(result.splitlines()), 3)
        self.assertTrue(result.endswith("..."))

    def test_short_title_is_not_elided(self) -> None:
        label = QLabel()

        result = elide_multiline_text(label, "简短标题", 200, 3)

        self.assertEqual(result, "简短标题")

    def test_english_title_uses_word_wrapping_and_three_dots(self) -> None:
        label = QLabel()
        title = "A very long video title with enough words to overflow the available title area"

        result = elide_multiline_text(label, title, 100, 3)

        self.assertLessEqual(len(result.splitlines()), 3)
        self.assertTrue(result.endswith("..."))


if __name__ == "__main__":
    unittest.main()
