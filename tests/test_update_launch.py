from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from services.update_service import ReleaseAsset, ReleaseInfo, UpdateService
from ui.main_window import MainWindow


class UpdateLaunchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = UpdateService(SimpleNamespace(effective_proxy=lambda: ("", "")))

    def test_installer_launcher_waits_for_application_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            installer = root / "setup.exe"
            installer.write_bytes(b"installer")
            updates_dir = root / "updates"
            updates_dir.mkdir()
            with (
                patch.object(self.service, "updates_dir", return_value=updates_dir),
                patch("services.update_service.sys.platform", "win32"),
                patch("services.update_service.shutil.which", return_value="powershell.exe"),
                patch("services.update_service.subprocess.Popen") as popen,
            ):
                self.service.launch_installer(installer)

            command = popen.call_args.args[0]
            script = (updates_dir / "installer_launcher.ps1").read_text(encoding="utf-8-sig")
            self.assertIn(str(installer.resolve()), command)
            self.assertIn("Wait-Process -Id $ParentPid", script)
            self.assertIn("Start-Process -FilePath $InstallerPath", script)

    def test_portable_updater_waits_replaces_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package = root / "portable.zip"
            package.write_bytes(b"zip")
            app_dir = root / "app"
            app_dir.mkdir()
            executable = app_dir / "Tube_Ultimate_Player.exe"
            executable.write_bytes(b"exe")
            updates_dir = root / "updates"
            updates_dir.mkdir()

            with (
                patch.object(self.service, "updates_dir", return_value=updates_dir),
                patch("services.update_service.sys.platform", "win32"),
                patch("services.update_service.sys.frozen", True, create=True),
                patch("services.update_service.sys.executable", str(executable)),
                patch("services.update_service.APP_DIR", app_dir),
                patch("services.update_service.shutil.which", return_value="powershell.exe"),
                patch("services.update_service.subprocess.Popen") as popen,
            ):
                self.service.launch_portable_update(package)

            command = popen.call_args.args[0]
            script_path = updates_dir / "portable_updater.ps1"
            script = script_path.read_text(encoding="utf-8-sig")
            self.assertIn(str(package.resolve()), command)
            self.assertIn(str(app_dir.resolve()), command)
            self.assertIn("Wait-Process -Id $ParentPid", script)
            self.assertIn("robocopy.exe $sourceRoot $TargetDir", script)
            self.assertIn("Start-Process -FilePath $RestartExecutable", script)

    def test_linux_prefers_enhanced_appimage_asset(self) -> None:
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[
                ReleaseAsset("Tube_Ultimate_Player_v1.0.0_x86_64.AppImage", "standard", 1),
                ReleaseAsset(
                    "Tube_Ultimate_Player_v1.0.0_x86_64_with_deno_ffmpeg.AppImage",
                    "enhanced",
                    2,
                ),
                ReleaseAsset("tube-ultimate-player_1.0.0_amd64.deb", "deb", 3),
            ],
        )

        selected = self.service.select_upgrade_asset(release, "linux_appimage")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.download_url, "enhanced")

    def test_linux_deb_mode_selects_deb_asset(self) -> None:
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[ReleaseAsset("tube-ultimate-player_1.0.0_amd64.deb", "deb", 3)],
        )

        selected = self.service.select_upgrade_asset(release, "linux_deb")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.download_url, "deb")


class UpdateLaunchUiTests(unittest.TestCase):
    def _state(self, install_mode: str = "portable") -> SimpleNamespace:
        return SimpleNamespace(
            _last_update_result=SimpleNamespace(
                install_mode=install_mode,
                install_mode_label="便携版" if install_mode == "portable" else "安装包版",
                has_update=True,
            ),
            about_page=SimpleNamespace(
                set_upgrade_progress=Mock(),
                set_status=Mock(),
                set_upgrade_available=Mock(),
            ),
            update_service=SimpleNamespace(
                launch_portable_update=Mock(),
                launch_installer=Mock(),
            ),
            close=Mock(),
        )

    def test_download_completion_waits_for_user_confirmation(self) -> None:
        state = self._state()
        state._launch_downloaded_upgrade = Mock()

        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            MainWindow._update_download_success(state, "portable.zip")

        state._launch_downloaded_upgrade.assert_not_called()
        state.about_page.set_status.assert_called_with("升级包已下载，等待用户启动升级。")

    def test_confirmed_portable_upgrade_launches_helper_then_closes(self) -> None:
        state = self._state()

        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda _delay, callback: callback()):
            MainWindow._launch_downloaded_upgrade(state, "portable.zip", "portable")

        state.update_service.launch_portable_update.assert_called_once_with("portable.zip")
        state.close.assert_called_once_with()

    def test_linux_download_completion_does_not_launch_or_close(self) -> None:
        state = self._state("linux_appimage")
        state._launch_downloaded_upgrade = Mock()

        with (
            patch("ui.main_window.QMessageBox.information"),
            patch("ui.main_window.QDesktopServices.openUrl"),
        ):
            MainWindow._update_download_success(state, "/tmp/player.AppImage")

        state._launch_downloaded_upgrade.assert_not_called()
        state.close.assert_not_called()
        state.about_page.set_status.assert_called_with("Linux 升级包已下载，请退出应用后手动安装或替换。")


if __name__ == "__main__":
    unittest.main()
