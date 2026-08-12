"""按站点探测「哪个浏览器登录过该站点」，供 Cookie 自动检测使用。

关键点：判断某浏览器是否登录过某站点**不需要解密 Cookie**。Chromium 的
`cookies` 表与 Firefox 的 `moz_cookies` 表里 host 与 name 列都是明文（只有 value
加密），因此只要把库文件复制到临时目录、只读打开、查一条 COUNT 就能判断，
全程不读也不解密 value 列。复制副本是为了绕开浏览器运行时对库文件的独占锁
（yt-dlp 也是这么做的），用完即删。
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from services.firefox_profiles import (
    COOKIE_DB_NAME,
    copy_sqlite_database,
    firefox_install_roots,
)


logger = logging.getLogger("tube_player.cookie")

# 站点 -> (host 匹配后缀, 登录态标志 Cookie 名)。命中任意一个标志 Cookie 即认为已登录。
SITE_LOGIN_COOKIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bilibili": ((".bilibili.com", "bilibili.com"), ("SESSDATA", "bili_jct", "DedeUserID")),
    "youtube": (
        (".youtube.com", "youtube.com", ".google.com"),
        # 不同浏览器/登录方式留下的标志 Cookie 并不一致，命中任意一个即算已登录。
        (
            "SID",
            "SAPISID",
            "APISID",
            "HSID",
            "SSID",
            "LOGIN_INFO",
            "__Secure-1PSID",
            "__Secure-3PSID",
            "__Secure-3PAPISID",
        ),
    ),
}


@dataclass(frozen=True)
class CookieDatabase:
    """一个待探测的浏览器 Cookie 库。"""

    browser_spec: str          # 传给 yt-dlp --cookies-from-browser 的值，如 chrome:Default
    path: Path                 # Cookie 库文件路径
    kind: str                  # "chromium" 或 "firefox"


@dataclass
class ProbeReport:
    """探测结果 + 读不到的浏览器，后者要如实告诉用户原因。"""

    matches: dict[str, str]
    unreadable: list[str]


def probe_site_cookie_browsers(
    sites: tuple[str, ...],
    databases: list[CookieDatabase],
) -> dict[str, str]:
    """为每个站点选出第一个登录过它的浏览器。

    返回 {site: browser_spec}；某站点一个浏览器都没登录时，该站点不出现在结果里。
    databases 的顺序即优先级（调用方按「默认浏览器优先」排好）。
    """
    return probe_site_cookie_browsers_detailed(sites, databases).matches


def probe_site_cookie_browsers_detailed(
    sites: tuple[str, ...],
    databases: list[CookieDatabase],
) -> ProbeReport:
    matches: dict[str, str] = {}
    unreadable: list[str] = []
    for database in _ranked(databases):
        remaining = [site for site in sites if site not in matches]
        if not remaining:
            break
        try:
            logged_in = _sites_logged_in(database, remaining)
        except CookieDatabaseUnreadable:
            # 运行中的 Chromium 会独占 Cookies 库，这不是"没登录"，必须区分开
            # 并告诉用户「关掉浏览器再检测」，否则看起来像检测功能坏了。
            logger.info("Cookie 库无法读取（浏览器可能正在运行） spec=%s", database.browser_spec)
            unreadable.append(database.browser_spec)
            continue
        except Exception:  # noqa: BLE001 - 单个库损坏不应中断整轮探测
            logger.warning("探测浏览器 Cookie 失败 spec=%s path=%s", database.browser_spec, database.path, exc_info=True)
            unreadable.append(database.browser_spec)
            continue
        for site in logged_in:
            matches.setdefault(site, database.browser_spec)
    return ProbeReport(matches=matches, unreadable=unreadable)


class CookieDatabaseUnreadable(RuntimeError):
    """Cookie 库存在但打不开（多为浏览器运行时的独占锁）。"""


def _ranked(databases: list[CookieDatabase]) -> list[CookieDatabase]:
    """Firefox 优先。

    探测只能确认「Cookie 名字在不在」（host/name 是明文），确认不了「值能不能被
    yt-dlp 解出来」。而 Chrome 127+ / Brave / Edge 的 App-Bound Encryption 会让
    yt-dlp 报 "Failed to decrypt with DPAPI" —— 名字明明在、实际却取不到值。
    Firefox 的 moz_cookies 里值是明文，yt-dlp 一向能读。所以同样命中登录 Cookie 时
    先选 Firefox，能显著降低选中一个「看起来行、实际不行」的浏览器的概率。
    """
    return sorted(databases, key=lambda item: 0 if item.kind == "firefox" else 1)


def _sites_logged_in(database: CookieDatabase, sites: list[str]) -> list[str]:
    if not database.path.is_file():
        return []
    with tempfile.TemporaryDirectory() as temp_dir:
        conn = _open_readonly(database.path, Path(temp_dir) / "cookies.db")
        try:
            return [site for site in sites if _has_login_cookie(conn, database.kind, site)]
        finally:
            conn.close()


def _open_readonly(source: Path, copy_path: Path) -> sqlite3.Connection:
    """以只读方式打开 Cookie 库。

    先复制副本（浏览器运行时通常对库文件加了锁），复制失败再退回直接打开源库。

    副本必须连 `-wal` 一起复制、且**不能**加 `immutable=1`：Cookie 库是 WAL 模式，
    刚登录写入的 Cookie 常常还在 `-wal` 里没 checkpoint，而 `immutable=1` 会让
    SQLite 直接无视 WAL。只拷主库或加了 immutable，读到的都是旧快照 —— 表现就是
    「浏览器明明登录着，探测却说没登录」。直接打开源库那条路仍保留 immutable：
    此时库多半正被独占，immutable 是唯一还能读出点东西的方式。
    """
    try:
        copy_sqlite_database(source, copy_path)
        target: Path = copy_path
        uri = f"file:{copy_path}?mode=ro"
    except OSError as exc:
        logger.debug("复制 Cookie 库失败，改为直接只读打开 path=%s detail=%s", source, exc)
        target = source
        uri = f"file:{source}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as exc:
        raise CookieDatabaseUnreadable(str(exc)) from exc
    try:
        # connect 是惰性的，且 immutable 下 "SELECT 1" 连页面都不用读；必须查一次
        # schema 才能真正暴露"文件被独占"或"根本不是 SQLite 库"。
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        # 必须显式关闭：连接残留会在 Windows 上占住临时副本，让临时目录清理失败，
        # 那个清理异常还会顶替掉真正的错误原因。
        conn.close()
        logger.debug("Cookie 库打不开 path=%s detail=%s", target, exc)
        raise CookieDatabaseUnreadable(str(exc)) from exc
    return conn


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
            cookie_file = profile_dir / COOKIE_DB_NAME
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
            "firefox": _firefox_profile_parents(home=home, environ=environ, platform_name=platform_name),
        }
        return mapping.get(browser, [])
    config_home = Path(environ.get("XDG_CONFIG_HOME", "").strip() or home / ".config")
    mapping = {
        "chrome": [config_home / "google-chrome", home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome"],
        "chromium": [config_home / "chromium", home / "snap" / "chromium" / "common" / "chromium"],
        "brave": [config_home / "BraveSoftware" / "Brave-Browser"],
        "vivaldi": [config_home / "vivaldi"],
        "firefox": _firefox_profile_parents(home=home, environ=environ, platform_name=platform_name),
    }
    return mapping.get(browser, [])


def _firefox_profile_parents(*, home: Path, environ: dict[str, str], platform_name: str) -> list[Path]:
    """profile 目录的可能父目录：Windows 在 `<root>/Profiles`，Linux 常直接在 `<root>` 下。

    走共用的安装根发现，才能覆盖 Microsoft Store 版（数据被重定向到
    `Packages\\...\\LocalCache`）与 Snap/Flatpak 版。只认 `%APPDATA%` 时这些安装的
    profile 一个都定位不到，探测便会把「登录着的 Firefox」判成读不到。
    """
    parents: list[Path] = []
    for root in firefox_install_roots(environ=environ, home=home, platform_name=platform_name):
        for parent in (root / "Profiles", root):
            if parent not in parents:
                parents.append(parent)
    return parents
