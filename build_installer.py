from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
VERSION_FILE = ROOT / "app_version.txt"
APP_NAME = "Tube_Ultimate_Player"
THIRDPART_DIR = ROOT / "3rdpart"
MIN_DLL_SIZE = 10 * 1024 * 1024
ICON_FILE = ROOT / "docs" / "assets" / "icons" / "app-icon.ico"


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def run_pyinstaller() -> None:
    validate_thirdpart_binaries()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--name",
            APP_NAME,
            "--windowed",
            "--icon",
            str(ICON_FILE),
            "--add-data",
            f"{ROOT / 'resources'};resources",
            "--add-data",
            f"{ROOT / 'config'};config",
            "--add-data",
            f"{ROOT / 'docs' / 'assets' / 'icons'};docs/assets/icons",
            str(ROOT / "main.py"),
        ],
        check=True,
        cwd=ROOT,
    )


def validate_thirdpart_binaries() -> None:
    required = (
        "libmpv-2.dll",
        "libEGL.dll",
        "libGLESv2.dll",
    )
    for name in required:
        path = THIRDPART_DIR / name
        if not path.exists():
            raise RuntimeError(f"Missing required third-party binary: {path}")
        size = path.stat().st_size
        if name == "libmpv-2.dll" and size < MIN_DLL_SIZE:
            raise RuntimeError(
                f"{path} is unexpectedly small ({size} bytes). "
                "Git LFS content was likely not downloaded."
            )


def build_installer(version: str) -> Path:
    iss_path = ROOT / "packaging" / "installer.iss"
    output_dir = DIST_DIR / "installer"
    output_dir.mkdir(parents=True, exist_ok=True)
    iscc = shutil.which("ISCC.exe")
    if not iscc:
        raise RuntimeError("Inno Setup compiler ISCC.exe was not found")
    subprocess.run(
        [
            iscc,
            f"/DAppVersion={version}",
            f"/DProjectRoot={ROOT}",
            f"/DOutputDir={output_dir}",
            str(iss_path),
        ],
        check=True,
        cwd=ROOT,
    )
    installers = sorted(output_dir.glob("*.exe"))
    if not installers:
        raise RuntimeError("Installer build finished but no output .exe was found")
    return installers[-1]


def main() -> int:
    version = read_version()
    run_pyinstaller()
    installer_path = build_installer(version)
    print(installer_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
