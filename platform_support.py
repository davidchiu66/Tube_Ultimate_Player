from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping


LINUX_QPA_OVERRIDE_ENV = "TUBE_PLAYER_QPA_PLATFORM"


def is_windows(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform).startswith("win")


def is_linux(platform_name: str | None = None) -> bool:
    return (platform_name or sys.platform).startswith("linux")


def configure_qt_platform_environment(
    environ: MutableMapping[str, str] | None = None,
    platform_name: str | None = None,
) -> str:
    """Select the supported Qt display backend before importing PySide6.

    The current libmpv integration embeds into an X11 window id.  Under a
    Wayland desktop session the first Linux release therefore runs Qt through
    xcb/XWayland unless the user explicitly selected another QPA backend.
    """

    env = environ if environ is not None else os.environ
    if not is_linux(platform_name):
        return env.get("QT_QPA_PLATFORM", "")

    if env.get("QT_QPA_PLATFORM", "").strip():
        return env["QT_QPA_PLATFORM"].strip()

    override = env.get(LINUX_QPA_OVERRIDE_ENV, "").strip()
    if override:
        env["QT_QPA_PLATFORM"] = override
    elif env.get("WAYLAND_DISPLAY", "").strip():
        env["QT_QPA_PLATFORM"] = "xcb"

    return env.get("QT_QPA_PLATFORM", "")


def is_root_user(platform_name: str | None = None) -> bool:
    if not is_linux(platform_name):
        return False
    getuid = getattr(os, "geteuid", None) or getattr(os, "getuid", None)
    if getuid is None:
        return False
    try:
        return int(getuid()) == 0
    except OSError:
        return False


def linux_session_type(environ: MutableMapping[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    declared = env.get("XDG_SESSION_TYPE", "").strip().lower()
    if declared:
        return declared
    if env.get("WAYLAND_DISPLAY", "").strip():
        return "wayland"
    if env.get("DISPLAY", "").strip():
        return "x11"
    return "unknown"
