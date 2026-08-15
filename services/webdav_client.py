from __future__ import annotations

import base64
import http.client
import logging
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from services.config_service import ConfigService


logger = logging.getLogger("tube_player.webdav")

DEFAULT_REMOTE_DIR = "Tube_Ultimate_Player/backups"
BACKUP_NAME_RE = re.compile(r"^tube-backup-(?P<version>.+)-(?P<stamp>\d{8}-\d{6})\.zip$")
CONTROL_TIMEOUT = 15
TRANSFER_TIMEOUT = 60
TRANSFER_CHUNK_SIZE = 1024 * 1024

_T = TypeVar("_T")
_PROXY_PREFERRED_ACCOUNTS: set[str] = set()
_PROXY_PREFERENCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class WebdavAccount:
    account_id: str
    name: str
    base_url: str
    username: str
    password: str
    remote_dir: str = DEFAULT_REMOTE_DIR


@dataclass(frozen=True)
class RemoteBackup:
    name: str
    size: int
    modified_at: str
    href: str


class WebdavError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def clear_proxy_preferences() -> None:
    with _PROXY_PREFERENCE_LOCK:
        _PROXY_PREFERRED_ACCOUNTS.clear()


class _ProgressReader:
    def __init__(self, path: Path, progress: Callable[[int], None] | None) -> None:
        self._handle = path.open("rb")
        self._total = max(0, path.stat().st_size)
        self._sent = 0
        self._progress = progress

    def __len__(self) -> int:
        return self._total

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        if chunk:
            self._sent += len(chunk)
            _report_progress(self._progress, self._sent, self._total)
        return chunk

    def close(self) -> None:
        self._handle.close()


class _Transport:
    def __init__(
        self,
        account: WebdavAccount,
        config: ConfigService,
        *,
        direct_opener=None,
        proxy_opener=None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.account = account
        self._sleep = sleep
        self._direct_opener = direct_opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )
        proxy_source, proxy = config.effective_proxy()
        self.proxy_source = proxy_source
        self.proxy = str(proxy or "").strip()
        self._proxy_opener = proxy_opener
        if self.proxy and self._proxy_opener is None:
            self._proxy_opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        self.last_channel = "直连"

    def execute(self, operation: Callable[[object], _T]) -> _T:
        if self._prefer_proxy() and self._proxy_opener is not None:
            return self._execute_proxy(operation, direct_error=None)

        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                result = operation(self._direct_opener)
                self.last_channel = "直连"
                return result
            except urllib.error.HTTPError as exc:
                raise _http_error(exc) from exc
            except _NETWORK_ERRORS as exc:
                last_error = exc
                if attempt < 2:
                    self._sleep(float(attempt + 1))

        if self.proxy and self._proxy_opener is not None:
            logger.info("webdav fallback to proxy account=%s", self.account.account_id)
            return self._execute_proxy(operation, direct_error=last_error)

        detail = _network_detail(last_error)
        raise WebdavError(f"{detail}（已重试 3 次直连，且未配置代理）") from last_error

    def _execute_proxy(
        self,
        operation: Callable[[object], _T],
        *,
        direct_error: BaseException | None,
    ) -> _T:
        try:
            result = operation(self._proxy_opener)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from exc
        except _NETWORK_ERRORS as exc:
            if direct_error is None:
                raise WebdavError(f"经代理访问失败：{_network_detail(exc)}") from exc
            raise WebdavError(
                "直连与代理均失败："
                f"直连 {_network_detail(direct_error)}；代理 {_network_detail(exc)}"
            ) from exc
        self.last_channel = f"经代理 {self.proxy_source} {self.proxy}".strip()
        with _PROXY_PREFERENCE_LOCK:
            _PROXY_PREFERRED_ACCOUNTS.add(self._preference_key())
        return result

    def _prefer_proxy(self) -> bool:
        with _PROXY_PREFERENCE_LOCK:
            return self._preference_key() in _PROXY_PREFERRED_ACCOUNTS

    def _preference_key(self) -> str:
        return self.account.account_id or f"{self.account.base_url}|{self.account.username}"


