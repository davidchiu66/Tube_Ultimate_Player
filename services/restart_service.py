from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app_paths import RUNTIME_ROOT

class RestartError(RuntimeError):
    pass


def restart_command(extra_args: list[str] | None = None) -> list[str]:
    extras = list(extra_args or [])
    current_args = list(sys.argv[1:])
    if getattr(sys, "frozen", False):
        return [sys.executable, *current_args, *extras]
    script = sys.argv[0] or str(RUNTIME_ROOT / "main.py")
    return [sys.executable, script, *current_args, *extras]


def restart_application(extra_args: list[str] | None = None) -> None:
    command = restart_command(extra_args)
    executable = Path(command[0]).resolve()
    script_or_exe = executable if getattr(sys, "frozen", False) else Path(command[1]).resolve()
    kwargs = {
        "cwd": str(script_or_exe.parent),
        "close_fds": True,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError as exc:
        raise RestartError(f"自动重启失败，请手动重启应用：{exc}") from exc
