from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_paths import APP_NAME, linux_runtime_directories
from platform_support import configure_qt_platform_environment, linux_session_type
from player.mpv_player import _libmpv_candidates
from services.config_service import detect_browser_cookie_sources
from services.config_service import ConfigService
from services.cookie_service import _resolve_firefox_profile_dir
from services.ffmpeg_install_service import FfmpegInstallService
from services.runtime_install_service import RuntimeInstallService


class LinuxQtPlatformTests(unittest.TestCase):
    def test_wayland_session_defaults_to_xwayland_xcb(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"}

        selected = configure_qt_platform_environment(env, "linux")

        self.assertEqual(selected, "xcb")
        self.assertEqual(env["QT_QPA_PLATFORM"], "xcb")

    def test_explicit_qt_platform_is_preserved(self) -> None:
        env = {"WAYLAND_DISPLAY": "wayland-0", "QT_QPA_PLATFORM": "offscreen"}

        selected = configure_qt_platform_environment(env, "linux")

        self.assertEqual(selected, "offscreen")

    def test_advanced_override_is_used_when_qt_platform_is_unset(self) -> None:
        env = {
            "WAYLAND_DISPLAY": "wayland-0",
            "TUBE_PLAYER_QPA_PLATFORM": "minimal",
        }

        selected = configure_qt_platform_environment(env, "linux")

        self.assertEqual(selected, "minimal")

    def test_session_type_falls_back_to_display_environment(self) -> None:
        self.assertEqual(linux_session_type({"DISPLAY": ":0"}), "x11")
        self.assertEqual(linux_session_type({"WAYLAND_DISPLAY": "wayland-0"}), "wayland")


class LinuxRuntimeDirectoryTests(unittest.TestCase):
    def test_xdg_directories_are_kept_separate(self) -> None:
        home = Path("/home/tester")
        env = {
            "XDG_CONFIG_HOME": "/xdg/config",
            "XDG_DATA_HOME": "/xdg/data",
            "XDG_CACHE_HOME": "/xdg/cache",
            "XDG_STATE_HOME": "/xdg/state",
            "XDG_VIDEOS_DIR": "/media/videos",
        }

        paths = linux_runtime_directories(env, home)

        self.assertEqual(paths.config, Path("/xdg/config") / APP_NAME)
        self.assertEqual(paths.data, Path("/xdg/data") / APP_NAME)
        self.assertEqual(paths.cache, Path("/xdg/cache") / APP_NAME)
        self.assertEqual(paths.logs, Path("/xdg/state") / APP_NAME / "logs")
        self.assertEqual(paths.downloads, Path("/media/videos") / APP_NAME)
        self.assertEqual(paths.updates, paths.cache / "updates")

    def test_xdg_directories_fall_back_below_home(self) -> None:
        home = Path("/home/tester")

        paths = linux_runtime_directories({}, home)

        self.assertEqual(paths.config, home / ".config" / APP_NAME)
        self.assertEqual(paths.data, home / ".local" / "share" / APP_NAME)
        self.assertEqual(paths.cache, home / ".cache" / APP_NAME)
        self.assertEqual(paths.logs, home / ".local" / "state" / APP_NAME / "logs")
        self.assertEqual(paths.downloads, home / "Videos" / APP_NAME)

    def test_linux_default_cookie_file_uses_config_directory(self) -> None:
        config_dir = Path("/xdg/config") / APP_NAME
        config = ConfigService.__new__(ConfigService)
        with (
            patch("services.config_service.sys.platform", "linux"),
            patch("services.config_service.CONFIG_DIR", config_dir),
        ):
            cookie_file = config.default_cookie_file("youtube")

        self.assertEqual(cookie_file, str(config_dir / "cookie_youtube.txt"))

    def test_videos_directory_is_read_from_xdg_user_dirs_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config_home = home / ".config"
            config_home.mkdir()
            (config_home / "user-dirs.dirs").write_text(
                'XDG_VIDEOS_DIR="$HOME/Media/Videos"\n',
                encoding="utf-8",
            )

            paths = linux_runtime_directories({}, home)

        self.assertEqual(paths.downloads, home / "Media" / "Videos" / APP_NAME)

    def test_relative_xdg_home_is_ignored(self) -> None:
        home = Path("/home/tester")

        paths = linux_runtime_directories({"XDG_CONFIG_HOME": "relative/config"}, home)

        self.assertEqual(paths.config, home / ".config" / APP_NAME)


class LinuxLibmpvDiscoveryTests(unittest.TestCase):
    def test_linux_candidates_include_bundled_and_system_sonames(self) -> None:
        with patch("player.mpv_player.ctypes.util.find_library", return_value="libmpv.so.2"):
            candidates = [str(item) for item in _libmpv_candidates("linux")]

        self.assertTrue(any(item.endswith(os.path.join("3rdpart", "libmpv.so.2")) for item in candidates))
        self.assertIn("libmpv.so.2", candidates)
        self.assertIn("libmpv.so.1", candidates)
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_windows_candidates_remain_available(self) -> None:
        candidates = [str(item) for item in _libmpv_candidates("win32")]

        self.assertTrue(any(item.endswith(os.path.join("3rdpart", "libmpv-2.dll")) for item in candidates))
        self.assertTrue(any(item.endswith(os.path.join("3rdpart", "mpv-2.dll")) for item in candidates))


class LinuxBrowserCookieTests(unittest.TestCase):
    def test_only_existing_linux_browser_profiles_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            chrome_cookie = home / ".config" / "google-chrome" / "Default" / "Network" / "Cookies"
            chrome_cookie.parent.mkdir(parents=True)
            chrome_cookie.write_bytes(b"")
            snap_firefox = home / "snap" / "firefox" / "common" / ".mozilla" / "firefox" / "abc.default"
            snap_firefox.mkdir(parents=True)
            (snap_firefox / "cookies.sqlite").write_bytes(b"")

            sources = detect_browser_cookie_sources(
                "linux",
                home,
                {"BROWSER": "firefox", "XDG_CONFIG_HOME": str(home / ".config")},
            )

        values = [value for _label, value in sources]
        labels = [label for label, _value in sources]
        self.assertIn("chrome:Default", values)
        self.assertIn(f"firefox:{snap_firefox.resolve()}", values)
        self.assertFalse(any(value.startswith("chromium:") for value in values))
        self.assertTrue(labels[0].startswith("默认浏览器 - Firefox"))

    def test_absolute_firefox_profile_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir) / "profile"
            profile.mkdir()
            (profile / "cookies.sqlite").write_bytes(b"")

            resolved = _resolve_firefox_profile_dir(str(profile.resolve()))

        self.assertEqual(resolved, profile.resolve())


class LinuxRuntimeInstallTests(unittest.TestCase):
    def test_missing_linux_runtime_does_not_offer_windows_installer(self) -> None:
        with (
            patch("services.runtime_install_service.sys.platform", "linux"),
            patch("services.runtime_install_service.detect_js_runtime", return_value=""),
        ):
            service = RuntimeInstallService.__new__(RuntimeInstallService)
            status = service.detect_runtime_status()

        self.assertFalse(status.available)
        self.assertFalse(status.automatic_install_supported)
        self.assertIn("增强版", status.display_text)

    def test_linux_ffmpeg_service_disables_windows_archive_installer(self) -> None:
        with patch("services.ffmpeg_install_service.sys.platform", "linux"):
            service = FfmpegInstallService.__new__(FfmpegInstallService)
            self.assertFalse(service.automatic_install_supported())
            with self.assertRaisesRegex(RuntimeError, "Linux"):
                service.install_info()


if __name__ == "__main__":
    unittest.main()
