"""下载并发验证：DownloadManager 使用独立线程池，容量跟随「同时下载数」。

批量下载曾经只能同时跑 CPU 核数个任务：下载 worker 与解析/搜索共用全局线程池，
核数被占满后，设得更大的「同时下载数」永远排不到线程。改为专用线程池后，容量应等于配置值。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from download.download_manager import DownloadManager  # noqa: E402


class _FakeConfig:
    def __init__(self, max_concurrent: int) -> None:
        self._max_concurrent = max_concurrent

    def set_max_concurrent(self, value: int) -> None:
        self._max_concurrent = value

    # DownloadManager.__init__ 只会用到下面两个方法。
    def download_max_concurrent(self) -> int:
        return self._max_concurrent

    def download_dir(self) -> str:
        return str(Path(os.devnull).parent / "no_such_download_dir")

    def load(self) -> None:
        pass


class DownloadPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_owns_a_dedicated_pool_sized_to_max_concurrent(self) -> None:
        manager = DownloadManager(_FakeConfig(5))
        self.addCleanup(manager.shutdown)

        self.assertIsNot(manager.thread_pool, QThreadPool.globalInstance())
        self.assertEqual(manager.thread_pool.maxThreadCount(), 5)

    def test_reload_settings_resizes_the_pool(self) -> None:
        config = _FakeConfig(2)
        manager = DownloadManager(config)
        self.addCleanup(manager.shutdown)
        self.assertEqual(manager.thread_pool.maxThreadCount(), 2)

        config.set_max_concurrent(8)
        manager.reload_settings()

        self.assertEqual(manager.thread_pool.maxThreadCount(), 8)

    def test_injected_pool_is_left_untouched(self) -> None:
        # 兼容旧签名：显式传入线程池时不接管、不改容量。
        pool = QThreadPool()
        pool.setMaxThreadCount(3)
        manager = DownloadManager(_FakeConfig(9), pool)
        self.addCleanup(manager.shutdown)

        self.assertIs(manager.thread_pool, pool)
        self.assertEqual(pool.maxThreadCount(), 3)


if __name__ == "__main__":
    unittest.main()
