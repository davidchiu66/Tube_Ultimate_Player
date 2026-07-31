from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app_paths import APP_DIR, SOURCE_DIR, UPDATE_DIR, read_app_version
from services.config_service import ConfigService


logger = logging.getLogger("tube_player.update")

REPO_SLUG = "davidchiu66/Tube_Ultimate_Player"
REPO_URL = f"https://github.com/{REPO_SLUG}"
RELEASES_API = f"https://api.github.com/repos/{REPO_SLUG}/releases"

LAUNCHER_COMMON_PRELUDE = r'''function Write-UpgradeLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message
    try { Add-Content -LiteralPath $script:LogPath -Value $line -Encoding UTF8 } catch { }
}

function Wait-ForProcessExit {
    param([int]$TargetPid, [string]$ExecutablePath, [int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $alive = @()
        if ($TargetPid -gt 0) {
            $alive += @(Get-Process -Id $TargetPid -ErrorAction SilentlyContinue)
        }
        if ($ExecutablePath) {
            $leaf = [System.IO.Path]::GetFileNameWithoutExtension($ExecutablePath)
            $alive += @(Get-Process -Name $leaf -ErrorAction SilentlyContinue | Where-Object {
                try { $_.Path -and ($_.Path -eq $ExecutablePath) } catch { $false }
            })
        }
        if ($alive.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Start-UpgradeProcess {
    param([string]$FilePath, [string]$WorkingDirectory)
    $arguments = @{ FilePath = $FilePath; PassThru = $true }
    if ($WorkingDirectory) { $arguments["WorkingDirectory"] = $WorkingDirectory }
    try {
        $process = Start-Process @arguments
        Write-UpgradeLog ("已启动 {0}，进程 Id={1}" -f $FilePath, $process.Id)
        return $process
    }
    catch {
        Write-UpgradeLog ("直接启动失败，尝试以管理员身份启动：" + $_.Exception.Message)
        $arguments["Verb"] = "RunAs"
        $process = Start-Process @arguments
        Write-UpgradeLog ("已提权启动 {0}，进程 Id={1}" -f $FilePath, $process.Id)
        return $process
    }
}
'''

INSTALLER_LAUNCHER_SCRIPT = r'''$ErrorActionPreference = "Stop"
$script:LogPath = Join-Path ([System.IO.Path]::GetDirectoryName($InstallerPath)) "installer-launch.log"

__COMMON__

Write-UpgradeLog ("启动器开始运行 pid={0} 安装包={1} 父进程={2}" -f $PID, $InstallerPath, $ParentPid)

try {
    if (-not (Wait-ForProcessExit -TargetPid $ParentPid -ExecutablePath $AppExecutable -TimeoutSeconds 120)) {
        Write-UpgradeLog "等待旧版进程退出超时，仍继续启动安装程序"
    } else {
        Write-UpgradeLog "旧版进程已退出"
    }
    Start-Sleep -Milliseconds 800
    if (-not (Test-Path -LiteralPath $InstallerPath)) {
        throw "安装包不存在：$InstallerPath"
    }
    $process = Start-UpgradeProcess -FilePath $InstallerPath -WorkingDirectory ([System.IO.Path]::GetDirectoryName($InstallerPath))
    Start-Sleep -Seconds 2
    if ($process.HasExited -and $process.ExitCode -ne 0) {
        throw "安装程序启动后立即退出，退出代码: $($process.ExitCode)"
    }
    Write-UpgradeLog "安装程序启动成功"
}
catch {
    Write-UpgradeLog ("失败：" + ($_ | Out-String))
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "新版安装程序启动失败。请查看日志：`n$script:LogPath`n`n$($_.Exception.Message)",
        "Tube_Ultimate_Player 升级失败",
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}
'''.replace("__COMMON__", LAUNCHER_COMMON_PRELUDE)

