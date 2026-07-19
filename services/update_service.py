from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app_paths import APP_DIR, SOURCE_DIR, UPDATE_DIR, read_app_version
from services.config_service import ConfigService


logger = logging.getLogger("tube_player.update")

REPO_SLUG = "davidchiu66/Tube_Ultimate_Player"
REPO_URL = f"https://github.com/{REPO_SLUG}"
RELEASES_API = f"https://api.github.com/repos/{REPO_SLUG}/releases"

INSTALLER_LAUNCHER_SCRIPT = r'''param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [Parameter(Mandatory=$true)][int]$ParentPid
)

$ErrorActionPreference = "Stop"
$logPath = Join-Path ([System.IO.Path]::GetDirectoryName($InstallerPath)) "installer-launch.log"

try {
    Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Start-Process -FilePath $InstallerPath
}
catch {
    ($_ | Out-String) | Out-File -LiteralPath $logPath -Encoding UTF8
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "新版安装程序启动失败。请查看日志：`n$logPath`n`n$($_.Exception.Message)",
        "Tube_Ultimate_Player 升级失败",
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}
'''

PORTABLE_UPDATER_SCRIPT = r'''param(
    [Parameter(Mandatory=$true)][string]$ArchivePath,
    [Parameter(Mandatory=$true)][string]$TargetDir,
    [Parameter(Mandatory=$true)][string]$RestartExecutable,
    [Parameter(Mandatory=$true)][int]$ParentPid
)

$ErrorActionPreference = "Stop"
$workRoot = Join-Path ([System.IO.Path]::GetDirectoryName($ArchivePath)) ("portable-update-" + [System.Guid]::NewGuid().ToString("N"))
$logPath = Join-Path ([System.IO.Path]::GetDirectoryName($ArchivePath)) "portable-update.log"

try {
    Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $workRoot)

    $sourceRoot = $workRoot
    $topItems = @(Get-ChildItem -LiteralPath $workRoot -Force)
    $topFiles = @($topItems | Where-Object { -not $_.PSIsContainer })
    $topDirs = @($topItems | Where-Object { $_.PSIsContainer })
    if ($topFiles.Count -eq 0 -and $topDirs.Count -eq 1) {
        $sourceRoot = $topDirs[0].FullName
    }

    & robocopy.exe $sourceRoot $TargetDir /E /COPY:DAT /DCOPY:DAT /R:10 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) {
        throw "文件替换失败，Robocopy 退出代码: $LASTEXITCODE"
    }

    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $RestartExecutable -WorkingDirectory $TargetDir
}
catch {
    ($_ | Out-String) | Out-File -LiteralPath $logPath -Encoding UTF8
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "便携版自动升级失败。请查看日志：`n$logPath`n`n$($_.Exception.Message)",
        "Tube_Ultimate_Player 升级失败",
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}
finally {
    if (Test-Path -LiteralPath $workRoot) {
        Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
'''


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    content_type: str = ""


@dataclass(slots=True)
class ReleaseInfo:
    tag_name: str
    name: str
    published_at: str
    body: str
    html_url: str
    prerelease: bool
    assets: list[ReleaseAsset]


