from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from app_paths import UPDATE_DIR
from services.config_service import ConfigService, detect_js_runtime
from services.update_service import UpdateService


logger = logging.getLogger("tube_player.runtime")

NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_WEBSITE_URL = "https://nodejs.org/zh-cn/download"


@dataclass(slots=True)
class RuntimeStatus:
    available: bool
    runtime: str
    display_text: str


@dataclass(slots=True)
class NodeInstallerInfo:
    version: str
    url: str
    filename: str


class RuntimeInstallService:
    def __init__(self, config: ConfigService) -> None:
        self.config = config
        self.update_service = UpdateService(config)

    def detect_runtime_status(self) -> RuntimeStatus:
        runtime = detect_js_runtime()
        if runtime:
            name, _, path = runtime.partition(":")
            label = name if not path else f"{name} ({path})"
            return RuntimeStatus(True, runtime, f"已检测到 JS Runtime：{label}")
        return RuntimeStatus(False, "", "未检测到 JS Runtime，建议安装 Node.js LTS。")

    def fetch_node_installer_info(self) -> NodeInstallerInfo:
        payload = self._read_json(NODE_INDEX_URL)
        if not isinstance(payload, list):
            raise RuntimeError("Node.js 版本清单格式无效")

        selected = None
        for item in payload:
            files = set(item.get("files", []) or [])
            if item.get("lts") and "win-x64-msi" in files:
                selected = item
                break
        if not selected:
            for item in payload:
                files = set(item.get("files", []) or [])
                if "win-x64-msi" in files:
                    selected = item
                    break
        if not selected:
            raise RuntimeError("没有找到适用于 Windows x64 的 Node.js 安装包")

        version = str(selected.get("version", "")).strip()
        filename = f"node-{version}-x64.msi"
        return NodeInstallerInfo(
            version=version,
            url=f"https://nodejs.org/dist/{version}/{filename}",
            filename=filename,
        )

    def installer_target_path(self, info: NodeInstallerInfo) -> Path:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        return UPDATE_DIR / info.filename

    def launch_installer(self, path: str | Path) -> None:
        target = Path(path)
        if not target.exists():
            raise RuntimeError("安装包文件不存在")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as exc:
            logger.exception("failed to launch node installer path=%s", target)
            raise RuntimeError(f"无法启动安装程序：{exc}") from exc

    def open_official_site(self) -> None:
        webbrowser.open(NODE_WEBSITE_URL)

    def _read_json(self, url: str):
        try:
            with self.update_service.open_url(url) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.exception("node index http error url=%s code=%s", url, exc.code)
            raise RuntimeError(f"访问 Node.js 下载源失败，HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            logger.exception("node index network error url=%s", url)
            raise RuntimeError(f"访问 Node.js 下载源失败：{exc.reason}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("node index parse error url=%s", url)
            raise RuntimeError("Node.js 下载源返回内容无法解析") from exc
