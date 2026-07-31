"""F1 / F2 验证：代理三态模式与 B 站请求层是否真的带上代理。"""

from __future__ import annotations

import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.config_service import (
    PROXY_MODE_AUTO,
    PROXY_MODE_MANUAL,
    PROXY_MODE_OFF,
    ConfigService,
)


def make_config(temp_dir: str, *, mode: str = PROXY_MODE_AUTO, proxy: str = "") -> ConfigService:
    root = Path(temp_dir)
    default_path = root / "default.json"
    default_path.write_text("", encoding="utf-8")
    config = ConfigService(default_path=default_path, user_path=root / "user.json")
    config.set("network.proxy_mode", mode)
    config.set("youtube.proxy", proxy)
    return config


class ProxyModeTests(unittest.TestCase):
    """F2：用户显式配置的代理不得被系统代理静默覆盖。"""

    def test_configured_proxy_beats_system_proxy_in_auto_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, mode=PROXY_MODE_AUTO, proxy="127.0.0.1:7890")
            with patch(
                "services.config_service.detect_system_proxy",
                return_value="http://10.0.0.1:8888",
            ):
                source, proxy = config.effective_proxy()

        self.assertEqual(source, "配置代理")
        self.assertEqual(proxy, "http://127.0.0.1:7890")

    def test_auto_mode_falls_back_to_system_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, mode=PROXY_MODE_AUTO, proxy="")
            with patch(
                "services.config_service.detect_system_proxy",
                return_value="http://10.0.0.1:8888",
            ):
                source, proxy = config.effective_proxy()

        self.assertEqual(source, "系统代理")
        self.assertEqual(proxy, "http://10.0.0.1:8888")

    def test_manual_mode_never_uses_system_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, mode=PROXY_MODE_MANUAL, proxy="")
            with patch(
                "services.config_service.detect_system_proxy",
                return_value="http://10.0.0.1:8888",
            ) as detect:
                source, proxy = config.effective_proxy()

        self.assertEqual(proxy, "")
        self.assertIn("手动模式", source)
        detect.assert_not_called()

    def test_off_mode_forces_direct_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, mode=PROXY_MODE_OFF, proxy="127.0.0.1:7890")
            with patch(
                "services.config_service.detect_system_proxy",
                return_value="http://10.0.0.1:8888",
            ) as detect:
                source, proxy = config.effective_proxy()

        self.assertEqual((source, proxy), ("强制直连", ""))
        detect.assert_not_called()

    def test_unknown_mode_degrades_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(temp_dir, mode="whatever")
            self.assertEqual(config.proxy_mode(), PROXY_MODE_AUTO)


class BilibiliProxyTests(unittest.TestCase):
    """F1：B 站请求层必须经 build_opener 带上 ProxyHandler。"""

    def _resolver(self, proxy: str):
        from resolver.site_resolver import BilibiliResolver

        config = SimpleNamespace(
            effective_proxy=lambda: (("配置代理", proxy) if proxy else ("未使用代理", "")),
        )
        return BilibiliResolver.__new__(BilibiliResolver), config

    def _proxy_handlers(self, opener: urllib.request.OpenerDirector) -> list[urllib.request.ProxyHandler]:
        return [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]

    def test_configured_proxy_is_installed_on_opener(self) -> None:
        resolver, config = self._resolver("http://127.0.0.1:7890")
        resolver.config = config

        handlers = self._proxy_handlers(resolver._build_opener())

        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].proxies.get("https"), "http://127.0.0.1:7890")
        self.assertEqual(handlers[0].proxies.get("http"), "http://127.0.0.1:7890")

    def test_direct_connection_ignores_environment_proxy(self) -> None:
        """无代理时显式给空映射，urllib 才不会回退去读环境变量里的 http_proxy。"""
        resolver, config = self._resolver("")
        resolver.config = config

        with patch.dict(
            "os.environ",
            {"http_proxy": "http://10.0.0.1:8888", "https_proxy": "http://10.0.0.1:8888"},
        ):
            opener = resolver._build_opener()

        # ProxyHandler({}) 不注册任何 *_open 方法，因此 build_opener 既不会装它，
        # 也不会补一个读环境变量的默认 ProxyHandler —— 结果就是真正的直连。
        for handler in self._proxy_handlers(opener):
            self.assertEqual(handler.proxies, {})

    def test_opener_is_rebuilt_per_call(self) -> None:
        """OpenerDirector 非线程安全，多个 worker 不能共用同一个实例。"""
        resolver, config = self._resolver("")
        resolver.config = config

        self.assertIsNot(resolver._build_opener(), resolver._build_opener())


if __name__ == "__main__":
    unittest.main()
