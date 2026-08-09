"""从 Chrome/Edge 等 Chromium 系浏览器解密 Cookie，导出成 Netscape cookies.txt。

背景：Chrome 127+/新版 Edge 用 App-Bound Encryption 存 Cookie（值以 ``v20`` 打头），
密钥被浏览器的 IElevator COM 提权服务包了一层，yt-dlp 走 DPAPI 必然报
``Failed to decrypt with DPAPI``（yt-dlp #10927）。老的 ``v10`` Cookie 用 DPAPI
就能解。

**为什么要子进程**：调用 IElevator 的 ``DecryptData`` 在 GUI 进程内可能直接让
Python 解释器崩溃（访问冲突）。所以真正的解密永远在一个独立子进程里做
（``run_cli``），GUI 侧只用 :func:`extract_cookies_to_netscape` 以「尽力而为」的方式
调它：超时、非零退出、缺少依赖……任何异常都回退到空字符串，调用方继续走原有的
Firefox / 配置 Cookie 文件链路，绝不因此比现状更差。

绝不强行关闭用户的浏览器进程：Cookie 库被独占时只尝试读副本，失败就放弃。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


logger = logging.getLogger("tube_player.cookie")

# GUI 侧调子进程的超时：解密几十条 Cookie 很快，给足冗余即可。
EXTRACT_TIMEOUT_SECONDS = 25.0

# 各浏览器的 IElevator 提权服务 CLSID / IID（来自各自 Chromium 源码 elevation_service）。
# 只有装了对应服务的浏览器才能解自己的 v20 Cookie。
_ELEVATOR_GUIDS: dict[str, tuple[str, str]] = {
    "chrome": ("{708860E0-F641-4611-8895-7D867DD3675B}", "{463ABECF-410D-407F-8AF5-0DF35A005CC8}"),
    "edge": ("{1FCBE96C-1697-43AF-9140-2897C7C69767}", "{C9C2B807-7731-4F34-81B7-44FF7779522B}"),
    "brave": ("{576B31AF-6369-4B6B-8560-E4B203A97A8B}", "{F396861E-0C8E-4C71-8256-2FAE6D759CE9}"),
}

# Chromium 系浏览器 User Data 目录（相对 %LOCALAPPDATA%），与 config_service 保持一致。
_USER_DATA_SUBPATHS: dict[str, tuple[str, ...]] = {
    "chrome": ("Google", "Chrome", "User Data"),
    "edge": ("Microsoft", "Edge", "User Data"),
    "brave": ("BraveSoftware", "Brave-Browser", "User Data"),
    "chromium": ("Chromium", "User Data"),
    "vivaldi": ("Vivaldi", "User Data"),
}

# Chrome 时间戳是 1601-01-01 起的微秒数；转 Unix 秒要减去这个偏移。
_CHROME_EPOCH_OFFSET = 11644473600


# --------------------------------------------------------------------------- #
# GUI 侧：以子进程「尽力而为」调用，任何失败都回退到空字符串。
# --------------------------------------------------------------------------- #
def extract_cookies_to_netscape(
    browser_spec: str,
    target_url: str,
    *,
    timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> str:
    """在独立子进程里解密该浏览器的 Cookie，返回写好的 Netscape 文件路径。

    仅在 Windows + Chromium 系浏览器上有意义；其它情况（Firefox、非 Windows、
    缺少加密依赖、子进程崩溃或超时）一律返回 ""，让调用方走原有回退链路。
    """
    spec = str(browser_spec or "").strip()
    if not spec or not sys.platform.startswith("win"):
        return ""
    browser = spec.split(":", 1)[0].strip().lower()
    if browser not in _USER_DATA_SUBPATHS:
        return ""

    output_path = _output_path_for(target_url)
    command = [*_subprocess_entry(), spec, target_url, str(output_path)]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Chromium Cookie 解密子进程未能运行：%s", exc)
        return ""

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        logger.info("Chromium Cookie 解密子进程返回 %s：%s", completed.returncode, detail[:500])
        return ""

    path = Path(output_path)
    if not path.exists() or path.stat().st_size == 0:
        return ""
    logger.info("已从浏览器 %s 解密 Cookie 并写入 %s", spec, path)
    return str(path)


def _subprocess_entry() -> list[str]:
    """子进程入口命令。

    源码运行时用 ``python -m services.chromium_cookie_extractor``；PyInstaller 冻结后
    没有独立解释器，改用主程序自身的哨兵参数（见 ``main.py`` 顶部的拦截）。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, _FROZEN_SENTINEL]
    return [sys.executable, "-m", "services.chromium_cookie_extractor"]


