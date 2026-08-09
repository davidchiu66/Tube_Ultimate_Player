from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from services.update_service import (
    PlatformInfo,
    ReleaseAsset,
    ReleaseInfo,
    UpdateService,
    asset_arch,
    detect_platform_info,
    host_cpu_arch,
    normalize_arch_label,
    read_pe_machine,
)
from ui.main_window import MainWindow


def decode_launcher_script(command: list[str]) -> str:
    """从 -EncodedCommand 参数还原出实际执行的 PowerShell 脚本。"""
    index = command.index("-EncodedCommand")
    return base64.b64decode(command[index + 1]).decode("utf-16-le")


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
            script = decode_launcher_script(command)
            self.assertIn(str(installer.resolve()), script)
            self.assertIn("$AppExecutable", script)
            self.assertIn("Wait-ForProcessExit", script)
            self.assertIn("Start-UpgradeProcess -FilePath $InstallerPath", script)
            self.assertIn("Write-UpgradeLog", script)

    def test_installer_launcher_does_not_write_script_to_disk(self) -> None:
        """S3：脚本不得落到用户可写目录，否则写入到执行之间可被替换（TOCTOU）。"""
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
            self.assertEqual(list(updates_dir.glob("*.ps1")), [])
            self.assertNotIn("-File", command)
            self.assertIn("-EncodedCommand", command)

    def test_launcher_parameters_are_quoted_as_literals(self) -> None:
        """含单引号/空格的路径必须被转义，避免拼接出可执行的额外语句。"""
        from services.update_service import build_launcher_command

        command = build_launcher_command(
            "powershell.exe",
            "Write-Output $InstallerPath",
            {"InstallerPath": "C:\\tmp\\it's here'; calc.exe #\\setup.exe"},
        )
        script = decode_launcher_script(command)

        self.assertIn("$InstallerPath = 'C:\\tmp\\it''s here''; calc.exe #\\setup.exe'", script)
        self.assertNotIn("'; calc.exe #", script.split("\n")[0].replace("''", ""))

    def test_launcher_rejects_newline_in_parameters(self) -> None:
        from services.update_service import build_launcher_command

        with self.assertRaises(RuntimeError):
            build_launcher_command("powershell.exe", "exit", {"InstallerPath": "a\nStart-Process calc"})

    def test_launcher_never_uses_detached_process(self) -> None:
        """DETACHED_PROCESS 会让 powershell.exe 不执行脚本直接退出，必须只用 CREATE_NO_WINDOW。"""
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

            flags = popen.call_args.kwargs["creationflags"]
            self.assertEqual(flags & 0x00000008, 0)
            self.assertEqual(flags, getattr(subprocess, "CREATE_NO_WINDOW", 0))

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
            script = decode_launcher_script(command)
            self.assertEqual(list(updates_dir.glob("*.ps1")), [])
            self.assertIn(str(package.resolve()), script)
            self.assertIn(str(app_dir.resolve()), script)
            self.assertIn("Wait-ForProcessExit", script)
            self.assertIn("robocopy.exe $sourceRoot $TargetDir", script)
            self.assertIn("Start-UpgradeProcess -FilePath $RestartExecutable", script)

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

    def test_windows_installer_picks_the_matching_arch(self) -> None:
        # 同一版本同时发布 x86_64 与 arm64 资产时，x86_64 机器不能选到 arm64 包。
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[
                ReleaseAsset(
                    "Tube_Ultimate_Player_setup_v1.0.0_win_arm64_with_deno_ffmpeg.exe",
                    "arm64",
                    1,
                ),
                ReleaseAsset(
                    "Tube_Ultimate_Player_setup_v1.0.0_win_x86_64_with_deno_ffmpeg.exe",
                    "x64",
                    2,
                ),
            ],
        )

        with patch("services.update_service.current_windows_arch", return_value="x86_64"):
            selected = self.service.select_upgrade_asset(release, "installer")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.download_url, "x64")

    def test_arm64_machine_skips_x86_64_assets(self) -> None:
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[
                ReleaseAsset(
                    "Tube_Ultimate_Player_setup_v1.0.0_win_x86_64.exe",
                    "x64",
                    1,
                ),
                ReleaseAsset(
                    "Tube_Ultimate_Player_portable_v1.0.0_win_arm64.zip",
                    "arm64",
                    2,
                ),
            ],
        )

        with patch("services.update_service.current_windows_arch", return_value="arm64"):
            selected = self.service.select_upgrade_asset(release, "portable")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.download_url, "arm64")

    def test_untagged_legacy_assets_still_selected_on_windows(self) -> None:
        # 旧版本发布没有架构后缀，仍按原有的 portable/installer 规则挑选，保证兼容。
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[
                ReleaseAsset("Tube_Ultimate_Player_setup_v1.0.0.exe", "legacy", 1),
                ReleaseAsset("Tube_Ultimate_Player_portable_v1.0.0.zip", "legacy", 2),
            ],
        )

        with patch("services.update_service.current_windows_arch", return_value="x86_64"):
            selected = self.service.select_upgrade_asset(release, "portable")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.download_url, "legacy")

    def _arm64_only_release(self) -> ReleaseInfo:
        return ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[
                ReleaseAsset("Tube_Ultimate_Player_setup_v1.0.0_win_arm64.exe", "arm64", 1),
                ReleaseAsset("Tube_Ultimate_Player_portable_v1.0.0_win_arm64.zip", "arm64", 2),
            ],
        )

    def test_missing_arch_never_falls_back_to_the_other_arch(self) -> None:
        # 这是 0.2.23 升级报 "does not support the version of Windows" 的根因：
        # 本机架构没有对应资产时，旧实现退回全量资产，于是 x64 机器挑到 arm64 安装包。
        release = self._arm64_only_release()

        with patch("services.update_service.current_windows_arch", return_value="x86_64"):
            for mode in ("installer", "portable"):
                with self.subTest(mode=mode):
                    self.assertIsNone(self.service.select_upgrade_asset(release, mode))

    def test_check_reports_arch_mismatch_instead_of_up_to_date(self) -> None:
        release = self._arm64_only_release()
        with patch.object(UpdateService, "fetch_latest_release", return_value=release), patch.object(
            UpdateService, "local_version", return_value="0.9.0"
        ), patch.object(UpdateService, "detect_install_mode", return_value=("installer", "安装版")), patch(
            "services.update_service.current_windows_arch", return_value="x86_64"
        ), patch(
            "services.update_service.host_cpu_arch", return_value="x86_64"
        ):
            result = self.service.check_for_updates()

        self.assertFalse(result.has_update)
        self.assertTrue(result.arch_mismatch)
        self.assertIsNone(result.selected_asset)
        self.assertIsNotNone(result.platform_info)

    def test_up_to_date_release_is_not_reported_as_arch_mismatch(self) -> None:
        release = self._arm64_only_release()
        with patch.object(UpdateService, "fetch_latest_release", return_value=release), patch.object(
            UpdateService, "local_version", return_value="1.0.0"
        ), patch.object(UpdateService, "detect_install_mode", return_value=("installer", "安装版")), patch(
            "services.update_service.current_windows_arch", return_value="x86_64"
        ):
            result = self.service.check_for_updates()

        self.assertFalse(result.has_update)
        self.assertFalse(result.arch_mismatch)

    def test_linux_skips_assets_built_for_another_arch(self) -> None:
        release = ReleaseInfo(
            tag_name="v1.0.0",
            name="1.0.0",
            published_at="",
            body="",
            html_url="https://example.invalid",
            prerelease=False,
            assets=[ReleaseAsset("Tube_Ultimate_Player_v1.0.0_aarch64.AppImage", "arm64", 1)],
        )

        with patch("services.update_service.host_cpu_arch", return_value="x86_64"):
            self.assertIsNone(self.service.select_upgrade_asset(release, "linux_appimage"))


