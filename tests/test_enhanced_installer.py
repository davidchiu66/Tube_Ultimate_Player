from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_installer
import build_portable


class EnhancedInstallerBuildTests(unittest.TestCase):
    def test_enhanced_runtime_validation_requires_all_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            thirdpart = Path(temp_dir)
            for name in build_installer.ENHANCED_RUNTIME_FILES:
                (thirdpart / name).write_bytes(b"binary")

            with patch("build_installer.THIRDPART_DIR", thirdpart):
                build_installer.validate_enhanced_runtime()

            (thirdpart / "ffprobe.exe").unlink()
            with (
                patch("build_installer.THIRDPART_DIR", thirdpart),
                self.assertRaisesRegex(RuntimeError, "ffprobe.exe"),
            ):
                build_installer.validate_enhanced_runtime()

    def test_enhanced_installer_uses_expected_filename_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir) / "dist"

            def fake_run(command, **_kwargs) -> None:
                self.assertIn("/DOutputSuffix=_with_deno_ffmpeg", command)
                # 架构标签必须传给 Inno Setup，落到 OutputBaseFilename。
                self.assertIn("/DArchTag=win_x86_64", command)
                self.assertIn("/DInstallArch=x64compatible", command)
                output_dir = dist_dir / "installer-with-deno-ffmpeg"
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "Tube_Ultimate_Player_setup_v0.2.8_win_x86_64_with_deno_ffmpeg.exe").write_bytes(b"installer")

            with (
                patch("build_installer.DIST_DIR", dist_dir),
                patch("build_installer.shutil.which", return_value="ISCC.exe"),
                patch("build_installer.subprocess.run", side_effect=fake_run),
            ):
                result = build_installer.build_installer("0.2.8", with_deno_ffmpeg=True)

        self.assertEqual(result.name, "Tube_Ultimate_Player_setup_v0.2.8_win_x86_64_with_deno_ffmpeg.exe")

    def test_installer_arm64_arch_maps_to_arm64_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist_dir = Path(temp_dir) / "dist"

            def fake_run(command, **_kwargs) -> None:
                self.assertIn("/DArchTag=win_arm64", command)
                self.assertIn("/DInstallArch=arm64", command)
                output_dir = dist_dir / "installer"
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "Tube_Ultimate_Player_setup_v0.2.8_win_arm64.exe").write_bytes(b"installer")

            with (
                patch("build_installer.DIST_DIR", dist_dir),
                patch("build_installer.shutil.which", return_value="ISCC.exe"),
                patch("build_installer.subprocess.run", side_effect=fake_run),
            ):
                result = build_installer.build_installer("0.2.8", arch="win_arm64")

        self.assertEqual(result.name, "Tube_Ultimate_Player_setup_v0.2.8_win_arm64.exe")


class EnhancedPortableBuildTests(unittest.TestCase):
    def test_enhanced_portable_validation_requires_all_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            thirdpart = Path(temp_dir)
            for name in build_portable.ENHANCED_RUNTIME_FILES:
                (thirdpart / name).write_bytes(b"binary")

            with patch("build_portable.THIRDPART_DIR", thirdpart):
                build_portable.validate_enhanced_runtime()

            (thirdpart / "deno.exe").unlink()
            with (
                patch("build_portable.THIRDPART_DIR", thirdpart),
                self.assertRaisesRegex(RuntimeError, "deno.exe"),
            ):
                build_portable.validate_enhanced_runtime()

    def test_enhanced_portable_zip_contains_bundled_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist_dir = root / "dist"
            bundle_dir = dist_dir / build_portable.RELEASE_NAME
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "Tube_Ultimate_Player.exe").write_bytes(b"app")
            thirdpart = root / "3rdpart"
            thirdpart.mkdir()
            for name in build_portable.ENHANCED_RUNTIME_FILES:
                (thirdpart / name).write_bytes(b"binary")
            for name in ("README.md", "THIRD_PARTY_NOTICES.md", "app_version.txt"):
                (root / name).write_text("content", encoding="utf-8")

            with (
                patch("build_portable.ROOT", root),
                patch("build_portable.DIST_DIR", dist_dir),
                patch("build_portable.THIRDPART_DIR", thirdpart),
                patch("build_portable.ENHANCED_PORTABLE_DIR", dist_dir / "enhanced"),
            ):
                result = build_portable.assemble_portable("0.2.8", with_deno_ffmpeg=True)

            self.assertEqual(
                result.name,
                "Tube_Ultimate_Player_portable_v0.2.8_win_x86_64_with_deno_ffmpeg.zip",
            )
            with build_portable.zipfile.ZipFile(result) as archive:
                names = set(archive.namelist())

        self.assertIn("3rdpart/deno.exe", names)
        self.assertIn("3rdpart/ffmpeg.exe", names)
        self.assertIn("3rdpart/ffprobe.exe", names)
        self.assertIn("THIRD_PARTY_NOTICES.md", names)

    def test_portable_zip_carries_arm64_arch_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dist_dir = root / "dist"
            bundle_dir = dist_dir / build_portable.RELEASE_NAME
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "Tube_Ultimate_Player.exe").write_bytes(b"app")
            thirdpart = root / "3rdpart"
            thirdpart.mkdir()
            for name in ("README.md", "THIRD_PARTY_NOTICES.md", "app_version.txt"):
                (root / name).write_text("content", encoding="utf-8")

            with (
                patch("build_portable.ROOT", root),
                patch("build_portable.DIST_DIR", dist_dir),
                patch("build_portable.THIRDPART_DIR", thirdpart),
                patch("build_portable.PORTABLE_DIR", dist_dir / "portable"),
            ):
                result = build_portable.assemble_portable("0.2.8", arch="win_arm64")

        self.assertEqual(result.name, "Tube_Ultimate_Player_portable_v0.2.8_win_arm64.zip")


if __name__ == "__main__":
    unittest.main()
