from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from services.backup_service import (
    BackupError,
    BackupVersionTooNew,
    build_backup_archive,
    prune_local_backups,
    prune_remote_backups,
    restore_backup_archive,
    validate_backup_archive,
)
from services.webdav_client import RemoteBackup
from services.config_service import ConfigService


class BackupArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_dir = self.root / "config"
        self.data_dir = self.root / "data"
        self.backup_dir = self.root / "backups"
        self.user_config = self.config_dir / "user_config.json"
        self.database = self.data_dir / "tube_ultimate_player.sqlite3"
        self.tasks = self.data_dir / "download_tasks.json"
        self.cookies = {
            "youtube": self.root / "cookie_youtube.txt",
            "bilibili": self.root / "cookie_bilibili.txt",
        }
        self.user_config.parent.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.user_config.write_text('{"value": 1}', encoding="utf-8")
        self.tasks.write_text('[{"task": 1}]', encoding="utf-8")
        self.cookies["youtube"].write_text("youtube-cookie", encoding="utf-8")
        self.cookies["bilibili"].write_text("bilibili-cookie", encoding="utf-8")
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('before')")
            connection.commit()
        finally:
            connection.close()

    def _build(self, include_cookies: bool = False) -> Path:
        return build_backup_archive(
            output_dir=self.backup_dir,
            user_config_path=self.user_config,
            db_path=self.database,
            tasks_path=self.tasks,
            include_cookies=include_cookies,
            cookie_paths=self.cookies,
        )

    def test_archive_has_manifest_hashes_and_optional_cookies(self) -> None:
        archive = self._build(False)
        manifest = validate_backup_archive(archive)
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())

        self.assertIn("manifest.json", names)
        self.assertIn("data/tube_ultimate_player.sqlite3", names)
        self.assertFalse(any(name.startswith("cookies/") for name in names))
        self.assertFalse(manifest["include_cookies"])
        self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["files"]))

    def test_tampered_file_is_rejected_before_restore(self) -> None:
        archive = self._build()
        rewritten = self.backup_dir / "tampered.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "config/user_config.json":
                    data += b"tampered"
                target.writestr(name, data)

        with self.assertRaisesRegex(BackupError, "校验失败"):
            validate_backup_archive(rewritten)

    def test_zip_slip_entry_is_rejected(self) -> None:
        archive = self._build()
        malicious = self.backup_dir / "malicious.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(malicious, "w") as target:
            for name in source.namelist():
                target.writestr(name, source.read(name))
            target.writestr("../outside.txt", "bad")

        with self.assertRaises(Exception):
            validate_backup_archive(malicious)

    def test_restore_creates_snapshot_and_replaces_files(self) -> None:
        archive = self._build()
        self.user_config.write_text('{"value": 2}', encoding="utf-8")
        self.tasks.write_text("[]", encoding="utf-8")
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE sample SET value='after'")
            connection.commit()
        finally:
            connection.close()

        snapshot = restore_backup_archive(
            archive,
            backup_dir=self.backup_dir,
            user_config_path=self.user_config,
            db_path=self.database,
            tasks_path=self.tasks,
            config_dir=self.config_dir,
        )

        self.assertTrue(snapshot.name.startswith("pre-restore-"))
        self.assertEqual(json.loads(self.user_config.read_text(encoding="utf-8"))["value"], 1)
        self.assertEqual(json.loads(self.tasks.read_text(encoding="utf-8"))[0]["task"], 1)
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "before")
        finally:
            connection.close()

    def test_config_persistence_can_be_suspended_after_restore(self) -> None:
        defaults = self.root / "defaults.json"
        defaults.write_text('{"value": 1}', encoding="utf-8")
        user = self.root / "user.json"
        user.write_text('{"value": 2}', encoding="utf-8")
        config = ConfigService(defaults, user)
        config.suspend_persistence()
        config.set("value", 3)
        config.save()

        self.assertEqual(json.loads(user.read_text(encoding="utf-8"))["value"], 2)

    def test_newer_version_requires_explicit_confirmation(self) -> None:
        archive = self._build()
        rewritten = self.backup_dir / "newer.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(data.decode("utf-8"))
                    manifest["app_version"] = "999.0.0"
                    data = json.dumps(manifest).encode("utf-8")
                target.writestr(name, data)

        with self.assertRaises(BackupVersionTooNew):
            validate_backup_archive(rewritten)
        self.assertEqual(validate_backup_archive(rewritten, allow_newer=True)["app_version"], "999.0.0")

    def test_cookie_entries_require_include_cookies_flag(self) -> None:
        archive = self._build(True)
        rewritten = self.backup_dir / "cookie-flag.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    manifest = json.loads(data.decode("utf-8"))
                    manifest["include_cookies"] = False
                    data = json.dumps(manifest).encode("utf-8")
                target.writestr(name, data)

        with self.assertRaisesRegex(BackupError, "不含 Cookie"):
            validate_backup_archive(rewritten)

    def test_local_and_remote_retention_only_remove_old_program_backups(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        names = [f"tube-backup-0.2.25-20260814-12000{index}.zip" for index in range(5)]
        for name in names:
            (self.backup_dir / name).write_bytes(b"zip")
        unrelated = self.backup_dir / "manual-export.zip"
        unrelated.write_bytes(b"keep")

        prune_local_backups(self.backup_dir, limit=3)

        remaining = sorted(path.name for path in self.backup_dir.glob("tube-backup-*.zip"))
        self.assertEqual(remaining, sorted(names[-3:]))
        self.assertTrue(unrelated.exists())

        class Client:
            deleted: list[str] = []

            def delete(self, name: str) -> None:
                self.deleted.append(name)

        backups = [RemoteBackup(name, 1, "", "") for name in reversed(names)]
        client = Client()
        prune_remote_backups(client, backups, limit=3)
        self.assertEqual(client.deleted, list(reversed(names))[-2:])


if __name__ == "__main__":
    unittest.main()