_FROZEN_SENTINEL = "--extract-chromium-cookies"


def _output_path_for(target_url: str) -> Path:
    from app_paths import CACHE_DIR

    target_dir = CACHE_DIR / "cookies"
    target_dir.mkdir(parents=True, exist_ok=True)
    site = "bilibili" if "bilibili" in (urlparse(target_url).hostname or "").lower() else "youtube"
    return target_dir / f"chromium_{site}_cookies.txt"


# --------------------------------------------------------------------------- #
# 子进程侧：真正读库、解密、落盘。
# --------------------------------------------------------------------------- #
def run_cli(argv: list[str]) -> int:
    """子进程主体：``argv = [browser_spec, target_url, output_path]``。

    成功写出至少一条 Cookie 返回 0，其余返回非零并把原因打到 stderr。这里绝不能
    抛异常给上层——它就是崩溃隔离层本身，任何异常都转成非零退出码。
    """
    if len(argv) < 3:
        print("usage: browser_spec target_url output_path", file=sys.stderr)
        return 2
    browser_spec, target_url, output_path = argv[0], argv[1], argv[2]
    try:
        rows = extract_cookie_rows(browser_spec, target_url)
    except Exception as exc:  # noqa: BLE001 —— 隔离层，任何失败都不能上抛
        print(f"extract failed: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print("no cookies decrypted", file=sys.stderr)
        return 1
    try:
        write_netscape_file(rows, Path(output_path))
    except OSError as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK {len(rows)}", file=sys.stderr)
    return 0


class CookieRow:
    __slots__ = ("host", "name", "value", "path", "secure", "expires")

    def __init__(self, host: str, name: str, value: str, path: str, secure: bool, expires: int) -> None:
        self.host = host
        self.name = name
        self.value = value
        self.path = path or "/"
        self.secure = secure
        self.expires = expires


def extract_cookie_rows(browser_spec: str, target_url: str) -> list[CookieRow]:
    spec = str(browser_spec or "").strip()
    browser = spec.split(":", 1)[0].strip().lower()
    profile = spec.split(":", 1)[1].strip() if ":" in spec else ""
    user_data = _user_data_dir(browser)
    if user_data is None:
        return []
    profile_dir = user_data / profile if profile else user_data / "Default"
    if not profile_dir.exists():
        profile_dir = user_data / "Default"

    v10_key = _load_v10_key(user_data)
    v20_key = _load_v20_key(user_data, browser)
    if v10_key is None and v20_key is None:
        return []

    cookies_db = _cookies_db_path(profile_dir)
    if cookies_db is None:
        return []

    host = (urlparse(target_url).hostname or "").lower()
    rows: list[CookieRow] = []
    for host_key, name, encrypted_value, path, is_secure, expires in _read_raw_rows(cookies_db):
        if host and not _domain_matches(host, host_key):
            continue
        value = _decrypt_cookie_value(encrypted_value, v10_key, v20_key)
        if not value:
            continue
        rows.append(CookieRow(host_key, name, value, path, bool(is_secure), int(expires or 0)))
    return rows


def _user_data_dir(browser: str) -> Path | None:
    parts = _USER_DATA_SUBPATHS.get(browser)
    if not parts:
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    user_data = Path(local_app_data).joinpath(*parts)
    return user_data if user_data.exists() else None


def _cookies_db_path(profile_dir: Path) -> Path | None:
    for candidate in (profile_dir / "Network" / "Cookies", profile_dir / "Cookies"):
        if candidate.is_file():
            return candidate
    return None


def _read_raw_rows(cookies_db: Path) -> list[tuple[str, str, bytes, str, int, int]]:
    """复制一份 Cookie 库再读，避免与正在运行的浏览器争锁；库被独占也只是读失败。"""
    fd, temp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(cookies_db, temp_path)
    except OSError:
        _silent_remove(temp_path)
        return []
    try:
        conn = sqlite3.connect(temp_path)
        try:
            cursor = conn.execute(
                "SELECT host_key, name, encrypted_value, path, is_secure, expires_utc FROM cookies"
            )
            return [
                (str(r[0] or ""), str(r[1] or ""), bytes(r[2] or b""), str(r[3] or "/"), int(r[4] or 0), int(r[5] or 0))
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    finally:
        _silent_remove(temp_path)


# --------------------------------------------------------------------------- #
# 密钥读取
# --------------------------------------------------------------------------- #
def _load_local_state(user_data: Path) -> dict:
    state_path = user_data / "Local State"
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_v10_key(user_data: Path) -> bytes | None:
    """os_crypt.encrypted_key：base64 解码去掉 ``DPAPI`` 前缀后用户级 DPAPI 解出 AES 密钥。"""
    encrypted = str(_load_local_state(user_data).get("os_crypt", {}).get("encrypted_key", "") or "")
    if not encrypted:
        return None
    try:
        blob = base64.b64decode(encrypted)
    except (ValueError, TypeError):
        return None
    if not blob.startswith(b"DPAPI"):
        return None
    return _dpapi_unprotect(blob[len(b"DPAPI") :])


def _load_v20_key(user_data: Path, browser: str) -> bytes | None:
    """app_bound_encrypted_key：去掉 ``APPB`` 前缀后交给 IElevator 提权服务解出 AES 密钥。"""
    encrypted = str(_load_local_state(user_data).get("os_crypt", {}).get("app_bound_encrypted_key", "") or "")
    if not encrypted or browser not in _ELEVATOR_GUIDS:
        return None
    try:
        blob = base64.b64decode(encrypted)
    except (ValueError, TypeError):
        return None
    if blob.startswith(b"APPB"):
        blob = blob[len(b"APPB") :]
    try:
        decrypted = _elevator_decrypt(blob, browser)
    except Exception as exc:  # noqa: BLE001 —— COM 层任何失败都退回「没有 v20 密钥」
        logger.debug("IElevator 解密 app-bound 密钥失败：%s", exc)
        return None
    if not decrypted:
        return None
    # 解出的内容通常是 [1 字节标志][32 字节 AES 密钥]，也可能就是 32 字节；两种都兼容。
    if len(decrypted) >= 32:
        return decrypted[-32:]
    return None


# --------------------------------------------------------------------------- #
# Cookie 值解密
# --------------------------------------------------------------------------- #
def _decrypt_cookie_value(blob: bytes, v10_key: bytes | None, v20_key: bytes | None) -> str:
    if not blob:
        return ""
    prefix = blob[:3]
    if prefix == b"v20":
        if v20_key is None:
            return ""
        plaintext = _aes_gcm_decrypt(v20_key, blob[3:15], blob[15:])
        # v20 明文前 32 字节是域名哈希等元数据，真正的值在其后。
        if plaintext is not None and len(plaintext) >= 32:
            return _safe_decode(plaintext[32:])
        return _safe_decode(plaintext) if plaintext is not None else ""
    if prefix == b"v10":
        if v10_key is None:
            return ""
        plaintext = _aes_gcm_decrypt(v10_key, blob[3:15], blob[15:])
        return _safe_decode(plaintext) if plaintext is not None else ""
    # 无前缀：老版本直接 DPAPI 加密整个值。
    plaintext = _dpapi_unprotect(blob)
    return _safe_decode(plaintext) if plaintext is not None else ""


def _safe_decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def _aes_gcm_decrypt(key: bytes, nonce: bytes, payload: bytes) -> bytes | None:
    """AES-256-GCM 解密，payload = ciphertext || 16 字节 tag。优先 cryptography，退回 pycryptodome。"""
    if len(payload) < 16:
        return None
    ciphertext, tag = payload[:-16], payload[-16:]
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).decrypt(nonce, ciphertext + tag, None)
    except Exception:  # noqa: BLE001 —— 依赖缺失或标签校验失败都算解不出
        pass
    try:
        from Crypto.Cipher import AES

        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Windows DPAPI（ctypes，无第三方依赖）
# --------------------------------------------------------------------------- #
def _dpapi_unprotect(data: bytes) -> bytes | None:
    if not sys.platform.startswith("win") or not data:
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
        logger.debug("CryptUnprotectData 失败：%s", exc)
        return None


# --------------------------------------------------------------------------- #
# IElevator COM 提权解密（v20 密钥）——最脆弱的一段，全程在子进程里跑。
# --------------------------------------------------------------------------- #
def _elevator_decrypt(blob: bytes, browser: str) -> bytes | None:
    import ctypes
    from ctypes import wintypes

    clsid_str, iid_str = _ELEVATOR_GUIDS[browser]
    ole32 = ctypes.windll.ole32
    oleaut32 = ctypes.windll.oleaut32

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def _guid(text: str) -> GUID:
        guid = GUID()
        if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid)) != 0:
            raise OSError(f"CLSIDFromString 失败: {text}")
        return guid

    oleaut32.SysAllocStringByteLen.restype = ctypes.c_void_p
    oleaut32.SysAllocStringByteLen.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    oleaut32.SysStringByteLen.restype = ctypes.c_uint
    oleaut32.SysStringByteLen.argtypes = [ctypes.c_void_p]
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]

    COINIT_APARTMENTTHREADED = 0x2
    CLSCTX_LOCAL_SERVER = 0x4
    RPC_C_AUTHN_DEFAULT = 0xFFFFFFFF
    RPC_C_AUTHZ_DEFAULT = 0xFFFFFFFF
    RPC_C_AUTHN_LEVEL_PKT_PRIVACY = 6
    RPC_C_IMP_LEVEL_IMPERSONATE = 3
    EOAC_DYNAMIC_CLOAKING = 0x40

    hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    initialized = hr in (0, 1)  # S_OK / S_FALSE
    try:
        clsid = _guid(clsid_str)
        iid = _guid(iid_str)
        elevator = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_LOCAL_SERVER, ctypes.byref(iid), ctypes.byref(elevator)
        )
        if hr != 0 or not elevator.value:
            raise OSError(f"CoCreateInstance 失败 hr={hr:#x}")
        try:
            # 提权服务要求 PKT_PRIVACY + 动态 cloaking，否则 DecryptData 会拒绝。
            ole32.CoSetProxyBlanket(
                elevator,
                RPC_C_AUTHN_DEFAULT,
                RPC_C_AUTHZ_DEFAULT,
                None,
                RPC_C_AUTHN_LEVEL_PKT_PRIVACY,
                RPC_C_IMP_LEVEL_IMPERSONATE,
                None,
                EOAC_DYNAMIC_CLOAKING,
            )

            ciphertext_bstr = oleaut32.SysAllocStringByteLen(blob, len(blob))
            if not ciphertext_bstr:
                raise OSError("SysAllocStringByteLen 失败")
            plaintext_bstr = ctypes.c_void_p()
            last_error = wintypes.DWORD(0)
            try:
                # 虚表：0 QueryInterface,1 AddRef,2 Release,3 RunRecoveryCRXElevated,
                # 4 EncryptData,5 DecryptData。
                decrypt = _com_method(
                    elevator,
                    5,
                    ctypes.c_long,
                    [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)],
                )
                hr = decrypt(elevator, ciphertext_bstr, ctypes.byref(plaintext_bstr), ctypes.byref(last_error))
                if hr != 0 or not plaintext_bstr.value:
                    raise OSError(f"DecryptData 失败 hr={hr:#x} last_error={last_error.value}")
                length = oleaut32.SysStringByteLen(plaintext_bstr)
                return ctypes.string_at(plaintext_bstr, length)
            finally:
                if ciphertext_bstr:
                    oleaut32.SysFreeString(ciphertext_bstr)
                if plaintext_bstr.value:
                    oleaut32.SysFreeString(plaintext_bstr)
        finally:
            _com_release(elevator)
    finally:
        if initialized:
            ole32.CoUninitialize()


