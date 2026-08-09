from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from services import chromium_cookie_extractor as extractor
from services import config_service
from services.config_service import (
    _browser_kind_from_executable,
    _executable_from_command,
    detect_browser_cookie_sources,
    detect_portable_default_browser_sources,
)


def _make_chromium_profile(user_data: Path, profile: str = "Default", *, local_state: bool = True) -> Path:
    profile_dir = user_data / profile
    (profile_dir / "Network").mkdir(parents=True, exist_ok=True)
    (profile_dir / "Network" / "Cookies").write_bytes(b"")
    if local_state:
        (user_data / "Local State").write_text(
            json.dumps({"os_crypt": {"encrypted_key": ""}}), encoding="utf-8"
        )
    return profile_dir


class ExecutableFromCommandTests(unittest.TestCase):
    def test_quoted_path_with_spaces_keeps_the_whole_path(self) -> None:
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "My Browser" / "chrome.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            command = f'"{exe}" --single-argument %1'
            self.assertEqual(_executable_from_command(command), exe)

    def test_unquoted_path_stops_at_the_exe_suffix(self) -> None:
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "firefox.exe"
            exe.write_bytes(b"")
            self.assertEqual(_executable_from_command(f"{exe} -osint -url %1"), exe)

    def test_missing_file_yields_none(self) -> None:
        self.assertIsNone(_executable_from_command(r'"C:\nope\gone.exe" %1'))
        self.assertIsNone(_executable_from_command(""))


class BrowserKindTests(unittest.TestCase):
    def test_known_executables_map_to_yt_dlp_browser_names(self) -> None:
        cases = {
            "chrome.exe": "chrome",
            "msedge.exe": "edge",
            "brave.exe": "brave",
            "firefox.exe": "firefox",
            "vivaldi.exe": "vivaldi",
            "chromium.exe": "chromium",
        }
        for name, expected in cases.items():
            self.assertEqual(_browser_kind_from_executable(Path("X:/x") / name), expected, name)

    def test_msedge_is_not_swallowed_by_the_chrome_rule(self) -> None:
        self.assertEqual(_browser_kind_from_executable(Path("X:/msedge.exe")), "edge")

    def test_unknown_executable_yields_empty(self) -> None:
        self.assertEqual(_browser_kind_from_executable(Path("X:/notepad.exe")), "")
        self.assertEqual(_browser_kind_from_executable(None), "")


class PortableDefaultBrowserTests(unittest.TestCase):
    def test_user_data_next_to_the_executable_is_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "PortableChrome"
            exe = root / "chrome.exe"
            root.mkdir(parents=True)
            exe.write_bytes(b"")
            profile_dir = _make_chromium_profile(root / "User Data")

            with mock.patch.object(
                config_service, "default_windows_browser_command", return_value=f'"{exe}" -- %1'
            ):
                found = detect_portable_default_browser_sources()

            self.assertEqual([item[0] for item in found], ["chrome"])
            # 必须是绝对路径：便携版的库不在 %LOCALAPPDATA% 下，profile 名定位不到。
            self.assertEqual(found[0][2], f"chrome:{profile_dir.resolve()}")

    def test_portableapps_layout_two_levels_up_is_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "GoogleChromePortable"
            exe = root / "App" / "Chrome-bin" / "chrome.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            profile_dir = _make_chromium_profile(root / "Data" / "profile")

            with mock.patch.object(
                config_service, "default_windows_browser_command", return_value=f'"{exe}" -- %1'
            ):
                found = detect_portable_default_browser_sources()

            self.assertEqual([item[2] for item in found], [f"chrome:{profile_dir.resolve()}"])

    def test_portable_firefox_profile_is_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "FirefoxPortable"
            exe = root / "App" / "firefox64" / "firefox.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            profile_dir = root / "Data" / "profile"
            profile_dir.mkdir(parents=True)
            (profile_dir / "cookies.sqlite").write_bytes(b"")

            with mock.patch.object(
                config_service, "default_windows_browser_command", return_value=f'"{exe}" -- %1'
            ):
                found = detect_portable_default_browser_sources()

            self.assertEqual([item[0] for item in found], ["firefox"])
            self.assertEqual(found[0][2], f"firefox:{profile_dir.resolve()}")

    def test_installed_browser_without_portable_layout_yields_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            exe = Path(tmp) / "chrome.exe"
            exe.write_bytes(b"")
            with mock.patch.object(
                config_service, "default_windows_browser_command", return_value=f'"{exe}" -- %1'
            ):
                self.assertEqual(detect_portable_default_browser_sources(), [])

    def test_no_registry_entry_yields_nothing(self) -> None:
        with mock.patch.object(config_service, "default_windows_browser_command", return_value=""):
            self.assertEqual(detect_portable_default_browser_sources(), [])


