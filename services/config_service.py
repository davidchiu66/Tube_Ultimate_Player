from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app_paths import (
    CONFIG_DIR,
    DEFAULT_CONFIG_DIR,
    DOWNLOAD_DIR,
    RUNTIME_ROOT,
    default_config_path,
    runtime_path,
    thirdpart_path,
)
from services.shortcut_service import DEFAULT_SHORTCUTS


DEFAULT_CONFIG_PATH = default_config_path("default_config.json")
USER_CONFIG_PATH = CONFIG_DIR / "user_config.json"

PROXY_MODE_AUTO = "auto"
PROXY_MODE_MANUAL = "manual"
PROXY_MODE_OFF = "off"
PROXY_MODES = (PROXY_MODE_AUTO, PROXY_MODE_MANUAL, PROXY_MODE_OFF)
PROXY_MODE_LABELS = {
    PROXY_MODE_AUTO: "自动（优先使用已配置代理，未配置时跟随系统）",
    PROXY_MODE_MANUAL: "仅使用下方配置的代理",
    PROXY_MODE_OFF: "强制直连（忽略系统代理）",
}

# 「播放 URL」面板保留的历史条数上限。
RECENT_URL_LIMIT = 20
RECENT_URL_KEY = "player.recent_urls"


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

    def recent_urls(self) -> list[dict[str, str]]:
        """「播放 URL」面板的历史，最新在前。忽略缺 url 的脏条目。"""
        raw = self.get(RECENT_URL_KEY, [])
        if not isinstance(raw, list):
            return []
        result: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            result.append(
                {
                    "url": url,
                    "title": str(item.get("title") or "").strip(),
                    "played_at": str(item.get("played_at") or "").strip(),
                }
            )
        return result

    def add_recent_url(self, url: str, title: str = "", played_at: str = "") -> None:
        """记录一条播放历史。按归一化 URL 去重，最新提到最前，超限淘汰最旧。"""
        clean_url = str(url or "").strip()
        if not clean_url:
            return
        key = _normalize_recent_url(clean_url)
        entries = [item for item in self.recent_urls() if _normalize_recent_url(item["url"]) != key]
        entries.insert(0, {"url": clean_url, "title": str(title or "").strip(), "played_at": played_at})
        self.set(RECENT_URL_KEY, entries[:RECENT_URL_LIMIT])

    def update_recent_url_title(self, url: str, title: str) -> None:
        """解析成功后回填标题；条目已被淘汰或标题为空时静默跳过。"""
        clean_title = str(title or "").strip()
        if not clean_title:
            return
        key = _normalize_recent_url(url)
        entries = self.recent_urls()
        changed = False
        for item in entries:
            if _normalize_recent_url(item["url"]) == key:
                item["title"] = clean_title
                changed = True
                break
        if changed:
            self.set(RECENT_URL_KEY, entries)

    def remove_recent_url(self, url: str) -> None:
        key = _normalize_recent_url(url)
        entries = [item for item in self.recent_urls() if _normalize_recent_url(item["url"]) != key]
        self.set(RECENT_URL_KEY, entries)

    def clear_recent_urls(self) -> None:
        self.set(RECENT_URL_KEY, [])

    def proxy_mode(self) -> str:
        """代理模式：auto 自动、manual 仅用配置代理、off 强制直连。"""
        value = str(self.get("network.proxy_mode", PROXY_MODE_AUTO) or PROXY_MODE_AUTO).strip().lower()
        if value not in PROXY_MODES:
            return PROXY_MODE_AUTO
        return value

    def configured_proxy(self) -> str:
        return normalize_proxy(str(self.get("youtube.proxy", "") or "").strip())

    def effective_proxy(self) -> tuple[str, str]:
        """返回 (来源描述, 代理地址)。

        用户在设置里填写的代理优先于系统代理：显式配置代表明确意图，
        若被系统代理静默覆盖，用户会看到"已配置代理"却走了另一条链路。
        """
        mode = self.proxy_mode()
        if mode == PROXY_MODE_OFF:
            return "强制直连", ""

        configured = self.configured_proxy()
        if configured:
            return "配置代理", configured

        if mode == PROXY_MODE_MANUAL:
            return "未配置代理（手动模式）", ""

        system_proxy = detect_system_proxy()
        if system_proxy:
            return "系统代理", system_proxy

        return "未使用代理", ""

    def cookie_file(self, site: str = "") -> str:
        normalized_site = self._normalize_cookie_site(site)
        value = str(self.get(f"cookies.{normalized_site}.file", "") or "").strip()
        if not value and normalized_site == self.default_home_source():
            # Before site-specific Cookie files were introduced, the single
            # legacy file followed the selected home site in practice.
            value = str(self.get("youtube.cookie_file", "") or "").strip()
            if not value:
                legacy_default = runtime_path("cookie.txt")
                if legacy_default.exists():
                    value = str(legacy_default)
        if not value:
            return ""

        path = Path(value)
        if not path.is_absolute():
            path = RUNTIME_ROOT / path
        # 空文件（用户清空过 Cookie 输入框、或从未填写但文件已被创建）视为"未配置"。
        # 否则它会挡在浏览器 Cookie 前面，并让 prepare_cookie_file 抛格式错误，
        # 把「本来可以退回浏览器 Cookie」变成整个请求失败。
        if not cookie_file_has_content(path):
            return ""
        return str(path)

    def cookie_file_for_url(self, target_url: str) -> str:
        return self.cookie_file(self.cookie_site_for_url(target_url))

    def cookie_file_path(self, site: str = "") -> str:
        """该站点 Cookie 文件的落盘位置，**不检查内容**，且保证非空。

        与 cookie_file() 分工不同：后者回答「这个文件能不能拿给 yt-dlp 用」，空文件
        返回空串；本方法回答「该往哪里写」。写入路径绝不能为空 —— Path("") 会解析成
        当前目录，写它会得到 PermissionError: '.'。
        """
        normalized_site = self._normalize_cookie_site(site)
        value = str(self.get(f"cookies.{normalized_site}.file", "") or "").strip()
        if not value and normalized_site == self.default_home_source():
            value = str(self.get("youtube.cookie_file", "") or "").strip()
        if not value:
            return self.default_cookie_file(normalized_site)
        path = Path(value)
        if not path.is_absolute():
            path = RUNTIME_ROOT / path
        return str(path)

    def default_cookie_file(self, site: str = "") -> str:
        normalized_site = self._normalize_cookie_site(site)
        if sys.platform.startswith("linux"):
            return str(CONFIG_DIR / f"cookie_{normalized_site}.txt")
        return str(runtime_path(f"cookie_{normalized_site}.txt"))

    def cookie_site_for_url(self, target_url: str) -> str:
        raw = str(target_url or "").strip()
        host = (urlparse(raw).hostname or "").lower()
        if host.endswith("bilibili.com") or host.endswith("b23.tv"):
            return "bilibili"
        return "youtube"

    def _normalize_cookie_site(self, site: str) -> str:
        normalized = str(site or "").strip().lower()
        return normalized if normalized in {"youtube", "bilibili"} else self.default_home_source()

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

    def dlna_media_server_port(self) -> int:
        try:
            value = int(self.get("dlna.media_server_port", 8899) or 8899)
        except (TypeError, ValueError):
            value = 8899
        return max(1, min(65535, value))

    def default_home_source(self) -> str:
        value = str(self.get("content.default_home", "bilibili") or "bilibili").strip().lower()
        return value if value in {"youtube", "bilibili"} else "bilibili"

    def default_home_label(self) -> str:
        return "Bilibili" if self.default_home_source() == "bilibili" else "YouTube"

    def shortcut_sequence(self, action: str) -> str:
        default = DEFAULT_SHORTCUTS.get(action, "")
        return str(self.get(f"shortcuts.{action}", default) or "").strip()

    def shortcut_sequences(self) -> dict[str, str]:
        return {action: self.shortcut_sequence(action) for action in DEFAULT_SHORTCUTS}

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

    def auto_cookie_browser_for_site(self, site: str) -> str:
        """自动模式下该站点应使用的浏览器 Cookie 源。

        优先用启动探测出的「登录过该站点」的浏览器；没有探测结果时回退到
        探测到的第一个浏览器（旧行为），保证功能不因探测失败而完全不可用。
        """
        browser = str(self.get("youtube.cookie_browser", "") or "").strip()
        if browser != "auto":
            return ""
        normalized_site = self._normalize_cookie_site(site)
        probed = str(self.get(f"cookies.{normalized_site}.auto_browser", "") or "").strip()
        if probed:
            return probed
        return detect_browser_cookie_source()

    def set_probed_cookie_browsers(self, mapping: dict[str, str]) -> None:
        """保存启动探测结果：{site: browser_spec}。未命中的站点清空其记录。"""
        for site in ("bilibili", "youtube"):
            spec = str(mapping.get(site, "") or "").strip()
            self.set(f"cookies.{site}.auto_browser", spec)

    def cookie_auto_probe_enabled(self) -> bool:
        return str(self.get("youtube.cookie_browser", "") or "").strip() == "auto"

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


