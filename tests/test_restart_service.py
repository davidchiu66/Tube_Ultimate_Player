from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from services.restart_service import RestartError, restart_application, restart_command


class RestartServiceTests(unittest.TestCase):
    def test_source_command_includes_script_and_existing_args(self) -> None:
        with patch.object(sys, "argv", ["main.py", "--flag"]), patch.object(sys, "frozen", False, create=True):
            command = restart_command(["--extra"])
        self.assertEqual(command, [sys.executable, "main.py", "--flag", "--extra"])

    def test_frozen_command_uses_executable_directly(self) -> None:
        with patch.object(sys, "argv", ["app.exe", "--flag"]), patch.object(sys, "frozen", True, create=True):
            command = restart_command()
        self.assertEqual(command, [sys.executable, "--flag"])

    def test_empty_script_argument_uses_runtime_fallback(self) -> None:
        with patch.object(sys, "argv", [""]), patch.object(sys, "frozen", False, create=True):
            command = restart_command()
        self.assertEqual(command[1], str(__import__("app_paths").RUNTIME_ROOT / "main.py"))

    def test_popen_failure_is_readable(self) -> None:
        with patch("services.restart_service.subprocess.Popen", side_effect=OSError("denied")):
            with self.assertRaisesRegex(RestartError, "请手动重启应用"):
                restart_application()

    def test_windows_process_is_detached(self) -> None:
        with (
            patch("services.restart_service.subprocess.Popen") as popen,
            patch("services.restart_service.sys.platform", "win32"),
            patch.object(sys, "argv", ["main.py"]),
            patch.object(sys, "frozen", False, create=True),
        ):
            restart_application()
        kwargs = popen.call_args.kwargs
        self.assertEqual(
            kwargs["creationflags"],
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
        self.assertTrue(kwargs["close_fds"])


if __name__ == "__main__":
    unittest.main()
