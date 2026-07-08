from __future__ import annotations

import json
import logging
import re
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
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO_SLUG}/releases/latest"


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
        payload = self._read_json(LATEST_RELEASE_API)
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

    def download_target_path(self, asset: ReleaseAsset) -> Path:
        filename = asset.name or "update_package.bin"
        return self.updates_dir() / filename

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