PORTABLE_UPDATER_SCRIPT = r'''$ErrorActionPreference = "Stop"
$workRoot = Join-Path ([System.IO.Path]::GetDirectoryName($ArchivePath)) ("portable-update-" + [System.Guid]::NewGuid().ToString("N"))
$script:LogPath = Join-Path ([System.IO.Path]::GetDirectoryName($ArchivePath)) "portable-update.log"

__COMMON__

Write-UpgradeLog ("便携版升级开始 pid={0} 升级包={1} 目标目录={2}" -f $PID, $ArchivePath, $TargetDir)

try {
    if (-not (Wait-ForProcessExit -TargetPid $ParentPid -ExecutablePath $RestartExecutable -TimeoutSeconds 120)) {
        Write-UpgradeLog "等待旧版进程退出超时，文件替换可能失败"
    } else {
        Write-UpgradeLog "旧版进程已退出"
    }
    Start-Sleep -Milliseconds 800
    New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $workRoot)
    Write-UpgradeLog "升级包解压完成"

    $sourceRoot = $workRoot
    $topItems = @(Get-ChildItem -LiteralPath $workRoot -Force)
    $topFiles = @($topItems | Where-Object { -not $_.PSIsContainer })
    $topDirs = @($topItems | Where-Object { $_.PSIsContainer })
    if ($topFiles.Count -eq 0 -and $topDirs.Count -eq 1) {
        $sourceRoot = $topDirs[0].FullName
    }

    & robocopy.exe $sourceRoot $TargetDir /E /COPY:DAT /DCOPY:DAT /R:10 /W:1 /NFL /NDL /NJH /NJS /NP
    $robocopyExit = $LASTEXITCODE
    Write-UpgradeLog ("Robocopy 退出代码: {0}" -f $robocopyExit)
    if ($robocopyExit -ge 8) {
        throw "文件替换失败，Robocopy 退出代码: $robocopyExit"
    }

    Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    Start-UpgradeProcess -FilePath $RestartExecutable -WorkingDirectory $TargetDir | Out-Null
    Write-UpgradeLog "便携版升级完成并已重启应用"
}
catch {
    Write-UpgradeLog ("失败：" + ($_ | Out-String))
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "便携版自动升级失败。请查看日志：`n$script:LogPath`n`n$($_.Exception.Message)",
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
'''.replace("__COMMON__", LAUNCHER_COMMON_PRELUDE)


# 升级包只允许来自 GitHub 官方发布域名，防止被重定向到第三方主机。
TRUSTED_DOWNLOAD_HOSTS = (
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)

HASH_CHUNK_SIZE = 1024 * 1024
SHA256_PATTERN = re.compile(r"\b([a-fA-F0-9]{64})\b")

# powershell.exe 命令行总长上限（Windows 为 32767），留出可执行文件与开关的余量。
MAX_ENCODED_COMMAND_CHARS = 30000


def quote_powershell_literal(value: str) -> str:
    """把值包成 PowerShell 单引号字面量：单引号成对转义，其中不做任何变量展开。"""
    text = str(value)
    if "\x00" in text or "\r" in text or "\n" in text:
        raise RuntimeError("升级参数包含非法字符，已终止升级")
    return "'" + text.replace("'", "''") + "'"


def build_launcher_command(
    powershell: str,
    script_body: str,
    parameters: dict[str, str | int],
) -> list[str]:
    """把升级脚本连同参数编译成 -EncodedCommand 形式的命令行。

    原实现把脚本写到用户可写的 updates 目录再以 -File 执行，从写入到执行之间存在
    TOCTOU 窗口：任何本地进程都能替换脚本内容，进而以本进程权限执行任意代码
    （安装版还可能被提权到管理员）。改为内联传递后磁盘上不再有可篡改的脚本。
    """
    prelude_lines = [f"${name} = {quote_powershell_literal(value)}" for name, value in parameters.items()]
    script = "\n".join(prelude_lines) + "\n" + script_body
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    if len(encoded) > MAX_ENCODED_COMMAND_CHARS:
        raise RuntimeError("升级脚本参数过长，无法启动升级程序")
    return [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-EncodedCommand",
        encoded,
    ]