@dataclass(slots=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    has_update: bool
    install_mode: str
    install_mode_label: str
    release: ReleaseInfo
    selected_asset: ReleaseAsset | None


class UpdateService:
    def __init__(self, config: ConfigService) -> None:
        self.config = config

    def local_version(self) -> str:
        return read_app_version()

    def updates_dir(self) -> Path:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        return UPDATE_DIR

    def detect_install_mode(self) -> tuple[str, str]:
        if sys.platform.startswith("linux"):
            if os.environ.get("APPIMAGE", "").strip():
                return "linux_appimage", "Linux AppImage"
            root = APP_DIR.resolve()
            root_text = root.as_posix().lower()
            if root_text.startswith("/opt/") or root_text.startswith("/usr/"):
                return "linux_deb", "Linux DEB"
            return "linux_appimage", "Linux 开发/AppImage 模式"

        root = APP_DIR
        root_text = str(root).lower()
        if "program files" in root_text:
            return "installer", "安装包版"

        portable_markers = (
            root / "3rdpart",
            root / "README.md",
            root / "app_version.txt",
        )
        if all(marker.exists() for marker in portable_markers):
            return "portable", "便携版"

        source_markers = (
            SOURCE_DIR / "3rdpart",
            SOURCE_DIR / "README.md",
            SOURCE_DIR / "app_version.txt",
        )
        if not getattr(sys, "frozen", False) and all(marker.exists() for marker in source_markers):
            return "portable", "开发/便携模式"

        return "installer", "安装包版"

    def fetch_latest_release(self) -> ReleaseInfo:
        releases = self._read_json(RELEASES_API)
        if not isinstance(releases, list) or not releases:
            raise RuntimeError("未获取到可用的版本发布信息")
        payload = self._select_release_payload(releases)
        assets = [
            ReleaseAsset(
                name=str(asset.get("name", "")),
                download_url=str(asset.get("browser_download_url", "")),
                size=int(asset.get("size", 0) or 0),
                content_type=str(asset.get("content_type", "")),
            )
            for asset in payload.get("assets", [])
        ]
        return ReleaseInfo(
            tag_name=str(payload.get("tag_name", "")).strip(),
            name=str(payload.get("name", "")).strip(),
            published_at=str(payload.get("published_at", "")).strip(),
            body=str(payload.get("body", "") or "").strip(),
            html_url=str(payload.get("html_url", "") or REPO_URL).strip(),
            prerelease=bool(payload.get("prerelease", False)),
            assets=assets,
        )

    @staticmethod
    def _select_release_payload(releases: list[dict]) -> dict:
        for release in releases:
            if release.get("draft"):
                continue
            return release
        raise RuntimeError("没有找到可用的版本发布信息")

    def check_for_updates(self) -> UpdateCheckResult:
        release = self.fetch_latest_release()
        current_version = self.local_version()
        latest_version = release.tag_name or release.name or current_version
        install_mode, install_mode_label = self.detect_install_mode()
        selected_asset = self.select_upgrade_asset(release, install_mode)
        has_update = compare_versions(latest_version, current_version) > 0 and selected_asset is not None
        return UpdateCheckResult(
            current_version=current_version,
            latest_version=latest_version,
            has_update=has_update,
            install_mode=install_mode,
            install_mode_label=install_mode_label,
            release=release,
            selected_asset=selected_asset,
        )

    def select_upgrade_asset(self, release: ReleaseInfo, install_mode: str) -> ReleaseAsset | None:
        assets = release.assets
        if install_mode.startswith("linux"):
            suffix = ".deb" if install_mode == "linux_deb" else ".appimage"
            matching = [asset for asset in assets if asset.name.lower().endswith(suffix)]
            matching = [
                asset
                for asset in matching
                if any(arch in asset.name.lower() for arch in ("x86_64", "amd64"))
            ] or matching
            for asset in matching:
                name = asset.name.lower()
                if "with_deno_ffmpeg" in name or "with-deno-ffmpeg" in name:
                    return asset
            return matching[0] if matching else None

        if install_mode == "portable":
            for asset in assets:
                name = asset.name.lower()
                if "portable" in name and name.endswith(".zip"):
                    return asset
            for asset in assets:
                if asset.name.lower().endswith(".zip"):
                    return asset

        for asset in assets:
            name = asset.name.lower()
            if name.endswith(".exe") and ("setup" in name or "installer" in name):
                return asset
        for asset in assets:
            if asset.name.lower().endswith(".exe"):
                return asset
        return None

    @staticmethod
    def automatic_upgrade_supported(install_mode: str) -> bool:
        return sys.platform.startswith("win") and install_mode in ("portable", "installer")

    def download_target_path(self, asset: ReleaseAsset) -> Path:
        filename = asset.name or "update_package.bin"
        return self.updates_dir() / filename

    def launch_installer(self, package_path: str | Path) -> None:
        package = Path(package_path).resolve()
        if not package.is_file():
            raise RuntimeError("升级安装包不存在")
        if package.suffix.lower() != ".exe":
            raise RuntimeError("当前升级文件不是可执行安装包")
        if not sys.platform.startswith("win"):
            raise RuntimeError("自动启动安装程序目前仅支持 Windows")

        powershell = shutil.which("powershell.exe")
        if not powershell:
            raise RuntimeError("未找到 Windows PowerShell，无法在应用退出后启动安装程序")
        script_path = self.updates_dir() / "installer_launcher.ps1"
        script_path.write_text(INSTALLER_LAUNCHER_SCRIPT, encoding="utf-8-sig")
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
            "-InstallerPath",
            str(package),
            "-ParentPid",
            str(os.getpid()),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            subprocess.Popen(command, close_fds=True, creationflags=creation_flags)
        except OSError as exc:
            logger.exception("failed to launch update installer path=%s", package)
            raise RuntimeError(f"无法启动升级安装程序：{exc}") from exc

    def launch_portable_update(self, package_path: str | Path) -> None:
        package = Path(package_path).resolve()
        if not package.is_file():
            raise RuntimeError("便携版升级包不存在")
        if package.suffix.lower() != ".zip":
            raise RuntimeError("便携版升级包必须是 ZIP 文件")
        if not sys.platform.startswith("win"):
            raise RuntimeError("便携版自动替换目前仅支持 Windows")
        if not getattr(sys, "frozen", False):
            raise RuntimeError("开发源码模式不支持自动覆盖，请使用版本控制工具更新源码")

        powershell = shutil.which("powershell.exe")
        if not powershell:
            raise RuntimeError("未找到 Windows PowerShell，无法启动便携版自动升级")

        script_path = self.updates_dir() / "portable_updater.ps1"
        script_path.write_text(PORTABLE_UPDATER_SCRIPT, encoding="utf-8-sig")
        executable = Path(sys.executable).resolve()
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
            "-ArchivePath",
            str(package),
            "-TargetDir",
            str(APP_DIR.resolve()),
            "-RestartExecutable",
            str(executable),
            "-ParentPid",
            str(os.getpid()),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        try:
            subprocess.Popen(
                command,
                close_fds=True,
                creationflags=creation_flags,
            )
        except OSError as exc:
            logger.exception("failed to launch portable updater package=%s", package)
            raise RuntimeError(f"无法启动便携版升级程序：{exc}") from exc

    def build_request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json, application/json;q=0.9, */*;q=0.8",
                "User-Agent": "Tube_Ultimate_Player/1.0",
            },
        )

    def open_url(self, url: str):
        opener = self._build_opener()
        request = self.build_request(url)
        return opener.open(request, timeout=30)

    def _read_json(self, url: str) -> dict:
        try:
            with self.open_url(url) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.exception("update api http error url=%s code=%s", url, exc.code)
            raise RuntimeError(f"访问更新接口失败，HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            logger.exception("update api network error url=%s", url)
            raise RuntimeError(f"访问更新接口失败：{exc.reason}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("update api parse error url=%s", url)
            raise RuntimeError("更新接口返回内容无法解析") from exc

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers: list[urllib.request.BaseHandler] = []
        _source, proxy = self.config.effective_proxy()
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        return urllib.request.build_opener(*handlers)


def compare_versions(left: str, right: str) -> int:
    left_key = _version_key(left)
    right_key = _version_key(right)
    if left_key > right_key:
        return 1
    if left_key < right_key:
        return -1
    return 0


def _version_key(raw: str) -> tuple[tuple[int, ...], int, str]:
    value = raw.strip().lstrip("vV")
    core_text, suffix = _split_version(value)
    core = tuple(int(part) for part in re.findall(r"\d+", core_text)) or (0,)
    suffix_text = suffix.lower()
    suffix_rank = _suffix_rank(suffix_text)
    return core, suffix_rank, suffix_text


def _split_version(value: str) -> tuple[str, str]:
    if "-" not in value:
        return value, ""
    core, suffix = value.split("-", 1)
    return core, suffix


def _suffix_rank(suffix: str) -> int:
    if not suffix:
        return 100
    order = {
        "dev": 10,
        "alpha": 20,
        "a": 20,
        "beta": 30,
        "b": 30,
        "rc": 40,
    }
    for key, rank in order.items():
        if suffix.startswith(key):
            return rank
    return 50
