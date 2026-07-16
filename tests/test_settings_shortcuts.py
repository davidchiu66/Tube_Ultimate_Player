from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from services.config_service import ConfigService
from services.shortcut_service import SHORTCUT_DEFINITIONS
from ui.settings_page import SettingsPage


class ShortcutSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_shortcut_tab_presents_all_configurable_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=Path(temp_dir) / "user.json",
            )
            page = SettingsPage(config)

            self.assertEqual(page.tabs.count(), 2)
            self.assertEqual(page.tabs.tabText(1), "快捷键")
            self.assertEqual(set(page.shortcut_edits), {item.action for item in SHORTCUT_DEFINITIONS})
            self.assertEqual(page.shortcut_edits["cast"].keySequence().toString(), "Ctrl+C")


if __name__ == "__main__":
    unittest.main()
