"""S4 验证：DLNA 中继只允许目标投屏设备取流，且 token 会过期。"""

from __future__ import annotations

import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dlna.media_server import DlnaMediaServer, DlnaMediaSource, _RegisteredSource


class AuthorizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.relay = DlnaMediaServer()
        self.source = DlnaMediaSource(title="Video", video_url="http://upstream/video")

    def _register(self, *, allowed_host: str = "192.168.1.20", ttl: float = 60.0) -> str:
        token = "token-under-test"
        with self.relay._sources_lock:
            self.relay._sources[token] = _RegisteredSource(
                source=self.source,
                allowed_host=allowed_host,
                expires_at=time.monotonic() + ttl,
            )
        return token

    def test_target_device_is_authorized(self) -> None:
        token = self._register()

        self.assertIs(self.relay.authorize(token, "192.168.1.20"), self.source)

    def test_other_lan_host_is_rejected(self) -> None:
        token = self._register()

        self.assertIsNone(self.relay.authorize(token, "192.168.1.99"))
        # 拒绝其他主机时不能顺手清掉登记，否则目标设备后续取流会失败。
        self.assertIs(self.relay.authorize(token, "192.168.1.20"), self.source)

    def test_unknown_token_is_rejected(self) -> None:
        self._register()

        self.assertIsNone(self.relay.authorize("not-a-token", "192.168.1.20"))

    def test_expired_token_is_rejected_and_evicted(self) -> None:
        token = self._register(ttl=-1.0)

        self.assertIsNone(self.relay.authorize(token, "192.168.1.20"))
        with self.relay._sources_lock:
            self.assertNotIn(token, self.relay._sources)

    def test_registering_a_new_source_invalidates_the_previous_token(self) -> None:
        old_token = self._register()
        with self.relay._sources_lock:
            self.relay._sources.clear()
            self.relay._sources["new-token"] = _RegisteredSource(
                source=self.source,
                allowed_host="192.168.1.20",
                expires_at=time.monotonic() + 60.0,
            )

        self.assertIsNone(self.relay.authorize(old_token, "192.168.1.20"))

    def test_successful_authorize_extends_the_deadline(self) -> None:
        # 滑动窗口：电视缓冲不足时会重新发起 GET，每次放行都要顺延有效期，
        # 否则长视频越过 TTL 后拿到 404 且不可恢复。
        token = self._register(ttl=5.0)
        with self.relay._sources_lock:
            before = self.relay._sources[token].expires_at

        self.assertIs(self.relay.authorize(token, "192.168.1.20"), self.source)

        with self.relay._sources_lock:
            after = self.relay._sources[token].expires_at
        self.assertGreater(after, before)

    def test_rejected_authorize_does_not_extend_the_deadline(self) -> None:
        token = self._register(ttl=5.0)
        with self.relay._sources_lock:
            before = self.relay._sources[token].expires_at

        self.assertIsNone(self.relay.authorize(token, "192.168.1.99"))

        with self.relay._sources_lock:
            self.assertEqual(self.relay._sources[token].expires_at, before)

    def test_stop_streams_invalidates_even_a_refreshed_token(self) -> None:
        token = self._register()
        self.relay.authorize(token, "192.168.1.20")

        self.relay.stop_streams()

        self.assertIsNone(self.relay.authorize(token, "192.168.1.20"))


class RelayHttpAccessTests(unittest.TestCase):
    """端到端：未授权来源与过期 token 都只能拿到 404。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        media = Path(self.temp.name) / "sample.mp3"
        media.write_bytes(b"0123456789")
        self.relay = DlnaMediaServer()
        self.addCleanup(self.relay.stop)
        self.relay._ensure_started("127.0.0.1", 0)
        self.port = self.relay._server.server_address[1]
        self.source = DlnaMediaSource(
            title="sample.mp3",
            video_url="",
            file_path=str(media),
            mime_type="audio/mpeg",
        )

    def _register(self, token: str, *, allowed_host: str, ttl: float = 60.0) -> None:
        with self.relay._sources_lock:
            self.relay._sources[token] = _RegisteredSource(
                source=self.source,
                allowed_host=allowed_host,
                expires_at=time.monotonic() + ttl,
            )

    def _fetch(self, token: str) -> int:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{self.port}/media/{token}", timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_allowed_client_can_stream(self) -> None:
        # 测试里请求来自 127.0.0.1，因此把它登记成目标设备。
        self._register("ok-token", allowed_host="127.0.0.1")

        self.assertEqual(self._fetch("ok-token"), 200)

    def test_foreign_client_gets_404(self) -> None:
        self._register("blocked-token", allowed_host="192.168.1.20")

        self.assertEqual(self._fetch("blocked-token"), 404)

    def test_expired_token_gets_404(self) -> None:
        self._register("stale-token", allowed_host="127.0.0.1", ttl=-1.0)

        self.assertEqual(self._fetch("stale-token"), 404)

    def test_unknown_token_gets_404(self) -> None:
        self.assertEqual(self._fetch("nope"), 404)


if __name__ == "__main__":
    unittest.main()
