"""Firefox profile 的发现与 Cookie 库复制，供 Cookie 探测/读取/浏览器列表共用。

三处都需要「Firefox 的 profile 在哪、哪个是正在用的那个」：`config_service`
列浏览器源、`cookie_probe_service` 探测登录状态、`cookie_service` 直接读
Cookie。原先三处各写一遍且都只扫 `%APPDATA%\\Mozilla\\Firefox\\Profiles`，
于是 Microsoft Store 版 Firefox 完全看不见，多 profile 时顺序还随 `iterdir()`
波动 —— 明明登录了却读不出来多半来自这里。

两个要点：
- **profiles.ini 才知道哪个 profile 在用。** `[Install*]` 段的 `Default=` 指向
  该安装最近使用的 profile，`[Profile*]` 段的 `Default=1` 是传统标记。目录名
  形如 `xxxxxxxx.default-release`，光看名字猜不出来。
- **Cookie 库是 WAL 模式。** 只复制主库（或用 `immutable=1` 打开）会读到不含
  最新写入的旧快照 —— Firefox 正在运行时，刚登录的 Cookie 往往还在 `-wal` 里。
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger("tube_player.cookie")

COOKIE_DB_NAME = "cookies.sqlite"
# SQLite 的边车文件：WAL 日志与共享内存索引，必须与主库一起复制。
_SQLITE_SIDECARS = ("-wal", "-shm")


@dataclass(frozen=True)
class FirefoxProfile:
    """一个 profile 目录。`is_default` 表示 profiles.ini 认为它正在被使用。"""

    path: Path
    name: str
    is_default: bool = False

    @property
    def cookie_db(self) -> Path:
        return self.path / COOKIE_DB_NAME

    @property
    def has_cookies(self) -> bool:
        return self.cookie_db.is_file()


def firefox_install_roots(
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
) -> list[Path]:
    """Firefox 的安装数据根目录（内含 profiles.ini），按可信度排序。"""
    env = environ if environ is not None else os.environ
    platform_value = platform_name or sys.platform
    if platform_value.startswith("win"):
        roots = [Path(env.get("APPDATA", "")) / "Mozilla" / "Firefox"]
        roots.extend(_ms_store_firefox_roots(env))
        return [root for root in roots if root.parts]
    user_home = home or Path.home()
    return [
        user_home / ".mozilla" / "firefox",
        user_home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        user_home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
    ]


def _ms_store_firefox_roots(environ: dict[str, str]) -> list[Path]:
    """Microsoft Store 版 Firefox：数据被重定向到 Packages 下的 LocalCache。"""
    local = Path(environ.get("LOCALAPPDATA", ""))
    if not local.parts:
        return []
    packages = local / "Packages"
    try:
        entries = sorted(packages.glob("Mozilla.Firefox_*"))
    except OSError:
        return []
    return [entry / "LocalCache" / "Roaming" / "Mozilla" / "Firefox" for entry in entries]


def firefox_profiles(
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
    require_cookies: bool = True,
) -> list[FirefoxProfile]:
    """所有 profile，正在使用的排在前面。

    `require_cookies` 为真时只返回已有 Cookie 库的 profile —— 列给用户选的时候，
    一个从未启动过的 profile 选了也读不出东西。
    """
    profiles: list[FirefoxProfile] = []
    seen: set[Path] = set()
    for root in firefox_install_roots(environ=environ, home=home, platform_name=platform_name):
        for profile in _profiles_in_root(root):
            resolved = _normalized(profile.path)
            if resolved in seen:
                continue
            seen.add(resolved)
            if require_cookies and not profile.has_cookies:
                continue
            profiles.append(profile)
    # 默认 profile 优先，其余保持发现顺序（sorted 稳定）。
    return sorted(profiles, key=lambda item: 0 if item.is_default else 1)


def _profiles_in_root(root: Path) -> list[FirefoxProfile]:
    defaults = _default_profile_paths(root)
    found: list[FirefoxProfile] = []
    seen: set[Path] = set()
    for parent in (root / "Profiles", root):
        if not parent.is_dir():
            continue
        try:
            candidates = sorted(parent.iterdir())
        except OSError:
            continue
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            resolved = _normalized(candidate)
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(
                FirefoxProfile(
                    path=candidate,
                    name=candidate.name,
                    is_default=resolved in defaults,
                )
            )
    return found


def _default_profile_paths(root: Path) -> set[Path]:
    """profiles.ini 里被标记为「正在使用」的 profile 目录。

    `[Install*]` 的 `Default=` 比 `[Profile*]` 的 `Default=1` 更贴近现实：后者是
    传统标记，装过多个版本（release/ESR）后常常已经不是实际在用的那个。
    """
    parser = _read_profiles_ini(root)
    if parser is None:
        return set()
    install_defaults: set[Path] = set()
    profile_defaults: set[Path] = set()
    for section in parser.sections():
        lowered = section.lower()
        if lowered.startswith("install"):
            path = _profile_path_from_ini(root, parser[section].get("Default", ""), parser[section])
            if path is not None:
                install_defaults.add(path)
        elif lowered.startswith("profile"):
            flag = str(parser[section].get("Default", "") or "").strip()
            if flag == "1":
                path = _profile_path_from_ini(root, parser[section].get("Path", ""), parser[section])
                if path is not None:
                    profile_defaults.add(path)
    return install_defaults or profile_defaults


def _read_profiles_ini(root: Path) -> configparser.ConfigParser | None:
    path = root / "profiles.ini"
    if not path.is_file():
        return None
    # profiles.ini 的键名大小写混用（Path/IsRelative/Default），不能被规范化成小写。
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error) as exc:
        logger.debug("读取 profiles.ini 失败 path=%s detail=%s", path, exc)
        return None
    return parser


def _profile_path_from_ini(root: Path, raw: str, section) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    # IsRelative 缺省按相对处理：profiles.ini 里的 Path 写的是 "Profiles/xxx" 这种
    # 正斜杠形式，Windows 上也能被 Path 正确拆分。
    relative = str(section.get("IsRelative", "1") or "1").strip() != "0"
    candidate = Path(value)
    if relative or not candidate.is_absolute():
        candidate = root / value
    return _normalized(candidate)


def _normalized(path: Path) -> Path:
    """用于比较的规范形式；Windows 上大小写不敏感，解析失败则退回原值。"""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return Path(str(resolved).lower()) if os.name == "nt" else resolved


def resolve_firefox_profile_dir(
    profile: str,
    *,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    platform_name: str | None = None,
) -> Path | None:
    """把 `firefox:<profile>` 里的 profile 解析成实际目录。

    profile 可以是绝对路径（便携版/Snap 用），也可以是目录名；为空时取默认
    profile。找不到 Cookie 库一律返回 None，让调用方去试下一个 Cookie 源。
    """
    value = str(profile or "").strip()
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate if (candidate / COOKIE_DB_NAME).is_file() else None
    profiles = firefox_profiles(environ=environ, home=home, platform_name=platform_name)
    if not value:
        return profiles[0].path if profiles else None
    lowered = value.lower()
    for item in profiles:
        if item.name.lower() == lowered:
            return item.path
    return None


def copy_sqlite_database(source: Path, target: Path) -> None:
    """复制 SQLite 库，连 `-wal`/`-shm` 一起。

    只复制主库会丢掉尚未 checkpoint 的写入 —— 对 Cookie 库来说就是「刚登录的
    账号看起来没登录」。边车文件缺失属正常（已 checkpoint 过），忽略即可。
    """
    shutil.copy2(source, target)
    for suffix in _SQLITE_SIDECARS:
        sidecar = source.with_name(source.name + suffix)
        if not sidecar.is_file():
            continue
        try:
            shutil.copy2(sidecar, target.with_name(target.name + suffix))
        except OSError as exc:
            # 主库已经到手，边车复制失败只是可能读到旧快照，不该让整轮读取失败。
            logger.debug("复制 SQLite 边车文件失败 path=%s detail=%s", sidecar, exc)
