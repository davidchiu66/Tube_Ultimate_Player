"""按站点探测「哪个浏览器登录过该站点」，供 Cookie 自动检测使用。

关键点：判断某浏览器是否登录过某站点**不需要解密 Cookie**。Chromium 的
`cookies` 表与 Firefox 的 `moz_cookies` 表里 host 与 name 列都是明文（只有 value
加密），因此只要把库文件复制到临时目录、只读打开、查一条 COUNT 就能判断，
全程不读也不解密 value 列。复制副本是为了绕开浏览器运行时对库文件的独占锁
（yt-dlp 也是这么做的），用完即删。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger("tube_player.cookie")

# 站点 -> (host 匹配后缀, 登录态标志 Cookie 名)。命中任意一个标志 Cookie 即认为已登录。
SITE_LOGIN_COOKIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bilibili": ((".bilibili.com", "bilibili.com"), ("SESSDATA", "bili_jct", "DedeUserID")),
    "youtube": (
        (".youtube.com", "youtube.com", ".google.com"),
        ("SID", "SAPISID", "__Secure-3PAPISID", "LOGIN_INFO"),
    ),
}


@dataclass(frozen=True)
class CookieDatabase:
    """一个待探测的浏览器 Cookie 库。"""

    browser_spec: str          # 传给 yt-dlp --cookies-from-browser 的值，如 chrome:Default
    path: Path                 # Cookie 库文件路径
    kind: str                  # "chromium" 或 "firefox"


def probe_site_cookie_browsers(
    sites: tuple[str, ...],
    databases: list[CookieDatabase],
) -> dict[str, str]:
    """为每个站点选出第一个登录过它的浏览器。

    返回 {site: browser_spec}；某站点一个浏览器都没登录时，该站点不出现在结果里。
    databases 的顺序即优先级（调用方按「默认浏览器优先」排好）。
    """
    result: dict[str, str] = {}
    for database in databases:
        remaining = [site for site in sites if site not in result]
        if not remaining:
            break
        try:
            logged_in = _sites_logged_in(database, remaining)
        except Exception:  # noqa: BLE001 - 单个库损坏/加锁不应中断整轮探测
            logger.warning("探测浏览器 Cookie 失败 spec=%s path=%s", database.browser_spec, database.path, exc_info=True)
            continue
        for site in logged_in:
            result.setdefault(site, database.browser_spec)
    return result


def _sites_logged_in(database: CookieDatabase, sites: list[str]) -> list[str]:
    if not database.path.is_file():
        return []
    with tempfile.TemporaryDirectory() as temp_dir:
        # 浏览器运行时对库文件加了独占锁，必须先复制副本再打开。
        copy_path = Path(temp_dir) / "cookies.db"
        try:
            shutil.copy2(database.path, copy_path)
        except OSError:
            logger.warning("复制 Cookie 库失败 path=%s", database.path)
            return []
        uri = f"file:{copy_path}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            return [site for site in sites if _has_login_cookie(conn, database.kind, site)]
        finally:
            conn.close()


def _has_login_cookie(conn: sqlite3.Connection, kind: str, site: str) -> bool:
    hosts, names = SITE_LOGIN_COOKIES.get(site, ((), ()))
    if not hosts or not names:
        return False
    if kind == "firefox":
        table, host_column, name_column = "moz_cookies", "host", "name"
    else:
        table, host_column, name_column = "cookies", "host_key", "name"
    # 只查 host 与 name，绝不触碰 value —— 探测不接触任何凭据内容。
    host_clause = " OR ".join(f"{host_column} LIKE ?" for _ in hosts)
    name_clause = ", ".join("?" for _ in names)
    query = f"SELECT COUNT(*) FROM {table} WHERE ({host_clause}) AND {name_column} IN ({name_clause})"
    params = [f"%{host}" for host in hosts] + list(names)
    try:
        row = conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and int(row[0]) > 0)


def discover_cookie_databases(sources: list[tuple[str, str]], *, home: Path, environ: dict[str, str], platform_name: str) -> list[CookieDatabase]:
    """把 detect_browser_cookie_sources 的 (label, spec) 列表映射成待探测的库文件。

    spec 形如 chrome:Default / firefox:<profile> / opera；这里据此定位实际的
    cookies 库文件（Chromium 新版在 Network/Cookies，旧版在 Cookies）。
    """
    databases: list[CookieDatabase] = []
    for _label, spec in sources:
        database = _database_for_spec(spec, home=home, environ=environ, platform_name=platform_name)
        if database is not None:
            databases.append(database)
    return databases


def _chromium_cookie_file(profile_dir: Path) -> Path | None:
    for candidate in (profile_dir / "Network" / "Cookies", profile_dir / "Cookies"):
        if candidate.is_file():
            return candidate
    return None


def _database_for_spec(spec: str, *, home: Path, environ: dict[str, str], platform_name: str) -> CookieDatabase | None:
    browser, _, profile = spec.partition(":")
    profile = profile or "Default"
    roots = _browser_user_data_roots(browser, home=home, environ=environ, platform_name=platform_name)
    if browser == "firefox":
        for root in roots:
            profile_dir = Path(profile) if Path(profile).is_absolute() else root / profile
            cookie_file = profile_dir / "cookies.sqlite"
            if cookie_file.is_file():
                return CookieDatabase(browser_spec=spec, path=cookie_file, kind="firefox")
        return None
    for root in roots:
        profile_dir = Path(profile) if Path(profile).is_absolute() else root / profile
        cookie_file = _chromium_cookie_file(profile_dir)
        if cookie_file is not None:
            return CookieDatabase(browser_spec=spec, path=cookie_file, kind="chromium")
    return None


def _browser_user_data_roots(browser: str, *, home: Path, environ: dict[str, str], platform_name: str) -> list[Path]:
    if platform_name.startswith("win"):
        local = Path(environ.get("LOCALAPPDATA", ""))
        app_data = Path(environ.get("APPDATA", ""))
        mapping = {
            "edge": [local / "Microsoft" / "Edge" / "User Data"],
            "chrome": [local / "Google" / "Chrome" / "User Data"],
            "brave": [local / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "chromium": [local / "Chromium" / "User Data"],
            "vivaldi": [local / "Vivaldi" / "User Data"],
            "opera": [app_data / "Opera Software" / "Opera Stable"],
            "firefox": [app_data / "Mozilla" / "Firefox" / "Profiles"],
        }
        return mapping.get(browser, [])
    config_home = Path(environ.get("XDG_CONFIG_HOME", "").strip() or home / ".config")
    mapping = {
        "chrome": [config_home / "google-chrome", home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome"],
        "chromium": [config_home / "chromium", home / "snap" / "chromium" / "common" / "chromium"],
        "brave": [config_home / "BraveSoftware" / "Brave-Browser"],
        "vivaldi": [config_home / "vivaldi"],
        "firefox": [home / ".mozilla" / "firefox"],
    }
    return mapping.get(browser, [])
