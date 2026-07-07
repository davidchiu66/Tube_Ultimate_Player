from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Tube_Ultimate_Player"
BASE_DIR = Path(__file__).resolve().parent
THIRDPART_DIR = BASE_DIR / "3rdpart"
RESOURCE_DIR = BASE_DIR / "resources"
DEFAULT_CONFIG_DIR = BASE_DIR / "config"


def _runtime_root_candidates() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / APP_NAME)
    candidates.append(Path.home() / f".{APP_NAME}")
    candidates.append(BASE_DIR / "runtime")
    return candidates


def _runtime_root() -> Path:
    for candidate in _runtime_root_candidates():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return BASE_DIR / "runtime"


RUNTIME_ROOT = _runtime_root()
CONFIG_DIR = RUNTIME_ROOT / "config"
CACHE_DIR = RUNTIME_ROOT / "cache"
DATA_DIR = RUNTIME_ROOT / "data"
LOG_DIR = RUNTIME_ROOT / "logs"
DOWNLOAD_DIR = RUNTIME_ROOT / "downloads"


def ensure_runtime_dirs() -> None:
    for path in (
        RUNTIME_ROOT,
        CONFIG_DIR,
        CACHE_DIR,
        DATA_DIR,
        CACHE_DIR / "cookies",
        LOG_DIR,
        DOWNLOAD_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def thirdpart_path(name: str) -> Path:
    return THIRDPART_DIR / name


def default_config_path(name: str = "default_config.json") -> Path:
    return DEFAULT_CONFIG_DIR / name


def runtime_path(*parts: str) -> Path:
    return RUNTIME_ROOT.joinpath(*parts)


def add_thirdpart_dll_directory() -> None:
    if not THIRDPART_DIR.exists():
        return

    if hasattr(os, "add_dll_directory") and sys.platform.startswith("win"):
        os.add_dll_directory(str(THIRDPART_DIR))

    current = os.environ.get("PATH", "")
    thirdpart = str(THIRDPART_DIR)
    if thirdpart not in current.split(os.pathsep):
        os.environ["PATH"] = thirdpart + os.pathsep + current
