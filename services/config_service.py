from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from app_paths import CONFIG_DIR, DEFAULT_CONFIG_DIR, DOWNLOAD_DIR, RUNTIME_ROOT, default_config_path, runtime_path


DEFAULT_CONFIG_PATH = default_config_path("default_config.json")
USER_CONFIG_PATH = CONFIG_DIR / "user_config.json"


class ConfigService:
    def __init__(
        self,
        default_path: Path = DEFAULT_CONFIG_PATH,
        user_path: Path = USER_CONFIG_PATH,
    ) -> None:
        self.default_path = default_path
        self.user_path = user_path
        self._config: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        defaults = self._read_json(self.default_path)
        user = self._read_json(self.user_path)
        self._config = self._merge(defaults, user)

    def save(self) -> None:
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_path.write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._config
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        node = self._config
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def all(self) -> dict[str, Any]:
        return copy.deepcopy(self._config)

    def effective_proxy(self) -> tuple[str, str]:
        system_proxy = detect_system_proxy()
        if system_proxy:
            return "系统代理", system_proxy

        configured = str(self.get("youtube.proxy", "") or "").strip()
        if configured:
            return "配置代理", normalize_proxy(configured)

        return "未使用代理", ""

    def cookie_file(self) -> str:
        value = str(self.get("youtube.cookie_file", "") or "").strip()
        if not value:
            return ""

        path = Path(value)
        if not path.is_absolute():
            path = RUNTIME_ROOT / path
        return str(path)

    def default_cookie_file(self) -> str:
        return str(runtime_path("cookie.txt"))

    def download_dir(self) -> str:
        value = str(self.get("download.save_dir", str(DOWNLOAD_DIR)) or str(DOWNLOAD_DIR)).strip()
        path = Path(value)
        if not path.is_absolute():
            path = RUNTIME_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def download_max_concurrent(self) -> int:
        try:
            value = int(self.get("download.max_concurrent", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, min(10, value))

    def download_ffmpeg_location(self) -> str:
        value = str(self.get("download.ffmpeg_dir", "") or "").strip()
        if not value:
            return ""
        path = Path(value)
        if not path.is_absolute():
            path = RUNTIME_ROOT / path
        return str(path)

    def cookie_browser(self) -> str:
        browser = str(self.get("youtube.cookie_browser", "") or "").strip()
        profile = str(self.get("youtube.cookie_browser_profile", "") or "").strip()
        if not browser:
            return ""
        if browser == "auto":
            return detect_browser_cookie_source()
        if ":" in browser:
            return browser
        return f"{browser}:{profile}" if profile else browser

    def explicit_cookie_browser(self) -> str:
        browser = str(self.get("youtube.cookie_browser", "") or "").strip()
        profile = str(self.get("youtube.cookie_browser_profile", "") or "").strip()
        if not browser or browser == "auto":
            return ""
        if ":" in browser:
            return browser
        return f"{browser}:{profile}" if profile else browser

    def auto_cookie_browser(self) -> str:
        browser = str(self.get("youtube.cookie_browser", "") or "").strip()
        if browser != "auto":
            return ""
        return detect_browser_cookie_source()

    def js_runtime(self) -> str:
        runtime = str(self.get("youtube.js_runtime", "auto") or "").strip()
        if not runtime:
            return ""
        if runtime == "auto":
            return detect_js_runtime()
        if ":" in runtime:
            return runtime
        path = shutil.which(runtime)
        return f"{runtime}:{path}" if path else runtime

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @classmethod
    def _merge(cls, defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(defaults)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged


def normalize_proxy(proxy: str) -> str:
    proxy = proxy.strip()
    if not proxy:
        return ""
    if "://" in proxy:
        return proxy
    if proxy.lower().startswith("socks"):
        return proxy
    return f"http://{proxy}"


def detect_system_proxy() -> str:
    if sys.platform.startswith("win"):
        proxy = _detect_windows_proxy()
        if proxy:
            return proxy

    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return normalize_proxy(value)

    return ""


def detect_browser_cookie_source() -> str:
    sources = detect_browser_cookie_sources()
    return str(sources[0][1]) if sources else ""


def detect_browser_cookie_sources() -> list[tuple[str, str]]:
    if sys.platform.startswith("win"):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        app_data = Path(os.environ.get("APPDATA", ""))
        default_browser = _detect_default_windows_browser()
        sources: list[tuple[str, str, str]] = []
        chromium_candidates = (
            ("edge", "Microsoft Edge", local_app_data / "Microsoft" / "Edge" / "User Data"),
            ("chrome", "Google Chrome", local_app_data / "Google" / "Chrome" / "User Data"),
            ("brave", "Brave", local_app_data / "BraveSoftware" / "Brave-Browser" / "User Data"),
            ("chromium", "Chromium", local_app_data / "Chromium" / "User Data"),
            ("vivaldi", "Vivaldi", local_app_data / "Vivaldi" / "User Data"),
        )
        for browser, label, user_data in chromium_candidates:
            for profile in _chromium_cookie_profiles(user_data):
                sources.append((browser, f"{label} ({profile})", f"{browser}:{profile}"))

        opera_root = app_data / "Opera Software" / "Opera Stable"
        if (opera_root / "Network" / "Cookies").exists() or (opera_root / "Cookies").exists():
            sources.append(("opera", "Opera", "opera"))

        firefox_profiles = app_data / "Mozilla" / "Firefox" / "Profiles"
        if firefox_profiles.exists():
            for profile_dir in firefox_profiles.iterdir():
                if (profile_dir / "cookies.sqlite").exists():
                    sources.append(("firefox", f"Firefox ({profile_dir.name})", f"firefox:{profile_dir.name}"))

        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for browser, label, value in sources:
            if value in seen:
                continue
            seen.add(value)
            prefix = "默认浏览器 - " if browser == default_browser else ""
            deduped.append((f"{prefix}{label}", value))

        deduped.sort(key=lambda item: (0 if item[0].startswith("默认浏览器") else 1, item[0].lower()))
        return deduped

    return []


def _chromium_cookie_profiles(user_data: Path) -> list[str]:
    if not user_data.exists():
        return []
    profiles: list[str] = []
    common = ["Default", *[f"Profile {index}" for index in range(1, 10)]]
    for profile in common:
        profile_dir = user_data / profile
        if (profile_dir / "Network" / "Cookies").exists() or (profile_dir / "Cookies").exists():
            profiles.append(profile)
    try:
        for profile_dir in user_data.iterdir():
            if not profile_dir.is_dir() or profile_dir.name in profiles:
                continue
            if (profile_dir / "Network" / "Cookies").exists() or (profile_dir / "Cookies").exists():
                profiles.append(profile_dir.name)
    except OSError:
        pass
    return profiles


def _detect_default_windows_browser() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
    except OSError:
        return ""

    mappings = (
        ("microsoftedge", "edge"),
        ("msedge", "edge"),
        ("ie.http", "edge"),
        ("ie.https", "edge"),
        ("chrome", "chrome"),
        ("brave", "brave"),
        ("firefox", "firefox"),
        ("opera", "opera"),
        ("vivaldi", "vivaldi"),
        ("chromium", "chromium"),
    )
    for needle, browser in mappings:
        if needle in prog_id:
            return browser
    return ""


def detect_js_runtime() -> str:
    for runtime in ("deno", "node", "quickjs", "qjs", "bun"):
        path = shutil.which(runtime)
        if not path:
            continue
        name = "quickjs" if runtime == "qjs" else runtime
        return f"{name}:{path}"
    return ""


def _detect_windows_proxy() -> str:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return ""
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0]).strip()
    except OSError:
        return ""

    if not raw:
        return ""

    parts: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" in item:
            name, value = item.split("=", 1)
            parts[name.lower().strip()] = value.strip()

    selected = parts.get("https") or parts.get("http") or parts.get("socks") or raw
    if parts.get("socks") == selected and "://" not in selected:
        return f"socks5://{selected}"
    return normalize_proxy(selected)
