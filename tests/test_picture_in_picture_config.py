from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app_paths import _pick_existing_with_child

from services.config_service import ConfigService


class PictureInPictureConfigTests(unittest.TestCase):
    def test_resource_root_requires_packaged_pip_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            incomplete = root / "installed" / "docs" / "assets"
            bundled = root / "bundle" / "docs" / "assets"
            incomplete.mkdir(parents=True)
            (bundled / "pip").mkdir(parents=True)
            (bundled / "pip" / "pip_style_a.svg").write_text("<svg/>", encoding="utf-8")

            selected = _pick_existing_with_child(incomplete, bundled, child="pip/pip_style_a.svg")

            self.assertEqual(selected, bundled)
    def _config(self, temp_dir: str) -> ConfigService:
        return ConfigService(
            default_path=Path("config/default_config.json"),
            user_path=Path(temp_dir) / "user.json",
        )

    def test_defaults_have_no_saved_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = self._config(temp_dir).picture_in_picture_settings()

        self.assertEqual(settings["width"], 0)
        self.assertEqual(settings["height"], 0)
        self.assertFalse(settings["muted"])

    def test_geometry_and_mute_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            config.set_picture_in_picture_settings(x=10, y=20, width=640, height=360, muted=True)
            config.save()
            settings = self._config(temp_dir).picture_in_picture_settings()

        self.assertEqual(settings, {"x": 10, "y": 20, "width": 640, "height": 360, "muted": True})

    def test_invalid_values_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            config.set("player.picture_in_picture", {"x": "bad", "width": -4, "height": None})
            settings = config.picture_in_picture_settings()

        self.assertEqual(settings["x"], 0)
        self.assertEqual(settings["width"], 0)
        self.assertEqual(settings["height"], 0)

    def test_picture_in_picture_style_defaults_to_random_and_normalizes_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(temp_dir)
            self.assertEqual(config.picture_in_picture_style(), "random")
            config.set("player.picture_in_picture_style", "style_a")
            self.assertEqual(config.picture_in_picture_style(), "style_a")
            config.set("player.picture_in_picture_style", "unknown")
            self.assertEqual(config.picture_in_picture_style(), "random")


if __name__ == "__main__":
    unittest.main()
