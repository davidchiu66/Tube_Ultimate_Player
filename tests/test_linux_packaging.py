from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_linux


class LinuxPackagingTests(unittest.TestCase):
    def _create_runtime(self, root: Path) -> tuple[Path, Path, Path, Path]:
        thirdpart = root / "3rdpart"
        thirdpart.mkdir()
        for name in ("deno", "ffmpeg", "ffprobe", "yt-dlp_linux", "libmpv.so.2"):
            (thirdpart / name).write_bytes(name.encode("ascii"))
        licenses = thirdpart / "licenses"
        licenses.mkdir()
        (licenses / "LICENSE").write_text("license", encoding="utf-8")
        (thirdpart / "third-party-manifest.sha256").write_text("hash  file\n", encoding="utf-8")

        pyinstaller = root / "dist" / "Tube_Ultimate_Player"
        pyinstaller.mkdir(parents=True)
        (pyinstaller / "Tube_Ultimate_Player").write_bytes(b"app")
        (pyinstaller / "_internal").mkdir()

        packaging = root / "packaging" / "linux"
        packaging.mkdir(parents=True)
        source_packaging = Path(build_linux.__file__).resolve().parent / "packaging" / "linux"
        for name in ("AppRun", "tube-ultimate-player", "tube-ultimate-player.desktop", "THIRD_PARTY_BUNDLED.md"):
            (packaging / name).write_text((source_packaging / name).read_text(encoding="utf-8"), encoding="utf-8")

        icons = root / "docs" / "assets" / "icons"
        icons.mkdir(parents=True)
        (icons / "app-icon-256.png").write_bytes(b"png")
        return thirdpart, pyinstaller, packaging, root / "build" / "linux" / "AppDir"

    def test_stage_appdir_contains_enhanced_runtime_and_desktop_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            thirdpart, pyinstaller, packaging, appdir = self._create_runtime(root)
            with (
                patch.object(build_linux, "ROOT", root),
                patch.object(build_linux, "THIRDPART_DIR", thirdpart),
                patch.object(build_linux, "PYINSTALLER_DIR", pyinstaller),
                patch.object(build_linux, "LINUX_PACKAGING_DIR", packaging),
                patch.object(build_linux, "APPDIR", appdir),
            ):
                result = build_linux.stage_appdir()

            bundled = result / "usr" / "bin" / "3rdpart"
            self.assertTrue((bundled / "deno").is_file())
            self.assertTrue((bundled / "ffmpeg").is_file())
            self.assertTrue((bundled / "ffprobe").is_file())
            self.assertTrue((bundled / "yt-dlp_linux").is_file())
            self.assertTrue((bundled / "libmpv.so.2").is_file())
            self.assertTrue((result / "AppRun").is_file())
            self.assertTrue(
                (result / "usr" / "share" / "applications" / "tube-ultimate-player.desktop").is_file()
            )

    def test_validate_enhanced_runtime_requires_license_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            thirdpart = Path(temp_dir)
            for name in ("deno", "ffmpeg", "ffprobe", "yt-dlp_linux", "libmpv.so.2"):
                (thirdpart / name).write_bytes(b"runtime")
            with patch.object(build_linux, "THIRDPART_DIR", thirdpart):
                with self.assertRaisesRegex(RuntimeError, "license"):
                    build_linux.validate_enhanced_runtime()

    def test_desktop_entry_targets_appimage_binary(self) -> None:
        desktop = (
            Path(build_linux.__file__).resolve().parent
            / "packaging"
            / "linux"
            / "tube-ultimate-player.desktop"
        ).read_text(encoding="utf-8")

        self.assertIn("Exec=Tube_Ultimate_Player %U", desktop)
        self.assertIn("Icon=tube-ultimate-player", desktop)


if __name__ == "__main__":
    unittest.main()
