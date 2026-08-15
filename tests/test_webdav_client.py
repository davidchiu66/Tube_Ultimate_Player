from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path

from services.webdav_client import WebdavAccount, WebdavClient, WebdavError, clear_proxy_preferences


class _Response:
    def __init__(self, body: bytes = b"", headers: dict | None = None) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Opener:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


class _Config:
    def __init__(self, proxy: str = "") -> None:
        self.proxy = proxy

    def effective_proxy(self):
        return ("配置代理", self.proxy) if self.proxy else ("未使用代理", "")


class WebdavClientTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_proxy_preferences()
        self.account = WebdavAccount("a1", "NAS", "https://example.test/dav/", "me", "pw", "backups")

    def test_network_failures_retry_three_times_then_use_proxy(self) -> None:
        direct = _Opener([urllib.error.URLError("offline") for _ in range(3)])
        proxy = _Opener([_Response(), _Response()])
        client = WebdavClient(
            self.account,
            _Config("http://127.0.0.1:7890"),
            direct_opener=direct,
            proxy_opener=proxy,
            sleep=lambda _seconds: None,
        )

        message = client.test_connection()

        self.assertEqual(len(direct.requests), 3)
        self.assertEqual(len(proxy.requests), 1)
        self.assertIn("经代理", message)

        client.test_connection()
        self.assertEqual(len(direct.requests), 3)
        self.assertEqual(len(proxy.requests), 2)

    def test_http_error_is_not_retried_or_proxied(self) -> None:
        error = urllib.error.HTTPError("https://example.test", 401, "Unauthorized", {}, None)
        direct = _Opener([error])
        proxy = _Opener([_Response()])
        client = WebdavClient(
            self.account,
            _Config("http://127.0.0.1:7890"),
            direct_opener=direct,
            proxy_opener=proxy,
            sleep=lambda _seconds: None,
        )

        with self.assertRaisesRegex(WebdavError, "用户名或密码"):
            client.test_connection()
        self.assertEqual(len(direct.requests), 1)
        self.assertEqual(len(proxy.requests), 0)

    def test_list_backups_filters_and_sorts(self) -> None:
        xml = b'''<?xml version="1.0"?>
        <D:multistatus xmlns:D="DAV:">
          <D:response><D:href>/dav/backups/tube-backup-0.2.25-20260814-120000.zip</D:href><D:propstat><D:prop><D:getcontentlength>20</D:getcontentlength><D:getlastmodified>today</D:getlastmodified></D:prop></D:propstat></D:response>
          <D:response><D:href>/dav/backups/notes.txt</D:href><D:propstat><D:prop><D:getcontentlength>4</D:getcontentlength></D:prop></D:propstat></D:response>
          <D:response><D:href>/dav/backups/tube-backup-0.2.25-20260814-130000.zip</D:href><D:propstat><D:prop><D:getcontentlength>30</D:getcontentlength></D:prop></D:propstat></D:response>
        </D:multistatus>'''
        opener = _Opener([_Response(xml)])
        client = WebdavClient(self.account, _Config(), direct_opener=opener, sleep=lambda _: None)

        backups = client.list_backups()

        self.assertEqual([item.size for item in backups], [30, 20])
        request = opener.requests[0][0]
        self.assertEqual(request.get_method(), "PROPFIND")
        self.assertEqual(request.get_header("Depth"), "1")
        self.assertEqual(request.full_url, "https://example.test/dav/backups/")

    def test_failed_upload_attempts_best_effort_remote_cleanup(self) -> None:
        class Client(WebdavClient):
            deleted: list[str] = []

            def delete(self, remote_name: str) -> None:
                self.deleted.append(remote_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "backup.zip"
            local.write_bytes(b"backup")
            opener = _Opener([urllib.error.URLError("broken") for _ in range(3)])
            client = Client(self.account, _Config(), direct_opener=opener, sleep=lambda _: None)
            with self.assertRaises(Exception):
                client.upload(local, "tube-backup-0.2.25-20260814-120000.zip")
            self.assertEqual(client.deleted, ["tube-backup-0.2.25-20260814-120000.zip"])

    def test_http_upload_failure_does_not_attempt_cleanup(self) -> None:
        class Client(WebdavClient):
            deleted: list[str] = []

            def delete(self, remote_name: str) -> None:
                self.deleted.append(remote_name)

        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "backup.zip"
            local.write_bytes(b"backup")
            error = urllib.error.HTTPError("https://example.test", 403, "Forbidden", {}, None)
            opener = _Opener([error])
            client = Client(self.account, _Config("http://127.0.0.1:7890"), direct_opener=opener, sleep=lambda _: None)
            with self.assertRaisesRegex(Exception, "服务器拒绝访问"):
                client.upload(local, "tube-backup-0.2.25-20260814-120000.zip")
            self.assertEqual(client.deleted, [])


if __name__ == "__main__":
    unittest.main()
