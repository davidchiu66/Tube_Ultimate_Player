from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from app_paths import CONFIG_DIR
from services.cookie_service import secure_cookie_file
from services.secret_store import protect, unprotect
from services.webdav_client import DEFAULT_REMOTE_DIR, WebdavAccount


class BackupTargetStore:
    def __init__(self, path: Path | None = None, key_path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else CONFIG_DIR / "backup_targets.json"
        self.key_path = Path(key_path) if key_path is not None else self.path.parent / ".backup_key"

    def accounts(self) -> list[WebdavAccount]:
        payload = self._load()
        result: list[WebdavAccount] = []
        for item in payload["accounts"]:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id") or "").strip()
            if not account_id:
                continue
            result.append(
                WebdavAccount(
                    account_id=account_id,
                    name=str(item.get("name") or "").strip(),
                    base_url=str(item.get("base_url") or "").strip(),
                    username=str(item.get("username") or "").strip(),
                    password=unprotect(str(item.get("password") or ""), key_path=self.key_path),
                    remote_dir=str(item.get("remote_dir") or DEFAULT_REMOTE_DIR).strip()
                    or DEFAULT_REMOTE_DIR,
                )
            )
        return result

    def active_id(self) -> str:
        return str(self._load().get("active_id") or "")

    def active_account(self) -> WebdavAccount | None:
        accounts = self.accounts()
        active_id = self.active_id()
        return next((item for item in accounts if item.account_id == active_id), accounts[0] if accounts else None)

    def credential_warning(self, account_id: str) -> str:
        for item in self._load()["accounts"]:
            if not isinstance(item, dict) or str(item.get("id") or "") != account_id:
                continue
            token = str(item.get("password") or "")
            if token.startswith("plain:"):
                return "当前平台无法加密保存 WebDAV 密码，请检查系统加密能力。"
            if token and not unprotect(token, key_path=self.key_path):
                return "WebDAV 密码已失效，请编辑账号并重新输入密码。"
            return ""
        return ""

    def save_account(self, account: WebdavAccount) -> WebdavAccount:
        payload = self._load()
        account_id = str(account.account_id or uuid.uuid4().hex[:12])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        replacement = {
            "id": account_id,
            "name": account.name.strip(),
            "base_url": account.base_url.strip(),
            "remote_dir": account.remote_dir.strip() or DEFAULT_REMOTE_DIR,
            "username": account.username.strip(),
            "password": protect(account.password, key_path=self.key_path),
            "created_at": now,
        }
        accounts = payload["accounts"]
        for index, item in enumerate(accounts):
            if isinstance(item, dict) and str(item.get("id") or "") == account_id:
                replacement["created_at"] = str(item.get("created_at") or now)
                accounts[index] = replacement
                break
        else:
            accounts.append(replacement)
        if not payload.get("active_id"):
            payload["active_id"] = account_id
        self._write(payload)
        return WebdavAccount(
            account_id=account_id,
            name=replacement["name"],
            base_url=replacement["base_url"],
            username=replacement["username"],
            password=account.password,
            remote_dir=replacement["remote_dir"],
        )

    def delete(self, account_id: str) -> bool:
        payload = self._load()
        original = len(payload["accounts"])
        payload["accounts"] = [
            item for item in payload["accounts"]
            if not isinstance(item, dict) or str(item.get("id") or "") != account_id
        ]
        if len(payload["accounts"]) == original:
            return False
        valid_ids = [str(item.get("id") or "") for item in payload["accounts"] if isinstance(item, dict)]
        if payload.get("active_id") not in valid_ids:
            payload["active_id"] = valid_ids[0] if valid_ids else ""
        self._write(payload)
        return True

    def set_active(self, account_id: str) -> None:
        payload = self._load()
        valid_ids = {str(item.get("id") or "") for item in payload["accounts"] if isinstance(item, dict)}
        selected_id = account_id if account_id in valid_ids else ""
        if payload.get("active_id") == selected_id:
            return
        payload["active_id"] = selected_id
        self._write(payload)

    def _load(self) -> dict:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        accounts = payload.get("accounts")
        return {
            "active_id": str(payload.get("active_id") or ""),
            "accounts": accounts if isinstance(accounts, list) else [],
        }

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(self.path.name + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        secure_cookie_file(temp_path)
        os.replace(temp_path, self.path)
        secure_cookie_file(self.path)