class SourceListingTests(unittest.TestCase):
    """便携版默认浏览器要压过同内核的安装版。"""

    def _sources(self, portable: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
        with TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            _make_chromium_profile(local / "Google" / "Chrome" / "User Data")
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(Path(tmp) / "Roaming")}
            with (
                mock.patch.object(
                    config_service, "detect_portable_default_browser_sources", return_value=portable
                ),
                mock.patch.object(config_service, "_detect_default_windows_browser", return_value="chrome"),
            ):
                return detect_browser_cookie_sources(platform_name="win32", environ=env)

    def test_portable_default_outranks_the_installed_same_kind_browser(self) -> None:
        portable = [("chrome", "Chrome 便携版 (Default)", r"chrome:E:\Portable\User Data\Default")]
        sources = self._sources(portable)

        self.assertTrue(sources[0][0].startswith("默认浏览器"))
        self.assertEqual(sources[0][1], r"chrome:E:\Portable\User Data\Default")
        # 安装版 Chrome 仍然列出，但不能再冒充默认浏览器。
        installed = [label for label, value in sources if value == "chrome:Default"]
        self.assertEqual(len(installed), 1)
        self.assertFalse(installed[0].startswith("默认浏览器"))

    def test_without_a_portable_default_the_installed_browser_keeps_the_marker(self) -> None:
        sources = self._sources([])
        marked = [label for label, value in sources if value == "chrome:Default"]
        self.assertEqual(len(marked), 1)
        self.assertTrue(marked[0].startswith("默认浏览器"))


class ResolveProfileTests(unittest.TestCase):
    def test_absolute_profile_directory_keeps_its_own_user_data(self) -> None:
        with TemporaryDirectory() as tmp:
            user_data = Path(tmp) / "Portable" / "User Data"
            profile_dir = _make_chromium_profile(user_data)
            resolved = extractor._resolve_profile("chrome", str(profile_dir))
            self.assertEqual(resolved, (user_data, profile_dir))

    def test_absolute_user_data_directory_falls_back_to_default_profile(self) -> None:
        with TemporaryDirectory() as tmp:
            user_data = Path(tmp) / "Portable" / "User Data"
            _make_chromium_profile(user_data)
            resolved = extractor._resolve_profile("chrome", str(user_data))
            self.assertEqual(resolved, (user_data, user_data / "Default"))

    def test_absolute_path_never_reads_the_installed_local_state(self) -> None:
        """便携版必须用自己的 Local State 解密，混用安装版的密钥会整批解不开。"""
        with TemporaryDirectory() as tmp:
            user_data = Path(tmp) / "Portable" / "User Data"
            profile_dir = _make_chromium_profile(user_data)
            with mock.patch.object(extractor, "_user_data_dir") as installed:
                resolved = extractor._resolve_profile("chrome", str(profile_dir))
            installed.assert_not_called()
            self.assertEqual(resolved[0], user_data)

    def test_missing_absolute_path_yields_none(self) -> None:
        self.assertIsNone(extractor._resolve_profile("chrome", r"E:\definitely\not\here"))

    def test_profile_name_still_resolves_under_local_app_data(self) -> None:
        with TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            user_data = local / "Google" / "Chrome" / "User Data"
            _make_chromium_profile(user_data, "Profile 1")
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}):
                resolved = extractor._resolve_profile("chrome", "Profile 1")
            self.assertEqual(resolved, (user_data, user_data / "Profile 1"))


class LooksAbsoluteTests(unittest.TestCase):
    def test_drive_letters_and_unc_are_absolute(self) -> None:
        for value in (r"E:\Portable\User Data", "E:/Portable", r"\\server\share\profile"):
            self.assertTrue(extractor._looks_absolute(value), value)

    def test_profile_names_are_not_absolute(self) -> None:
        for value in ("Default", "Profile 1", "", "p1.default-release"):
            self.assertFalse(extractor._looks_absolute(value), value)


class SpecSplittingTests(unittest.TestCase):
    def test_drive_letter_colon_survives_the_browser_split(self) -> None:
        """spec 形如 chrome:E:\\...，取内核时必须限制只切一次。"""
        spec = r"chrome:E:\Program Files\Portable\User Data\Default"
        self.assertEqual(spec.split(":", 1)[0], "chrome")
        self.assertEqual(spec.split(":", 1)[1], r"E:\Program Files\Portable\User Data\Default")


if __name__ == "__main__":
    unittest.main()