class PlatformDetectionTests(unittest.TestCase):
    def test_normalize_arch_label_maps_known_spellings(self) -> None:
        for value, expected in (
            ("AMD64", "x86_64"),
            ("x86_64", "x86_64"),
            ("ARM64", "arm64"),
            ("aarch64", "arm64"),
            ("x86", "x86"),
            ("mips", ""),
            ("", ""),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_arch_label(value), expected)

    def test_asset_arch_reads_the_suffix(self) -> None:
        self.assertEqual(asset_arch("app_win_arm64.exe"), "arm64")
        self.assertEqual(asset_arch("app_win_x86_64_with_deno_ffmpeg.zip"), "x86_64")
        self.assertEqual(asset_arch("app_v1.0.0.exe"), "")

    def test_describe_flags_emulation_when_process_differs_from_cpu(self) -> None:
        native = PlatformInfo(os_label="Windows 11", host_arch="arm64", process_arch="arm64")
        self.assertFalse(native.emulated)
        self.assertNotIn("模拟", native.describe())

        emulated = PlatformInfo(os_label="Windows 11", host_arch="arm64", process_arch="x86_64")
        self.assertTrue(emulated.emulated)
        self.assertIn("CPU arm64", emulated.describe())
        self.assertIn("模拟运行", emulated.describe())

    def test_detect_platform_info_reports_this_machine(self) -> None:
        info = detect_platform_info()
        self.assertTrue(info.os_label)
        self.assertIn(info.host_arch, ("x86_64", "arm64", "x86"))
        self.assertIn(info.process_arch, ("x86_64", "arm64", "x86"))

    def test_read_pe_machine_identifies_a_real_executable(self) -> None:
        self.assertEqual(read_pe_machine(sys.executable), host_cpu_arch())

    def test_read_pe_machine_returns_blank_for_non_pe_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "notes.txt"
            plain.write_text("hello", encoding="utf-8")
            self.assertEqual(read_pe_machine(plain), "")
            self.assertEqual(read_pe_machine(Path(tmp) / "missing.exe"), "")

    def test_launch_rejects_a_package_built_for_another_arch(self) -> None:
        service = UpdateService.__new__(UpdateService)
        with patch("services.update_service.read_pe_machine", return_value="arm64"), patch(
            "services.update_service.host_cpu_arch", return_value="x86_64"
        ):
            with self.assertRaises(RuntimeError) as ctx:
                service.ensure_package_arch_runnable("whatever.exe")
        self.assertIn("arm64", str(ctx.exception))
        self.assertIn("x86_64", str(ctx.exception))

    def test_arm64_host_may_run_emulated_x86_64_packages(self) -> None:
        service = UpdateService.__new__(UpdateService)
        with patch("services.update_service.read_pe_machine", return_value="x86_64"), patch(
            "services.update_service.host_cpu_arch", return_value="arm64"
        ):
            service.ensure_package_arch_runnable("whatever.exe")

    def test_unreadable_machine_type_does_not_block_upgrade(self) -> None:
        service = UpdateService.__new__(UpdateService)
        with patch("services.update_service.read_pe_machine", return_value=""):
            service.ensure_package_arch_runnable("whatever.exe")


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
            thread_pool=SimpleNamespace(waitForDone=Mock(return_value=True)),
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
        state._quit_for_upgrade = lambda: MainWindow._quit_for_upgrade(state)

        with (
            patch("ui.main_window.QTimer.singleShot", side_effect=lambda _delay, callback: callback()),
            patch("ui.main_window._arm_exit_watchdog") as watchdog,
            patch("ui.main_window.QApplication.instance", return_value=None),
        ):
            MainWindow._launch_downloaded_upgrade(state, "portable.zip", "portable")

        state.update_service.launch_portable_update.assert_called_once_with("portable.zip")
        state.close.assert_called_once_with()
        watchdog.assert_called_once_with()
        state.thread_pool.waitForDone.assert_called_once()

    def test_upgrade_exit_quits_application_event_loop(self) -> None:
        state = self._state("installer")
        application = Mock()

        with (
            patch("ui.main_window._arm_exit_watchdog"),
            patch("ui.main_window.QApplication.instance", return_value=application),
        ):
            MainWindow._quit_for_upgrade(state)

        state.close.assert_called_once_with()
        application.quit.assert_called_once_with()

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
