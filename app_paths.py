from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "Tube_Ultimate_Player"
SOURCE_DIR = Path(__file__).resolve().parent


def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return SOURCE_DIR


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_DIR


def _pick_existing(*candidates: Path) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


BUNDLE_DIR = _bundle_dir()
APP_DIR = _app_dir()
BASE_DIR = APP_DIR
THIRDPART_DIR = _pick_existing(APP_DIR / "3rdpart", BUNDLE_DIR / "3rdpart", SOURCE_DIR / "3rdpart")
RESOURCE_DIR = _pick_existing(APP_DIR / "resources", BUNDLE_DIR / "resources", SOURCE_DIR / "resources")
DEFAULT_CONFIG_DIR = _pick_existing(APP_DIR / "config", BUNDLE_DIR / "config", SOURCE_DIR / "config")
ASSET_DIR = _pick_existing(APP_DIR / "docs" / "assets", BUNDLE_DIR / "docs" / "assets", SOURCE_DIR / "docs" / "assets")


@dataclass(frozen=True, slots=True)
class RuntimeDirectories:
    root: Path
    config: Path
    cache: Path
    data: Path
    logs: Path
    downloads: Path
    updates: Path


def linux_runtime_directories(
    environ: dict[str, str] | None = None,
    home: Path | None = None,
) -> RuntimeDirectories:
    env = environ if environ is not None else os.environ
    user_home = home if home is not None else Path.home()
    config_home = _absolute_env_path(env, "XDG_CONFIG_HOME", user_home / ".config")
    data_home = _absolute_env_path(env, "XDG_DATA_HOME", user_home / ".local" / "share")
    cache_home = _absolute_env_path(env, "XDG_CACHE_HOME", user_home / ".cache")
    state_home = _absolute_env_path(env, "XDG_STATE_HOME", user_home / ".local" / "state")
    videos_home = _linux_videos_home(env, user_home, config_home)

    data = data_home / APP_NAME
    cache = cache_home / APP_NAME
    return RuntimeDirectories(
        root=data,
        config=config_home / APP_NAME,
        cache=cache,
        data=data,
        logs=state_home / APP_NAME / "logs",
        downloads=videos_home / APP_NAME,
        updates=cache / "updates",
    )


def _absolute_env_path(environ: dict[str, str], key: str, fallback: Path) -> Path:
    raw = environ.get(key, "").strip()
    if not raw:
        return fallback
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() or raw.startswith("/") else fallback


def _linux_videos_home(environ: dict[str, str], home: Path, config_home: Path) -> Path:
    configured = environ.get("XDG_VIDEOS_DIR", "").strip()
    if not configured:
        user_dirs = config_home / "user-dirs.dirs"
        try:
            for raw_line in user_dirs.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line.startswith("XDG_VIDEOS_DIR="):
                    continue
                configured = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        except OSError:
            pass
    if configured:
        configured = configured.replace("${HOME}", str(home)).replace("$HOME", str(home))
        candidate = Path(configured).expanduser()
        if candidate.is_absolute() or configured.startswith("/"):
            return candidate
    return home / "Videos"


def _runtime_root_candidates() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            candidates.append(Path(local_app_data) / APP_NAME)
    candidates.append(Path.home() / f".{APP_NAME}")
    candidates.append(APP_DIR / "runtime")
    candidates.append(SOURCE_DIR / "runtime")
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
    return SOURCE_DIR / "runtime"


if sys.platform.startswith("linux"):
    _RUNTIME_DIRS = linux_runtime_directories()
    RUNTIME_ROOT = _RUNTIME_DIRS.root
    CONFIG_DIR = _RUNTIME_DIRS.config
    CACHE_DIR = _RUNTIME_DIRS.cache
    DATA_DIR = _RUNTIME_DIRS.data
    LOG_DIR = _RUNTIME_DIRS.logs
    DOWNLOAD_DIR = _RUNTIME_DIRS.downloads
    UPDATE_DIR = _RUNTIME_DIRS.updates
else:
    RUNTIME_ROOT = _runtime_root()
    CONFIG_DIR = RUNTIME_ROOT / "config"
    CACHE_DIR = RUNTIME_ROOT / "cache"
    DATA_DIR = RUNTIME_ROOT / "data"
    LOG_DIR = RUNTIME_ROOT / "logs"
    DOWNLOAD_DIR = RUNTIME_ROOT / "downloads"
    UPDATE_DIR = RUNTIME_ROOT / "updates"


def ensure_runtime_dirs() -> None:
    for path in (
        RUNTIME_ROOT,
        CONFIG_DIR,
        CACHE_DIR,
        DATA_DIR,
        CACHE_DIR / "cookies",
        LOG_DIR,
        DOWNLOAD_DIR,
        UPDATE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def thirdpart_path(name: str) -> Path:
    return THIRDPART_DIR / name


def default_config_path(name: str = "default_config.json") -> Path:
    return DEFAULT_CONFIG_DIR / name


def resource_path(*parts: str) -> Path:
    return RESOURCE_DIR.joinpath(*parts)


def asset_path(*parts: str) -> Path:
    return ASSET_DIR.joinpath(*parts)


def runtime_path(*parts: str) -> Path:
    return RUNTIME_ROOT.joinpath(*parts)


def app_version_path() -> Path:
    candidates = (
        APP_DIR / "app_version.txt",
        SOURCE_DIR / "app_version.txt",
        BUNDLE_DIR / "app_version.txt",
    )
    return _pick_existing(*candidates)


def read_app_version(default: str = "0.0.0-dev") -> str:
    path = app_version_path()
    try:
        return path.read_text(encoding="utf-8").strip() or default
    except OSError:
        return default


def add_thirdpart_dll_directory() -> None:
    if not THIRDPART_DIR.exists():
        return

    if hasattr(os, "add_dll_directory") and sys.platform.startswith("win"):
        os.add_dll_directory(str(THIRDPART_DIR))

    current = os.environ.get("PATH", "")
    thirdpart = str(THIRDPART_DIR)
    if thirdpart not in current.split(os.pathsep):
        os.environ["PATH"] = thirdpart + os.pathsep + current