def cookie_file_has_content(path: Path) -> bool:
    """Cookie 文件里是否有真正可用的内容（至少一行非注释非空行）。

    只读到第一行数据就返回，不整文件读入。仅有 Netscape 头注释的文件同样算空 ——
    那种文件交给 yt-dlp 也带不上任何 Cookie。
    """
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return True
    except OSError:
        return False
    return False


def _normalize_recent_url(url: str) -> str:


    """去重用的归一化键：去掉首尾空白、去掉末尾斜杠、忽略大小写。"""
    return str(url or "").strip().rstrip("/").casefold()


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


def detect_browser_cookie_sources(
    platform_name: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    platform_value = platform_name or sys.platform
    env = environ if environ is not None else os.environ
    if platform_value.startswith("win"):
        local_app_data = Path(env.get("LOCALAPPDATA", ""))
        app_data = Path(env.get("APPDATA", ""))
        default_browser = _detect_default_windows_browser()
        # 便携版默认浏览器排在最前：它的库不在标准位置，若不显式列出，用户选到的
        # 会是同内核那个几乎没用过的安装版（yt-dlp 也会去读那个空 profile）。
        sources: list[tuple[str, str, str]] = list(detect_portable_default_browser_sources())
        portable_count = len(sources)
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

        portable_values = {value for _browser, _label, value in sources[:portable_count]}
        deduped: list[tuple[str, str]] = []
        seen: set[str] = set()
        for browser, label, value in sources:
            if value in seen:
                continue
            seen.add(value)
            # 已经定位到便携版默认浏览器时，同内核的安装版就不该再挂「默认浏览器」——
            # 那台机器上真正在用的是便携版。
            if value in portable_values:
                is_default = True
            elif portable_values:
                is_default = False
            else:
                is_default = browser == default_browser
            deduped.append((f"默认浏览器 - {label}" if is_default else label, value))

        deduped.sort(key=lambda item: (0 if item[0].startswith("默认浏览器") else 1, item[0].lower()))
        return deduped

    if platform_value.startswith("linux"):
        return _detect_linux_browser_cookie_sources(home or Path.home(), env)

    return []


def _detect_linux_browser_cookie_sources(home: Path, environ: dict[str, str]) -> list[tuple[str, str]]:
    config_home = Path(environ.get("XDG_CONFIG_HOME", "").strip() or home / ".config")
    default_browser = _browser_name_from_command(environ.get("BROWSER", ""))
    sources: list[tuple[str, str, str]] = []

    chromium_roots = (
        ("chrome", "Google Chrome", config_home / "google-chrome", True),
        ("chromium", "Chromium", config_home / "chromium", True),
        ("brave", "Brave", config_home / "BraveSoftware" / "Brave-Browser", True),
        ("vivaldi", "Vivaldi", config_home / "vivaldi", True),
        ("chromium", "Chromium (Snap)", home / "snap" / "chromium" / "common" / "chromium", False),
        (
            "chrome",
            "Google Chrome (Flatpak)",
            home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome",
            False,
        ),
        (
            "chromium",
            "Chromium (Flatpak)",
            home / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium",
            False,
        ),
        (
            "brave",
            "Brave (Flatpak)",
            home / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser",
            False,
        ),
    )
    for browser, label, user_data, use_profile_name in chromium_roots:
        for profile in _chromium_cookie_profiles(user_data):
            profile_value = profile if use_profile_name else str((user_data / profile).resolve())
            sources.append((browser, f"{label} ({profile})", f"{browser}:{profile_value}"))

    firefox_roots = (
        ("Firefox", home / ".mozilla" / "firefox", True),
        ("Firefox (Snap)", home / "snap" / "firefox" / "common" / ".mozilla" / "firefox", False),
        ("Firefox (Flatpak)", home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox", False),
    )
    for label, profiles_root, use_profile_name in firefox_roots:
        if not profiles_root.exists():
            continue
        try:
            profile_dirs = list(profiles_root.iterdir())
        except OSError:
            continue
        for profile_dir in profile_dirs:
            if not (profile_dir / "cookies.sqlite").is_file():
                continue
            profile_value = profile_dir.name if use_profile_name else str(profile_dir.resolve())
            sources.append(("firefox", f"{label} ({profile_dir.name})", f"firefox:{profile_value}"))

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


def _browser_name_from_command(command: str) -> str:
    executable = Path(str(command or "").strip().split()[0]).name.lower() if str(command or "").strip() else ""
    mappings = (
        ("google-chrome", "chrome"),
        ("chrome", "chrome"),
        ("chromium", "chromium"),
        ("brave", "brave"),
        ("firefox", "firefox"),
        ("vivaldi", "vivaldi"),
    )
    for needle, browser in mappings:
        if needle in executable:
            return browser
    return ""


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


def _default_windows_browser_prog_id() -> str:
    """https 关联的 ProgId，取不到返回空串。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            return str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
    except (OSError, ImportError):
        # 非 Windows 上 winreg 根本不存在，按「取不到」处理。
        return ""


def default_windows_browser_command() -> str:
    """默认浏览器的启动命令行（HKCR\\<ProgId>\\shell\\open\\command），取不到返回空串。

    便携版浏览器可以注册成系统默认浏览器，但不会把 Cookie 库放在
    %LOCALAPPDATA% 的标准位置。要找到它只能顺着这条注册项拿到真实 exe 路径。
    """
    prog_id = _default_windows_browser_prog_id()
    if not prog_id:
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            return str(winreg.QueryValueEx(key, "")[0]).strip()
    except (OSError, ImportError):
        return ""


def _executable_from_command(command: str) -> Path | None:
    """从注册表命令行里剥出 exe 路径。带引号的整段取引号内，否则取第一个 .exe 结尾处。"""
    text = str(command or "").strip()
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        candidate = text[1:end] if end > 1 else text[1:]
    else:
        lowered = text.lower()
        marker = lowered.find(".exe")
        candidate = text[: marker + 4] if marker >= 0 else text.split()[0]
    candidate = candidate.strip()
    if not candidate:
        return None
    path = Path(candidate)
    return path if path.is_file() else None


def _browser_kind_from_executable(executable: Path | None) -> str:
    """由 exe 文件名判定浏览器内核标识，认不出返回空串。"""
    if executable is None:
        return ""
    name = executable.name.lower()
    # msedge 要排在 edge 前面；brave/vivaldi 的 exe 名就是自身。
    mappings = (
        ("msedge", "edge"),
        ("edge", "edge"),
        ("brave", "brave"),
        ("vivaldi", "vivaldi"),
        ("opera", "opera"),
        ("firefox", "firefox"),
        ("librewolf", "firefox"),
        ("chromium", "chromium"),
        ("chrome", "chrome"),
    )
    for needle, browser in mappings:
        if needle in name:
            return browser
    return ""


# 便携版浏览器常见的 profile 目录布局（相对 exe 所在目录）。PortableApps 打包的
# 结构是 <App>\App\Chrome-bin\chrome.exe 配 <App>\Data\profile，所以要往上找两级。
_PORTABLE_CHROMIUM_LAYOUTS = (
    ("User Data",),
    ("..", "User Data"),
    ("Data", "profile"),
    ("..", "Data", "profile"),
    ("..", "..", "Data", "profile"),
)
_PORTABLE_FIREFOX_LAYOUTS = (
    ("Data", "profile"),
    ("..", "Data", "profile"),
    ("..", "..", "Data", "profile"),
)


def detect_portable_default_browser_sources() -> list[tuple[str, str, str]]:
    """默认浏览器是便携版时，顺着注册表里的 exe 路径找出它的 Cookie 库。

    返回 [(browser, label, value)]，value 用**绝对路径**而不是 profile 名——
    便携版的库不在 %LOCALAPPDATA% 下，只有绝对路径能让 yt-dlp 和自带的解密
    子进程找对地方。找不到就返回空列表，调用方继续用标准位置的候选。
    """
    executable = _executable_from_command(default_windows_browser_command())
    browser = _browser_kind_from_executable(executable)
    if executable is None or not browser:
        return []

    exe_dir = executable.parent
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    if browser == "firefox":
        for parts in _PORTABLE_FIREFOX_LAYOUTS:
            root = _resolve_relative(exe_dir, parts)
            if root is None:
                continue
            # Data\profile 本身就是 profile 目录；也兼容它下面再套一层 profiles 的情况。
            for candidate in (root, *_iter_subdirs(root)):
                if not (candidate / "cookies.sqlite").is_file():
                    continue
                value = f"firefox:{candidate}"
                if value in seen:
                    continue
                seen.add(value)
                found.append(("firefox", f"Firefox 便携版 ({candidate.name})", value))
        return found

    for parts in _PORTABLE_CHROMIUM_LAYOUTS:
        user_data = _resolve_relative(exe_dir, parts)
        if user_data is None:
            continue
        for profile in _chromium_cookie_profiles(user_data):
            value = f"{browser}:{user_data / profile}"
            if value in seen:
                continue
            seen.add(value)
            found.append((browser, f"{browser.capitalize()} 便携版 ({profile})", value))
    return found


def _resolve_relative(base: Path, parts: tuple[str, ...]) -> Path | None:
    try:
        resolved = base.joinpath(*parts).resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _iter_subdirs(root: Path) -> list[Path]:
    try:
        return [child for child in root.iterdir() if child.is_dir()]
    except OSError:
        return []


def _detect_default_windows_browser() -> str:
    prog_id = _default_windows_browser_prog_id()
    if not prog_id:
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
    bundled_deno = thirdpart_path("deno.exe" if sys.platform.startswith("win") else "deno")
    if bundled_deno.is_file():
        return f"deno:{bundled_deno}"
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
