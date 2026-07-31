"""测试包初始化：把运行目录重定向到临时目录。

必须在任何项目模块之前执行 —— app_paths 在 import 期就根据 %LocalAppData% /
%AppData%（Linux 下是 XDG 目录）算出所有运行时路径。不重定向的话，构造真实
MainWindow / ConfigService / DownloadManager 的用例会写到用户**真实**的
user_config.json、download_tasks.json 和 cookie_*.txt 上。

这不是假想的风险：曾经有一个用例把占位 Cookie（Cookie: SID=abc）写进了真实的
cookie_youtube.txt，直接导致 YouTube 首页拉不到内容。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path


_SANDBOX = Path(tempfile.mkdtemp(prefix="tube_player_tests_"))

for _name in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
    _target = _SANDBOX / _name.lower()
    _target.mkdir(parents=True, exist_ok=True)
    os.environ[_name] = str(_target)

# HOME 也一并指过去：Linux 分支与浏览器 Cookie 探测都会用到它。
os.environ["HOME"] = str(_SANDBOX / "home")
(_SANDBOX / "home").mkdir(parents=True, exist_ok=True)


@atexit.register
def _cleanup_sandbox() -> None:
    shutil.rmtree(_SANDBOX, ignore_errors=True)
