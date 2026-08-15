from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from resolver.models import VideoQuality  # noqa: E402
from resolver.quality_selector import select_quality_by_tier  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from ui.settings_page import SettingsPage  # noqa: E402


def _quality(label: str, height: int, fps: int = 30) -> VideoQuality:
    return VideoQuality(
        label=label,
        height=height,
        width=max(1, height * 16 // 9),
        fps=fps,
        vcodec="avc1",
        acodec="mp4a.40.2",
        ext="mp4",
        format_id=label,
        video_url=f"https://example.com/{label}.mp4",
    )


class QualityTierSelectionTests(unittest.TestCase):
    def test_high_and_low_do_not_depend_on_mapping_order(self) -> None:
        qualities = {
            "720p": _quality("720p", 720),
            "480p": _quality("480p", 480),
            "1080p60": _quality("1080p60", 1080, 60),
        }

        self.assertEqual(select_quality_by_tier(qualities, "high").label, "1080p60")
        self.assertEqual(select_quality_by_tier(qualities, "low").label, "480p")

    def test_medium_uses_the_middle_unique_height_and_best_fps(self) -> None:
        qualities = {
            "1080p": _quality("1080p", 1080),
            "720p": _quality("720p", 720),
            "480p": _quality("480p", 480),
            "1080p60": _quality("1080p60", 1080, 60),
            "720p60": _quality("720p60", 720, 60),
        }

        selected = select_quality_by_tier(qualities, "medium")

        self.assertEqual(selected.label, "720p60")

    def test_medium_with_even_height_count_chooses_the_lower_half(self) -> None:
        qualities = {
            "1080p": _quality("1080p", 1080),
            "720p": _quality("720p", 720),
        }

        self.assertEqual(select_quality_by_tier(qualities, "medium").label, "720p")

    def test_single_empty_and_unknown_tier_edges(self) -> None:
        only = {"360p": _quality("360p", 360)}

        for tier in ("high", "medium", "low", "unknown"):
            with self.subTest(tier=tier):
                self.assertEqual(select_quality_by_tier(only, tier).label, "360p")
        self.assertIsNone(select_quality_by_tier({}, "medium"))

    def test_malformed_dimensions_are_treated_as_zero(self) -> None:
        malformed = _quality("bad", 0)
        malformed.height = "not-a-number"
        malformed.fps = "also-invalid"
        valid = _quality("360p", 360)

        self.assertEqual(select_quality_by_tier({"bad": malformed, "valid": valid}, "high").label, "360p")

        self.assertIsNone(select_quality_by_tier({"bad": malformed}, "high"))


class DefaultQualityConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        default_path = root / "default.json"
        default_path.write_text('{"player": {"default_quality": "high"}}', encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=root / "user.json")

    def test_tiers_are_normalized_and_legacy_values_fall_back_high(self) -> None:
        for raw, expected in (
            ("high", "high"),
            ("medium", "medium"),
            ("LOW", "low"),
            ("Auto", "high"),
            ("", "high"),
            ("1080p", "high"),
        ):
            with self.subTest(raw=raw):
                self.config.set("player.default_quality", raw)
                self.assertEqual(self.config.default_quality_tier(), expected)

    def test_only_legacy_exact_labels_are_exposed_as_overrides(self) -> None:
        for raw, expected in (
            ("1080p", "1080p"),
            ("Auto", ""),
            ("medium", ""),
            ("", ""),
        ):
            with self.subTest(raw=raw):
                self.config.set("player.default_quality", raw)
                self.assertEqual(self.config.default_quality_label_override(), expected)


class DefaultQualitySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_combo_lists_three_tiers_and_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            defaults = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
            defaults["download"]["save_dir"] = str(root / "downloads")
            for site in ("youtube", "bilibili"):
                cookie_path = root / f"cookie_{site}.txt"
                cookie_path.write_text("", encoding="utf-8")
                defaults["cookies"][site]["file"] = str(cookie_path)
            default_path = root / "default.json"
            default_path.write_text(json.dumps(defaults, ensure_ascii=False), encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=root / "user.json")

            with patch("ui.settings_page.detect_browser_cookie_sources", return_value=[]):
                page = SettingsPage(config)
            self.addCleanup(page.deleteLater)

            values = [
                page.default_quality_combo.itemData(index)
                for index in range(page.default_quality_combo.count())
            ]
            self.assertEqual(values, ["high", "medium", "low"])
            self.assertEqual(page.default_quality_combo.currentData(), "high")

            page.default_quality_combo.setCurrentIndex(page.default_quality_combo.findData("low"))
            page.save()

            self.assertEqual(config.get("player.default_quality"), "low")
            persisted = json.loads((root / "user.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["player"]["default_quality"], "low")

    def test_unrelated_save_preserves_legacy_exact_quality_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            defaults = json.loads(Path("config/default_config.json").read_text(encoding="utf-8"))
            defaults["download"]["save_dir"] = str(root / "downloads")
            default_path = root / "default.json"
            default_path.write_text(json.dumps(defaults, ensure_ascii=False), encoding="utf-8")
            user_path = root / "user.json"
            user_path.write_text('{"player": {"default_quality": "1080p"}}', encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=user_path)

            with patch("ui.settings_page.detect_browser_cookie_sources", return_value=[]):
                page = SettingsPage(config)
            self.addCleanup(page.deleteLater)

            self.assertEqual(page.default_quality_combo.currentData(), "high")
            page.proxy_edit.setText("http://127.0.0.1:7890")
            page.save()
            self.assertEqual(config.get("player.default_quality"), "1080p")

            page.default_quality_combo.setCurrentIndex(page.default_quality_combo.findData("low"))
            page.save()
            self.assertEqual(config.get("player.default_quality"), "low")


if __name__ == "__main__":
    unittest.main()
