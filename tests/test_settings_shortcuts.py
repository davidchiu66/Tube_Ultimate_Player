from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from services.config_service import ConfigService, detect_js_runtime
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
            page.close()

    def test_cookie_browser_combo_only_lists_detected_browsers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=Path(temp_dir) / "user.json",
            )
            config.set("youtube.cookie_browser", "chrome")
            config.save()

            with patch(
                "ui.settings_page.detect_browser_cookie_sources",
                return_value=[("Microsoft Edge (Default)", "edge:Default")],
            ):
                page = SettingsPage(config)

            values = [page.cookie_browser_combo.itemData(index) for index in range(page.cookie_browser_combo.count())]
            self.assertEqual(values, ["auto", "", "edge:Default"])
            self.assertEqual(page.cookie_browser_combo.currentData(), "auto")
            page.close()

    def test_bundled_deno_is_preferred_as_js_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            deno = Path(temp_dir) / "deno.exe"
            deno.write_bytes(b"deno")
            with (
                patch("services.config_service.thirdpart_path", return_value=deno),
                patch("services.config_service.shutil.which", return_value=None),
            ):
                runtime = detect_js_runtime()

        self.assertEqual(runtime, f"deno:{deno}")


if __name__ == "__main__":
    unittest.main()