def _com_method(interface, index, restype, argtypes):
    import ctypes

    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.c_void_p))[0]
    func_ptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
    prototype = ctypes.WINFUNCTYPE(restype, *argtypes)
    return prototype(func_ptr)


def _com_release(interface) -> None:
    import ctypes

    try:
        release = _com_method(interface, 2, ctypes.c_ulong, [ctypes.c_void_p])
        release(interface)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Netscape cookies.txt 落盘
# --------------------------------------------------------------------------- #
def write_netscape_file(rows: list[CookieRow], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    far_future = int(time.time()) + 365 * 24 * 60 * 60
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by Tube_Ultimate_Player from Chromium App-Bound cookies.",
    ]
    for row in rows:
        name = row.name.strip()
        if not name:
            continue
        domain = row.host.strip()
        if not domain:
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if row.secure else "FALSE"
        expires = _chrome_epoch_to_unix(row.expires) or far_future
        value = row.value.replace("\t", "").replace("\r", "").replace("\n", "")
        lines.append(
            f"{domain}\t{include_subdomains}\t{row.path}\t{secure}\t{expires}\t{name}\t{value}"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            target.chmod(0o600)
        except OSError:
            pass


def _chrome_epoch_to_unix(expires_utc: int) -> int:
    if expires_utc <= 0:
        return 0
    unix = expires_utc // 1_000_000 - _CHROME_EPOCH_OFFSET
    return unix if unix > 0 else 0


def _domain_matches(host: str, domain: str) -> bool:
    if not domain:
        return False
    clean_domain = domain.lstrip(".").lower()
    clean_host = host.lstrip(".").lower()
    return clean_host == clean_domain or clean_host.endswith("." + clean_domain)


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