@dataclass(slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    content_type: str = ""
    digest: str = ""


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
                digest=str(asset.get("digest", "") or ""),
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

    def resolve_expected_sha256(self, release: ReleaseInfo, asset: ReleaseAsset) -> str:
        """按 asset digest -> 校验和清单资产 -> Release 正文 的顺序解析期望哈希。"""
        digest = normalize_sha256(asset.digest)
        if digest:
            logger.info("update hash source=asset-digest asset=%s", asset.name)
            return digest

        for checksum_asset in release.assets:
            if checksum_asset is asset or not _is_checksum_asset(checksum_asset.name, asset.name):
                continue
            try:
                ensure_trusted_download_url(checksum_asset.download_url)
                with self.open_url(checksum_asset.download_url) as response:
                    text = response.read(256 * 1024).decode("utf-8", errors="replace")
            except (RuntimeError, OSError, urllib.error.URLError) as exc:
                logger.warning("读取校验和清单失败 name=%s error=%s", checksum_asset.name, exc)
                continue
            bare_allowed = checksum_asset.name.lower().endswith((".sha256", ".sha256sum"))
            digest = extract_sha256_for(text, asset.name, allow_bare=bare_allowed)
            if digest:
                logger.info("update hash source=%s asset=%s", checksum_asset.name, asset.name)
                return digest

        digest = extract_sha256_for(release.body, asset.name)
        if digest:
            logger.info("update hash source=release-body asset=%s", asset.name)
            return digest

        logger.warning("未在发布信息中找到 %s 的 SHA256 校验值", asset.name)
        return ""

    def verify_authenticode(self, package_path: str | Path) -> None:
        """无法取得哈希时的兜底：校验 Windows 数字签名，签名无效则拒绝执行。"""
        package = Path(package_path).resolve()
        if not sys.platform.startswith("win"):
            raise RuntimeError("升级包缺少 SHA256 校验值，已终止升级")
        powershell = shutil.which("powershell.exe")
        if not powershell:
            raise RuntimeError("升级包缺少 SHA256 校验值，且未找到 PowerShell 无法校验数字签名")
        command = [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "$ErrorActionPreference='Stop';"
            f"(Get-AuthenticodeSignature -LiteralPath '{package}').Status.ToString()",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.exception("authenticode 校验执行失败 path=%s", package)
            raise RuntimeError(f"无法校验升级包数字签名：{exc}") from exc
        status = (completed.stdout or "").strip().splitlines()[-1:] or [""]
        if status[0].strip() != "Valid":
            logger.error("authenticode 校验未通过 path=%s status=%s", package, status[0].strip())
            raise RuntimeError(f"升级包数字签名校验未通过（{status[0].strip() or '未知状态'}），已终止升级")
        logger.info("authenticode 校验通过 path=%s", package)

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
        parameters = {
            "InstallerPath": str(package),
            "ParentPid": str(os.getpid()),
            "AppExecutable": str(Path(sys.executable).resolve()),
        }
        command = build_launcher_command(powershell, INSTALLER_LAUNCHER_SCRIPT, parameters)
        self._spawn_launcher(command, "升级安装程序")

    def _spawn_launcher(self, command: list[str], label: str) -> None:
        """以独立进程启动 PowerShell 升级脚本。

        注意：不能使用 DETACHED_PROCESS —— powershell.exe 在没有控制台的情况下会立即以
        退出码 0 结束且不执行脚本任何一行，这会导致升级包"下载完成却从未被执行"。
        CREATE_NO_WINDOW 会为子进程分配一个不可见控制台，脚本可正常运行，
        且 Windows 不会因父进程退出而结束子进程。
        """
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                command,
                close_fds=True,
                creationflags=creation_flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.exception("failed to launch %s command=%s", label, command[:1])
            raise RuntimeError(f"无法启动{label}：{exc}") from exc
        logger.info("%s启动器已创建 pid=%s", label, process.pid)

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

        executable = Path(sys.executable).resolve()
        parameters = {
            "ArchivePath": str(package),
            "TargetDir": str(APP_DIR.resolve()),
            "RestartExecutable": str(executable),
            "ParentPid": str(os.getpid()),
        }
        command = build_launcher_command(powershell, PORTABLE_UPDATER_SCRIPT, parameters)
        self._spawn_launcher(command, "便携版升级程序")

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


def normalize_sha256(value: str) -> str:
    """接受 "sha256:xxx" / "SHA256 = xxx" / 裸哈希三种写法，返回小写 64 位十六进制。"""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if ":" in text:
        algorithm, _, remainder = text.partition(":")
        if algorithm.strip() not in ("sha256", "sha-256"):
            return ""
        text = remainder.strip()
    match = SHA256_PATTERN.search(text)
    return match.group(1).lower() if match else ""


def extract_sha256_for(text: str, filename: str, *, allow_bare: bool = False) -> str:
    """从校验和清单或 Release 正文里取出指定文件的 SHA256。"""
    content = str(text or "")
    target = str(filename or "").strip().lower()
    if not content:
        return ""
    if target:
        for line in content.splitlines():
            if target not in line.lower():
                continue
            match = SHA256_PATTERN.search(line)
            if match:
                return match.group(1).lower()
    if allow_bare:
        matches = SHA256_PATTERN.findall(content)
        if len(matches) == 1:
            return matches[0].lower()
    return ""


def _is_checksum_asset(candidate_name: str, target_name: str) -> bool:
    name = str(candidate_name or "").strip().lower()
    if not name:
        return False
    if name in (f"{str(target_name).lower()}.sha256", f"{str(target_name).lower()}.sha256sum"):
        return True
    return any(
        keyword in name
        for keyword in ("sha256sums", "sha256sum.txt", "checksums", "checksum.txt", "sha256.txt")
    )


def ensure_trusted_download_url(url: str, allowed_hosts: tuple[str, ...] = TRUSTED_DOWNLOAD_HOSTS) -> str:
    """要求下载地址使用 HTTPS 且主机在白名单内，否则拒绝下载。"""
    text = str(url or "").strip()
    if not text:
        raise RuntimeError("下载地址为空")
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() != "https":
        raise RuntimeError(f"下载地址不是 HTTPS，已拒绝：{text}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise RuntimeError(f"下载地址缺少主机名：{text}")
    for allowed in allowed_hosts:
        allowed = allowed.lower()
        if host == allowed or host.endswith("." + allowed):
            return text
    raise RuntimeError(f"下载地址不在受信任的发布域名内，已拒绝：{host}")


def sha256_file(path: str | Path, chunk_size: int = HASH_CHUNK_SIZE) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(max(4096, chunk_size)):
            digest.update(chunk)
    return digest.hexdigest()


def verify_downloaded_file(
    path: str | Path,
    *,
    expected_size: int = 0,
    expected_sha256: str = "",
) -> None:
    """校验已下载文件的大小与 SHA256，任一不符即抛出中文错误。"""
    target = Path(path)
    if not target.is_file():
        raise RuntimeError("下载文件不存在，无法校验完整性")
    actual_size = target.stat().st_size
    if actual_size <= 0:
        raise RuntimeError("下载文件为空，已终止")
    if expected_size > 0 and actual_size != expected_size:
        raise RuntimeError(f"下载文件大小不符（期望 {expected_size} 字节，实际 {actual_size} 字节）")
    expected = normalize_sha256(expected_sha256)
    if not expected:
        return
    actual = sha256_file(target)
    if actual != expected:
        logger.error("下载文件哈希不符 path=%s expected=%s actual=%s", target, expected, actual)
        raise RuntimeError("下载文件 SHA256 校验失败，文件可能已损坏或被篡改")
    logger.info("下载文件 SHA256 校验通过 path=%s", target)


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
