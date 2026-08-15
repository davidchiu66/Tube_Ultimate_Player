from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path

from app_paths import CONFIG_DIR
from services.cookie_service import secure_cookie_file


logger = logging.getLogger("tube_player.backup")


def protect(text: str, *, key_path: Path | None = None) -> str:
    value = str(text or "")
    if not value:
        return ""
    payload = value.encode("utf-8")
    if _is_windows():
        encrypted = _dpapi_protect(payload)
        if encrypted is not None:
            return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    else:
        token = _fernet_encrypt(payload, key_path=key_path)
        if token:
            return "fernet:" + token

    logger.warning("backup password encryption unavailable; falling back to plain storage")
    return "plain:" + value


def unprotect(token: str, *, key_path: Path | None = None) -> str:
    value = str(token or "")
    if not value:
        return ""
    try:
        if value.startswith("dpapi:"):
            encrypted = base64.b64decode(value[6:].encode("ascii"), validate=True)
            decrypted = _dpapi_unprotect(encrypted)
            return decrypted.decode("utf-8") if decrypted is not None else ""
        if value.startswith("fernet:"):
            decrypted = _fernet_decrypt(value[7:], key_path=key_path)
            return decrypted.decode("utf-8") if decrypted is not None else ""
        if value.startswith("plain:"):
            return value[6:]
    except (ValueError, UnicodeError, OSError):
        return ""
    return ""


def is_secure_token(token: str) -> bool:
    return str(token or "").startswith(("dpapi:", "fernet:"))


def _fernet_key_path(key_path: Path | None = None) -> Path:
    return Path(key_path) if key_path is not None else CONFIG_DIR / ".backup_key"


def _load_or_create_fernet_key(key_path: Path | None = None) -> bytes | None:
    try:
        from cryptography.fernet import Fernet
    except Exception:  # noqa: BLE001
        return None

    path = _fernet_key_path(key_path)
    try:
        if path.exists():
            key = path.read_bytes().strip()
            Fernet(key)
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_bytes(key)
        secure_cookie_file(temp_path)
        os.replace(temp_path, path)
        secure_cookie_file(path)
        return key
    except (OSError, ValueError):
        logger.exception("failed to initialize backup encryption key path=%s", path)
        return None


def _fernet_encrypt(data: bytes, *, key_path: Path | None = None) -> str:
    key = _load_or_create_fernet_key(key_path)
    if not key:
        return ""
    try:
        from cryptography.fernet import Fernet

        return Fernet(key).encrypt(data).decode("ascii")
    except Exception:  # noqa: BLE001
        logger.exception("failed to encrypt backup password with Fernet")
        return ""


def _fernet_decrypt(token: str, *, key_path: Path | None = None) -> bytes | None:
    key = _load_or_create_fernet_key(key_path)
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet, InvalidToken

        return Fernet(key).decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError, UnicodeError):
        return None
    except Exception:  # noqa: BLE001
        logger.exception("failed to decrypt backup password with Fernet")
        return None


def _dpapi_protect(data: bytes) -> bytes | None:
    if not _is_windows() or not data:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception as exc:  # noqa: BLE001
        logger.debug("CryptProtectData failed: %s", exc)
        return None


def _dpapi_unprotect(data: bytes) -> bytes | None:
    if not _is_windows() or not data:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception as exc:  # noqa: BLE001
        logger.debug("CryptUnprotectData failed: %s", exc)
        return None


def _is_windows() -> bool:
    return sys.platform.startswith("win")
