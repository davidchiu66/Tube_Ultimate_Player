from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.backup_targets import BackupTargetStore
from services.secret_store import protect, unprotect
from services.webdav_client import WebdavAccount


class SecretStoreTests(unittest.TestCase):
    def test_fernet_round_trip_and_invalid_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("services.secret_store._is_windows", return_value=False):
            key_path = Path(temp_dir) / ".key"
            token = protect("s3cret", key_path=key_path)

            self.assertTrue(token.startswith("fernet:"))
            self.assertEqual(unprotect(token, key_path=key_path), "s3cret")
            self.assertEqual(unprotect("fernet:not-valid", key_path=key_path), "")
            self.assertTrue(key_path.exists())

    def test_backup_target_password_is_not_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch("services.secret_store._is_windows", return_value=False):
            root = Path(temp_dir)
            store = BackupTargetStore(root / "targets.json", root / ".key")
            saved = store.save_account(
                WebdavAccount("", "NAS", "https://dav.example/dav/", "me", "plain-password")
            )
            store.set_active(saved.account_id)

            payload = json.loads((root / "targets.json").read_text(encoding="utf-8"))
            self.assertNotIn("plain-password", payload["accounts"][0]["password"])
            self.assertTrue(payload["accounts"][0]["password"].startswith("fernet:"))
            self.assertEqual(store.active_account().password, "plain-password")

    def test_malformed_target_json_is_treated_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "targets.json"
            path.write_text("[]", encoding="utf-8")
            store = BackupTargetStore(path, Path(temp_dir) / ".key")
            self.assertEqual(store.accounts(), [])
            self.assertIsNone(store.active_account())

    def test_plain_and_unreadable_credentials_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "targets.json"
            path.write_text(
                json.dumps(
                    {
                        "active_id": "plain",
                        "accounts": [
                            {"id": "plain", "password": "plain:pw"},
                            {"id": "broken", "password": "fernet:not-valid"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            store = BackupTargetStore(path, root / ".key")
            self.assertIn("无法加密", store.credential_warning("plain"))
            self.assertIn("已失效", store.credential_warning("broken"))


if __name__ == "__main__":
    unittest.main()
