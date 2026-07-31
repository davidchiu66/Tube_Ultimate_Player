"""回归守卫：Cookie 文件写入路径不得为空，写入失败不得吞掉其余设置。

真实故障：磁盘上留着 0 字节的 cookie_youtube.txt 时，_cookie_file_path 经
cookie_file() 取值拿到空串，Path("") 解析成当前目录，write_text 报
PermissionError: '.'，save() 在写 content.default_home 之前就中断 ——
表现为「默认首页配了 bilibili 却显示 YouTube」「点首页不刷新」。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from services.config_service import ConfigService  # noqa: E402
from ui.settings_page import SettingsPage  # noqa: E402


class CookieWritePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")

    def test_path_query_survives_an_empty_cookie_file(self) -> None:
        empty = self.root / "cookie_youtube.txt"
        empty.write_text("", encoding="utf-8")
        self.config.set("cookies.youtube.file", str(empty))

        # cookie_file 回答「能不能用」→ 空；cookie_file_path 回答「写哪里」→ 仍是该路径。
        self.assertEqual(self.config.cookie_file("youtube"), "")
        self.assertEqual(self.config.cookie_file_path("youtube"), str(empty))

    def test_path_query_never_returns_empty(self) -> None:
        for site in ("youtube", "bilibili"):
            value = self.config.cookie_file_path(site)
            self.assertTrue(value)
            self.assertNotEqual(Path(value), Path("."))


class SettingsSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")
        for site in ("youtube", "bilibili"):
            empty = self.root / f"cookie_{site}.txt"
            empty.write_text("", encoding="utf-8")
            self.config.set(f"cookies.{site}.file", str(empty))
        self.config.set("download.save_dir", str(self.root / "downloads"))
        # SettingsPage.load() 会 config.load() 重新读盘，内存里的 set 会被丢掉；
        # 不先落盘，写入路径就会退回 default_cookie_file()，那是**真实**运行目录。
        self.config.save()
        # 双保险：即使某条路径没配置，兜底路径也必须留在临时目录里，
        # 单测绝不能碰用户真实的 Cookie 文件。
        patcher = patch.object(
            ConfigService,
            "default_cookie_file",
            lambda _self, site="": str(self.root / f"default_cookie_{site or 'unknown'}.txt"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _page(self) -> SettingsPage:
        page = SettingsPage(self.config)
        self.addCleanup(page.deleteLater)
        return page

    def test_default_home_is_persisted_with_empty_cookie_files(self) -> None:
        page = self._page()
        emitted: list[bool] = []
        page.settings_saved.connect(lambda: emitted.append(True))

        page.default_home_youtube.setChecked(True)
        page.default_home_bilibili.setChecked(False)
        page.save()

        self.assertEqual(self.config.get("content.default_home"), "youtube")
        self.assertEqual(emitted, [True])

    def test_switching_back_also_persists(self) -> None:
        page = self._page()

        page.default_home_youtube.setChecked(True)
        page.default_home_bilibili.setChecked(False)
        page.save()
        page.default_home_youtube.setChecked(False)
        page.default_home_bilibili.setChecked(True)
        page.save()

        self.assertEqual(self.config.get("content.default_home"), "bilibili")

    def test_cookie_files_are_written_to_their_configured_paths(self) -> None:
        page = self._page()
        page._cookie_texts["youtube"] = "Cookie: SID=abc"

        page.save()

        written = Path(self.config.cookie_file_path("youtube"))
        self.assertEqual(written.read_text(encoding="utf-8"), "Cookie: SID=abc")
        # 写入后内容非空，cookie_file 又能重新把它当作可用的 Cookie 文件。
        self.assertEqual(self.config.cookie_file("youtube"), str(written))


class RealPathIsolationTests(unittest.TestCase):
    """守卫：SettingsPage 的 Cookie 写入不得落在真实运行目录。

    曾经真实发生过 —— 用例只在内存里 set 了 cookies.<site>.file，而
    SettingsPage.load() 会 config.load() 重新读盘把它丢掉，写入路径于是退回
    default_cookie_file()，把用户真实的 cookie_youtube.txt 覆盖成了用例的占位值。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_in_memory_config_survives_the_page_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            default_path = root / "default.json"
            default_path.write_text("{}", encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=root / "user.json")
            target = root / "cookie_youtube.txt"
            config.set("cookies.youtube.file", str(target))
            config.save()

            page = SettingsPage(config)
            self.addCleanup(page.deleteLater)

            # 走完一次 load() 之后，配置里仍然是临时路径。
            self.assertEqual(Path(config.cookie_file_path("youtube")), target)
            self.assertEqual(page._cookie_file_path("youtube", for_write=True), target)


if __name__ == "__main__":
    unittest.main()
