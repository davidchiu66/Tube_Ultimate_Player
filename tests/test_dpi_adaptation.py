from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.toolbar import PlayerToolbar


class DpiAdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_adaptive_window_length_never_exceeds_available_space(self) -> None:
        self.assertLessEqual(
            MainWindow._adaptive_window_length(900, preferred=1180, minimum=720),
            900,
        )
        self.assertLessEqual(
            MainWindow._adaptive_window_length(620, preferred=760, minimum=520),
            620,
        )
        self.assertEqual(MainWindow._adaptive_window_length(500, preferred=760, minimum=520), 470)

    def test_toolbar_starts_compact_to_keep_initial_minimum_width_small(self) -> None:
        toolbar = PlayerToolbar()

        self.assertEqual(toolbar._compact_mode, "icon")
        self.assertEqual(toolbar.playlist_button.text(), "")
        self.assertLess(toolbar.minimumSizeHint().width(), 900)


if __name__ == "__main__":
    unittest.main()
