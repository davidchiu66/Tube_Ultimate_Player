from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from ui.text_elision import _WidthCache, elide_multiline_text, format_seconds


class _CountingMetrics:
    """记录 horizontalAdvance 调用次数的假字体度量对象。"""

    def __init__(self) -> None:
        self.calls = 0

    def averageCharWidth(self) -> int:  # noqa: N802 - 与 QFontMetrics 接口保持一致
        return 10

    def horizontalAdvance(self, text: str) -> int:  # noqa: N802
        self.calls += 1
        return 10 * len(text)


class WidthCacheTests(unittest.TestCase):
    def test_each_character_is_measured_once(self) -> None:
        metrics = _CountingMetrics()
        cache = _WidthCache(metrics)

        for _ in range(50):
            cache.text_width("abcabcabc")

        self.assertEqual(metrics.calls, 3)
        self.assertEqual(cache.text_width("abc"), 30)

    def test_zero_width_character_falls_back_to_average(self) -> None:
        class ZeroMetrics(_CountingMetrics):
            def horizontalAdvance(self, text: str) -> int:  # noqa: N802
                self.calls += 1
                return 0

        cache = _WidthCache(ZeroMetrics())
        self.assertEqual(cache.text_width("ab"), 20)


class ElisionBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_two_line_limit_with_three_dots(self) -> None:
        label = QLabel()
        title = "这是一个非常非常长的视频标题" * 6

        result = elide_multiline_text(label, title, 160, 2)

        self.assertEqual(len(result.splitlines()), 2)
        self.assertTrue(result.endswith("..."))

    def test_result_is_stable_across_repeated_calls(self) -> None:
        label = QLabel()
        title = "A very long video title with enough words to overflow the available area"

        first = elide_multiline_text(label, title, 120, 2)
        second = elide_multiline_text(label, title, 120, 2)

        self.assertEqual(first, second)

    def test_whitespace_only_text_returns_empty(self) -> None:
        label = QLabel()
        self.assertEqual(elide_multiline_text(label, "   \n\t ", 120, 2), "")

    def test_zero_line_budget_returns_empty(self) -> None:
        label = QLabel()
        self.assertEqual(elide_multiline_text(label, "标题", 120, 0), "")

    def test_long_title_batch_stays_fast(self) -> None:
        label = QLabel()
        title = "混合 Mixed 长标题 with numbers 1234567890 和中文字符" * 8

        started = time.perf_counter()
        for _ in range(200):
            elide_multiline_text(label, title, 200, 3)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 2.0, f"200 次折行耗时 {elapsed:.3f}s，字符宽度缓存可能失效")


class FormatSecondsTests(unittest.TestCase):
    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(format_seconds(75), "01:15")

    def test_hours_are_included(self) -> None:
        self.assertEqual(format_seconds(3725), "01:02:05")

    def test_none_is_zero(self) -> None:
        self.assertEqual(format_seconds(0), "00:00")


if __name__ == "__main__":
    unittest.main()
