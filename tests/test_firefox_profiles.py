"""Firefox profile 发现与 Cookie 库复制。

核心是两条：正在使用的 profile 必须由 profiles.ini 决定（目录名看不出来），
以及 Cookie 库必须连 `-wal` 一起复制（Firefox 在跑时新 Cookie 还在 WAL 里）。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.firefox_profiles import (
    copy_sqlite_database,
    firefox_install_roots,
    firefox_profiles,
    resolve_firefox_profile_dir,
)


def make_profile(root: Path, name: str, *, cookies: bool = True) -> Path:
    profile_dir = root / "Profiles" / name
    profile_dir.mkdir(parents=True, exist_ok=True)
    if cookies:
        (profile_dir / "cookies.sqlite").write_bytes(b"")
    return profile_dir


def write_profiles_ini(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.ini").write_text(body, encoding="utf-8")


class InstallRootTests(unittest.TestCase):
    def test_windows_includes_the_microsoft_store_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "Local"
            package = local / "Packages" / "Mozilla.Firefox_n80bbvh6b1yt2"
            package.mkdir(parents=True)
            env = {"APPDATA": str(Path(tmp) / "Roaming"), "LOCALAPPDATA": str(local)}

            roots = firefox_install_roots(environ=env, platform_name="win32")

            self.assertEqual(roots[0], Path(env["APPDATA"]) / "Mozilla" / "Firefox")
            self.assertIn(
                package / "LocalCache" / "Roaming" / "Mozilla" / "Firefox",
                roots,
            )

    def test_linux_covers_snap_and_flatpak(self) -> None:
        home = Path("/home/tester")
        roots = firefox_install_roots(home=home, platform_name="linux", environ={})

        self.assertEqual(roots[0], home / ".mozilla" / "firefox")
        self.assertIn(home / "snap" / "firefox" / "common" / ".mozilla" / "firefox", roots)
        self.assertIn(
            home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox", roots
        )


class ProfileDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Roaming" / "Mozilla" / "Firefox"
        self.env = {
            "APPDATA": str(Path(self.temp.name) / "Roaming"),
            "LOCALAPPDATA": str(Path(self.temp.name) / "Local"),
        }

    def _profiles(self, **kwargs):
        return firefox_profiles(environ=self.env, platform_name="win32", **kwargs)

    def test_install_default_wins_over_the_legacy_profile_flag(self) -> None:
        """`[Install*]` 的 Default 才是这台机器正在用的 profile。"""
        make_profile(self.root, "aaa.default")
        make_profile(self.root, "bbb.default-release")
        write_profiles_ini(
            self.root,
            "[Profile0]\nName=old\nIsRelative=1\nPath=Profiles/aaa.default\nDefault=1\n\n"
            "[Profile1]\nName=new\nIsRelative=1\nPath=Profiles/bbb.default-release\n\n"
            "[Install1B2C3D]\nDefault=Profiles/bbb.default-release\nLocked=1\n",
        )

        found = self._profiles()

        self.assertEqual(found[0].name, "bbb.default-release")
        self.assertTrue(found[0].is_default)

    def test_legacy_flag_is_used_when_no_install_section_exists(self) -> None:
        make_profile(self.root, "aaa.default")
        make_profile(self.root, "bbb.other")
        write_profiles_ini(
            self.root,
            "[Profile0]\nName=a\nIsRelative=1\nPath=Profiles/bbb.other\n\n"
            "[Profile1]\nName=b\nIsRelative=1\nPath=Profiles/aaa.default\nDefault=1\n",
        )

        self.assertEqual(self._profiles()[0].name, "aaa.default")

    def test_profiles_without_a_cookie_database_are_hidden_by_default(self) -> None:
        make_profile(self.root, "with.cookies")
        make_profile(self.root, "never.launched", cookies=False)

        self.assertEqual([item.name for item in self._profiles()], ["with.cookies"])
        names = [item.name for item in self._profiles(require_cookies=False)]
        self.assertIn("never.launched", names)

    def test_microsoft_store_profiles_are_discovered(self) -> None:
        store_root = (
            Path(self.env["LOCALAPPDATA"])
            / "Packages"
            / "Mozilla.Firefox_abc"
            / "LocalCache"
            / "Roaming"
            / "Mozilla"
            / "Firefox"
        )
        store_profile = make_profile(store_root, "store.default-release")

        found = self._profiles()

        self.assertEqual([item.path for item in found], [store_profile])

    def test_no_firefox_yields_nothing(self) -> None:
        self.assertEqual(self._profiles(), [])


class ResolveProfileDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Roaming" / "Mozilla" / "Firefox"
        self.env = {
            "APPDATA": str(Path(self.temp.name) / "Roaming"),
            "LOCALAPPDATA": str(Path(self.temp.name) / "Local"),
        }

    def _resolve(self, profile: str):
        return resolve_firefox_profile_dir(profile, environ=self.env, platform_name="win32")

    def test_blank_profile_picks_the_default_one(self) -> None:
        make_profile(self.root, "aaa.other")
        target = make_profile(self.root, "bbb.default-release")
        write_profiles_ini(
            self.root,
            "[Install1]\nDefault=Profiles/bbb.default-release\n",
        )

        self.assertEqual(self._resolve(""), target)

    def test_directory_name_is_matched_case_insensitively(self) -> None:
        target = make_profile(self.root, "Mixed.Case-Release")

        self.assertEqual(self._resolve("mixed.case-release"), target)

    def test_absolute_path_is_used_as_is(self) -> None:
        target = make_profile(self.root, "portable")

        self.assertEqual(self._resolve(str(target)), target)

    def test_absolute_path_without_a_cookie_database_is_rejected(self) -> None:
        target = make_profile(self.root, "empty", cookies=False)

        self.assertIsNone(self._resolve(str(target)))

    def test_unknown_name_yields_none(self) -> None:
        make_profile(self.root, "real.profile")

        self.assertIsNone(self._resolve("no-such-profile"))


class CopySqliteDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _wal_database(self) -> tuple[Path, sqlite3.Connection]:
        """未 checkpoint 的 WAL 库：数据只在 `-wal` 里，主库还是空的。"""
        source = self.root / "source" / "cookies.sqlite"
        source.parent.mkdir(parents=True)
        conn = sqlite3.connect(source)
        conn.execute("PRAGMA journal_mode=wal")
        conn.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT)")
        conn.execute("INSERT INTO moz_cookies VALUES ('.youtube.com', 'SID')")
        conn.commit()
        self.addCleanup(conn.close)
        return source, conn

    def test_wal_content_survives_the_copy(self) -> None:
        source, _conn = self._wal_database()
        target = self.root / "copy" / "cookies.sqlite"
        target.parent.mkdir(parents=True)

        copy_sqlite_database(source, target)

        self.assertTrue(target.with_name(target.name + "-wal").is_file())
        copied = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()[0], 1)
        finally:
            copied.close()

    def test_main_database_alone_would_have_lost_the_rows(self) -> None:
        """反证：只拷主库时连表都还看不见 —— 这正是「登录了却读不出来」的成因。"""
        import shutil

        source, _conn = self._wal_database()
        target = self.root / "main-only" / "cookies.sqlite"
        target.parent.mkdir(parents=True)
        shutil.copy2(source, target)

        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            with self.assertRaises(sqlite3.Error):
                conn.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()
        finally:
            conn.close()

    def test_missing_sidecars_are_not_an_error(self) -> None:
        source = self.root / "plain.sqlite"
        sqlite3.connect(source).close()
        target = self.root / "plain-copy.sqlite"

        copy_sqlite_database(source, target)

        self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
