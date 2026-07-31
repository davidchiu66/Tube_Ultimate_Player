# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Tube_Ultimate_Player is a Windows/Linux desktop video player and downloader for YouTube and Bilibili, built on **PySide6** (Qt), **yt-dlp** (resolving/downloading), and **libmpv** (playback via `ctypes`). Python 3.10+.

Note: much of the code, comments, docs, and commit messages are in Chinese. Match the surrounding language when editing user-facing strings and logs.

## Commands

```bash
pip install -r requirements.txt      # install deps (also needs yt-dlp + libmpv, see below)
python main.py                       # run the app
python -m unittest discover -s tests -p "test_*.py"   # run all tests
python -m unittest tests.test_playlist_page           # run a single test module
```

Build scripts (PyInstaller-based, run from repo root):
- `build_installer.py` — Windows installer
- `build_portable.py` — Windows portable (`--with-deno-ffmpeg` for the enhanced variant)
- `build_portable_with_deno_ffmpeg.py` — enhanced portable with bundled Deno/FFmpeg
- `build_linux.py` — Linux AppDir; official Linux release is via Fedora RPM/COPR (`.github/workflows/release-linux-rpm-copr.yml`)
- `tube-ultimate-player.spec` — PyInstaller spec

### Runtime binaries not in git

`3rdpart/libmpv-2.dll` and `3rdpart/yt-dlp.exe` are downloaded during release builds (libmpv from SourceForge, yt-dlp latest release) and are the two dependencies most likely to be missing in a fresh checkout. Playback and resolving will fail without them.

## Architecture

The app is a single `QMainWindow` (`ui/main_window.py`) that owns all services and swaps pages in a `QStackedWidget`. `main.py` only sets up Qt platform env, logging, icon/QSS, and constructs `MainWindow`.

**Layered structure — each directory is a layer, `main_window.py` wires them together:**

- `resolver/` — site resolving. `SiteResolver` is the dispatcher: it detects URL kind and routes to `YoutubeResolver` or `BilibiliResolver` (both live under this package; Bilibili logic including WBI signing is in `site_resolver.py`). Returns `resolver/models.py` dataclasses (`VideoInfo`, `HomeVideo`, `PlaylistInfo`, `VideoQuality`, etc.) that flow through the whole app.
- `player/mpv_player.py` — `MpvPlayer` loads `libmpv` via `ctypes`, embeds into a Qt widget's native window id, and exposes Qt Signals (`position_changed`, `pause_changed`, `playback_finished`, ...). Linux X11/XWayland embedding is why Wayland sessions are forced to `xcb` in `platform_support.py`.
- `download/` — `DownloadManager` runs a queue of `download_worker` tasks that shell out to yt-dlp (`command_builder.py` builds args). Tasks persist to `data/download_tasks.json`.
- `dlna/` — DLNA casting: SSDP `discovery`, SOAP `controller`, and `media_server` (local HTTP relay that live-remuxes split audio/video streams to MPEG-TS via FFmpeg without re-encoding).
- `database/` — SQLite via `sqlite_manager.py` + repository classes (`FavoriteRepository`, `HistoryRepository`, `PlaylistRepository`). Repos auto-migrate missing columns on startup.
- `services/` — `ConfigService` (JSON config, see below), plus cookie, logging, shortcut, update, and FFmpeg/JS-runtime install services.
- `workers/` — all blocking work (resolve, search, home load, playlist build, DLNA actions, update download) runs off the UI thread here. See threading pattern below.
- `ui/` — one file per page/component; `main_window.py` connects worker signals to page slots.

### Threading model

Never block the Qt UI thread. Long operations are `QRunnable` subclasses in `workers/`, submitted to `QThreadPool`. Each worker owns a `WorkerSignals(QObject)` with `success(object)` / `error(str)` / `finished()` signals; `main_window.py` connects these to slots. Follow this exact pattern (see `workers/resolver_worker.py`) when adding background work rather than calling resolver/download/network code directly from UI slots.

### Config and runtime paths

`app_paths.py` is the single source of truth for filesystem locations and handles frozen (PyInstaller) vs. source runs, plus Windows (`%LocalAppData%\Tube_Ultimate_Player`) vs. Linux (XDG dirs). Import paths from here — do not hardcode. `ensure_runtime_dirs()` (called first in `main`) creates them, falling back to a writable dir (often project-local `runtime/`) when the preferred location is not writable.

`ConfigService` merges `config/default_config.json` (template, in git) over `user_config.json` (runtime, gitignored). Read/write config through this service; it also owns default home source, per-site cookies, and shortcut config (`DEFAULT_SHORTCUTS` from `shortcut_service.py`).

### Versioning

`app_version.txt` at repo root is the release source of truth (not `pyproject.toml`, which can lag). The release workflow requires a matching `docs/releases/v<version>.md` to exist before building.

## Conventions

- All modules start with `from __future__ import annotations` and use modern type hints.
- Logging uses named loggers (`logging.getLogger("tube_player.<area>")`), lazy `%`-formatting, and `logger.exception` in worker `except` blocks. Match this.
- Never commit user data: `cookie_*.txt`, `config/user_config.json`, `logs/`, `downloads/`, `data/`, `runtime/`, `updates/` are gitignored.
