"""E6 验证：「播放 URL」面板的历史记录。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from services.config_service import RECENT_URL_LIMIT, ConfigService


class RecentUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        default_path = Path(self.temp.name) / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.user_path = Path(self.temp.name) / "user.json"
        self.config = ConfigService(default_path=default_path, user_path=self.user_path)

    def test_recorded_url_is_first(self) -> None:
        self.config.add_recent_url("https://a.example/1")
        self.config.add_recent_url("https://b.example/2")

        urls = [item["url"] for item in self.config.recent_urls()]
        self.assertEqual(urls, ["https://b.example/2", "https://a.example/1"])

    def test_duplicate_is_deduped_and_promoted(self) -> None:
        self.config.add_recent_url("https://a.example/1")
        self.config.add_recent_url("https://b.example/2")
        self.config.add_recent_url("https://a.example/1/")  # 末尾斜杠归一化后视为同一条

        urls = [item["url"] for item in self.config.recent_urls()]
        self.assertEqual(urls, ["https://a.example/1/", "https://b.example/2"])

    def test_limit_evicts_oldest(self) -> None:
        for index in range(RECENT_URL_LIMIT + 5):
            self.config.add_recent_url(f"https://example.com/{index}")

        entries = self.config.recent_urls()
        self.assertEqual(len(entries), RECENT_URL_LIMIT)
        self.assertEqual(entries[0]["url"], f"https://example.com/{RECENT_URL_LIMIT + 4}")

    def test_title_backfill(self) -> None:
        self.config.add_recent_url("https://a.example/1")
        self.config.update_recent_url_title("https://a.example/1", "示例标题")

        self.assertEqual(self.config.recent_urls()[0]["title"], "示例标题")

    def test_remove_and_clear(self) -> None:
        self.config.add_recent_url("https://a.example/1")
        self.config.add_recent_url("https://b.example/2")

        self.config.remove_recent_url("https://a.example/1")
        self.assertEqual([i["url"] for i in self.config.recent_urls()], ["https://b.example/2"])

        self.config.clear_recent_urls()
        self.assertEqual(self.config.recent_urls(), [])

    def test_missing_key_is_tolerated(self) -> None:
        self.assertEqual(self.config.recent_urls(), [])

    def test_dirty_entries_are_ignored(self) -> None:
        self.user_path.write_text(
            json.dumps({"player": {"recent_urls": [{"title": "无 url"}, "字符串", {"url": "https://ok.example"}]}}),
            encoding="utf-8",
        )
        self.config.load()

        self.assertEqual([i["url"] for i in self.config.recent_urls()], ["https://ok.example"])


if __name__ == "__main__":
    unittest.main()
