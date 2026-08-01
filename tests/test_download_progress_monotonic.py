"""下载进度必须单调递增。

真实现象：下载 YouTube 视频时进度条每秒从起点跳到当前进度。原因是两个进度源
同时往界面推 —— yt-dlp --progress-template 给的是**单文件**百分比（视频+音频
会跑两轮 0→100），按磁盘字节算的估算器给的是整体百分比。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from download.download_worker import DownloadWorker
from download.models import DownloadTask
from services.config_service import ConfigService


class ProgressMonotonicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        default_path = root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=root / "user.json")
        self.task = DownloadTask(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            title="t",
            video_id="abcdefghijk",
            source_site="youtube",
            save_dir=str(root),
        )

    def _worker(self, expected_bytes: int | None):
        self.task.expected_bytes = expected_bytes
        worker = DownloadWorker(self.task, self.config)
        seen: list[float] = []
        worker.signals.progress.connect(lambda _tid, percent, _s, _e: seen.append(percent))
        return worker, seen

    def _feed(self, worker, percent: float, speed: str = "1MiB/s", eta: str = "00:10") -> None:
        worker._handle_output_line(f"progress:{percent:.1f}%|{speed}|{eta}", [], "")

    def test_per_file_percent_does_not_move_the_bar_when_total_is_known(self) -> None:
        worker, seen = self._worker(expected_bytes=1000)

        self._feed(worker, 100.0)   # 视频文件下完
        self._feed(worker, 0.0)     # 音频文件开始 —— 旧实现在这里掉回 0
        self._feed(worker, 50.0)

        self.assertEqual(seen, [0.0, 0.0, 0.0])
        # 速度和剩余时间仍然被刷新。
        self.assertEqual(self.task.speed_text, "1MiB/s")
        self.assertEqual(self.task.eta_text, "00:10")

    def test_byte_based_progress_drives_the_bar(self) -> None:
        worker, seen = self._worker(expected_bytes=1000)

        worker._publish_progress(40.0, "", "")
        self._feed(worker, 0.0)     # 单文件百分比归零也不能把进度条拉回去
        worker._publish_progress(70.0, "", "")

        self.assertEqual(seen, [40.0, 40.0, 70.0])

    def test_progress_never_goes_backwards(self) -> None:
        worker, seen = self._worker(expected_bytes=1000)

        for value in (10.0, 35.0, 20.0, 35.0, 99.5, 80.0, 100.0):
            worker._publish_progress(value, "", "")

        self.assertEqual(seen, [10.0, 35.0, 35.0, 35.0, 99.5, 99.5, 100.0])

    def test_percent_is_clamped_to_100(self) -> None:
        worker, seen = self._worker(expected_bytes=1000)

        worker._publish_progress(140.0, "", "")

        self.assertEqual(seen, [100.0])

    def test_template_percent_is_used_when_total_is_unknown(self) -> None:
        worker, seen = self._worker(expected_bytes=None)

        self._feed(worker, 12.0)
        self._feed(worker, 48.0)
        self._feed(worker, 0.0)     # 第二个文件重新开始，但进度条不后退

        self.assertEqual(seen, [12.0, 48.0, 48.0])

    def test_speed_and_eta_are_kept_when_a_line_omits_them(self) -> None:
        worker, _seen = self._worker(expected_bytes=1000)

        self._feed(worker, 10.0, speed="2MiB/s", eta="00:05")
        worker._publish_progress(20.0, "", "")

        self.assertEqual(self.task.speed_text, "2MiB/s")
        self.assertEqual(self.task.eta_text, "00:05")


if __name__ == "__main__":
    unittest.main()
