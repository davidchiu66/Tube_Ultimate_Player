from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import build_macos


class MacosBuildTests(unittest.TestCase):
    def test_architecture_tags_are_explicit(self) -> None:
        self.assertEqual(build_macos.ARCH_MACHINES["mac_x86_64"], {"x86_64", "amd64"})
        self.assertEqual(build_macos.ARCH_MACHINES["mac_arm64"], {"arm64", "aarch64"})

    def test_package_names_include_version_and_architecture(self) -> None:
        self.assertIn("mac_x86_64", "Tube_Ultimate_Player_v1.2.3_mac_x86_64.dmg")
        self.assertIn("mac_arm64", "Tube_Ultimate_Player_v1.2.3_mac_arm64.zip")

    def test_bundled_libmpv_finds_the_packaged_library(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "Player.app"
            library = app / "Contents" / "Frameworks" / "libmpv.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"mpv")
            self.assertEqual(build_macos.bundled_libmpv(app), library)

    def test_homebrew_link_verification_rejects_external_dependency(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "Player.app"
            library = app / "Contents" / "Frameworks" / "libmpv.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"mpv")
            result = type("Result", (), {"stdout": f"{library}:\n\t/opt/homebrew/opt/ffmpeg/lib/libavcodec.dylib"})()
            with patch("build_macos.subprocess.run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "Unbundled Homebrew dependency"):
                    build_macos.verify_no_homebrew_links(app)

    def test_plist_version_helper_adds_missing_key(self) -> None:
        with TemporaryDirectory() as temp_dir, patch("build_macos.subprocess.run") as run:
            plist = Path(temp_dir) / "Info.plist"
            plist.write_text("plist", encoding="utf-8")
            run.side_effect = [
                __import__("subprocess").CalledProcessError(1, "PlistBuddy"),
                None,
            ]
            build_macos._set_plist_string(plist, "CFBundleVersion", "0.2.31")
            self.assertIn("Add :CFBundleVersion string 0.2.31", run.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