_NETWORK_ERRORS = (
    urllib.error.URLError,
    socket.timeout,
    TimeoutError,
    ConnectionError,
    http.client.HTTPException,
    ssl.SSLError,
)


class WebdavClient:
    def __init__(
        self,
        account: WebdavAccount,
        config: ConfigService,
        *,
        direct_opener=None,
        proxy_opener=None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.account = account
        self.base_url = _normalize_base_url(account.base_url)
        self.remote_dir = _normalize_remote_dir(account.remote_dir)
        credentials = f"{account.username}:{account.password}".encode("utf-8")
        self._authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self._transport = _Transport(
            account,
            config,
            direct_opener=direct_opener,
            proxy_opener=proxy_opener,
            sleep=sleep,
        )

    def test_connection(self) -> str:
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>'
        )

        def operation(opener) -> None:
            request = self._request(
                self.base_url,
                method="PROPFIND",
                data=body,
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            )
            with opener.open(request, timeout=CONTROL_TIMEOUT) as response:
                response.read()

        self._transport.execute(operation)
        return f"连接成功（{self._transport.last_channel}）"

    def ensure_dir(self) -> None:
        current = self.base_url
        for segment in [part for part in self.remote_dir.split("/") if part]:
            current = urllib.parse.urljoin(current, urllib.parse.quote(segment, safe="") + "/")

            def operation(opener, url=current.rstrip("/")) -> None:
                request = self._request(url, method="MKCOL")
                try:
                    with opener.open(request, timeout=CONTROL_TIMEOUT) as response:
                        response.read()
                except urllib.error.HTTPError as exc:
                    if exc.code in {301, 405}:
                        return
                    raise

            self._transport.execute(operation)

    def upload(
        self,
        local: Path,
        remote_name: str,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        path = Path(local)
        if not path.is_file():
            raise WebdavError(f"待上传的备份文件不存在：{path}")
        target_url = self._remote_file_url(remote_name)

        def operation(opener) -> None:
            reader = _ProgressReader(path, progress)
            try:
                request = self._request(
                    target_url,
                    method="PUT",
                    data=reader,
                    headers={
                        "Content-Type": "application/zip",
                        "Content-Length": str(path.stat().st_size),
                    },
                )
                with opener.open(request, timeout=TRANSFER_TIMEOUT) as response:
                    response.read()
                if progress is not None:
                    progress(100)
            finally:
                reader.close()

        try:
            self._transport.execute(operation)
        except Exception as exc:
            # A broken PUT may leave a partial resource on some WebDAV servers.
            # Best-effort deletion prevents the next retention pass from treating
            # that partial file as a valid backup. HTTP errors mean the server
            # rejected the request before a partial PUT is expected, so do not
            # issue a second request (and risk deleting an older same-name file).
            if getattr(exc, "status", None) is None:
                try:
                    self.delete(remote_name)
                except Exception as cleanup_error:  # noqa: BLE001
                    logger.warning("failed to clean up incomplete WebDAV upload name=%s: %s", remote_name, cleanup_error)
            raise

    def list_backups(self) -> list[RemoteBackup]:
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<D:propfind xmlns:D="DAV:"><D:prop><D:getcontentlength/>'
            b'<D:getlastmodified/><D:resourcetype/></D:prop></D:propfind>'
        )

        def operation(opener) -> bytes:
            request = self._request(
                self._remote_root_url(),
                method="PROPFIND",
                data=body,
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            )
            with opener.open(request, timeout=CONTROL_TIMEOUT) as response:
                return response.read()

        payload = self._transport.execute(operation)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise WebdavError("WebDAV 返回的备份清单无法解析") from exc

        backups: list[RemoteBackup] = []
        ns = "{DAV:}"
        for response in root.findall(f".//{ns}response"):
            href = str(response.findtext(f"{ns}href") or "")
            path = urllib.parse.unquote(urllib.parse.urlparse(href).path).rstrip("/")
            name = path.rsplit("/", 1)[-1]
            if not BACKUP_NAME_RE.fullmatch(name):
                continue
            resource_type = response.find(f".//{ns}resourcetype")
            if resource_type is not None and resource_type.find(f"{ns}collection") is not None:
                continue
            raw_size = response.findtext(f".//{ns}getcontentlength") or "0"
            try:
                size = max(0, int(raw_size))
            except (TypeError, ValueError):
                size = 0
            modified_at = str(response.findtext(f".//{ns}getlastmodified") or "")
            backups.append(RemoteBackup(name=name, size=size, modified_at=modified_at, href=href))
        backups.sort(key=lambda item: (_backup_stamp(item.name), item.name), reverse=True)
        return backups

    def download(
        self,
        remote_name: str,
        local: Path,
        progress: Callable[[int], None] | None = None,
    ) -> None:
        target = Path(local)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(target.name + ".part")
        source_url = self._remote_file_url(remote_name)

        def operation(opener) -> None:
            temp_path.unlink(missing_ok=True)
            request = self._request(source_url, method="GET")
            try:
                with opener.open(request, timeout=TRANSFER_TIMEOUT) as response:
                    try:
                        total = int(response.headers.get("Content-Length", "0") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    received = 0
                    with temp_path.open("wb") as handle:
                        while True:
                            chunk = response.read(TRANSFER_CHUNK_SIZE)
                            if not chunk:
                                break
                            handle.write(chunk)
                            received += len(chunk)
                            _report_progress(progress, received, total)
                temp_path.replace(target)
                if progress is not None:
                    progress(100)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        self._transport.execute(operation)

    def delete(self, remote_name: str) -> None:
        target_url = self._remote_file_url(remote_name)

        def operation(opener) -> None:
            request = self._request(target_url, method="DELETE")
            with opener.open(request, timeout=CONTROL_TIMEOUT) as response:
                response.read()

        self._transport.execute(operation)

    def _request(
        self,
        url: str,
        *,
        method: str,
        data=None,
        headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        request_headers = {
            "Authorization": self._authorization,
            "User-Agent": "Tube_Ultimate_Player/1.0",
        }
        request_headers.update(headers or {})
        return urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )

    def _remote_root_url(self) -> str:
        if not self.remote_dir:
            return self.base_url
        suffix = "/".join(
            urllib.parse.quote(segment, safe="")
            for segment in self.remote_dir.split("/")
            if segment
        )
        return urllib.parse.urljoin(self.base_url, suffix + "/")

    def _remote_file_url(self, remote_name: str) -> str:
        clean_name = _validate_remote_name(remote_name)
        return urllib.parse.urljoin(
            self._remote_root_url(),
            urllib.parse.quote(clean_name, safe=""),
        )


def _normalize_base_url(value: str) -> str:
    text = str(value or "").strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebdavError("WebDAV 地址必须是有效的 http:// 或 https:// 地址")
    return text.rstrip("/") + "/"


def _normalize_remote_dir(value: str) -> str:
    parts = [part for part in str(value or DEFAULT_REMOTE_DIR).replace("\\", "/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise WebdavError("远程目录不能包含 . 或 ..")
    return "/".join(parts)


def _validate_remote_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise WebdavError("远程备份文件名无效")
    return name


def _http_error(exc: urllib.error.HTTPError) -> WebdavError:
    messages = {
        401: "用户名或密码不正确（Nextcloud 请使用应用专用密码）",
        403: "服务器拒绝访问，请检查该账号对目录的写权限",
        404: "远程目录不存在",
        405: "服务器不支持该操作，请确认地址是 WebDAV 入口",
        507: "网盘空间不足",
    }
    return WebdavError(messages.get(exc.code, f"WebDAV 请求失败，HTTP {exc.code}"), status=exc.code)


def _network_detail(exc: BaseException | None) -> str:
    if exc is None:
        return "网络连接失败"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason or exc)
    return str(exc) or type(exc).__name__


def _report_progress(callback: Callable[[int], None] | None, current: int, total: int) -> None:
    if callback is None:
        return
    if total <= 0:
        callback(0)
        return
    callback(max(0, min(100, int(current * 100 / total))))


def _backup_stamp(name: str) -> str:
    match = BACKUP_NAME_RE.fullmatch(name)
    return match.group("stamp") if match else ""
