from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
THIRDPART_DIR = ROOT / "3rdpart"
VERSION_FILE = ROOT / "app_version.txt"
APP_NAME = "Tube_Ultimate_Player"


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def run_pyinstaller() -> None:
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
            "--add-data",
            f"{ROOT / 'resources'};resources",
            "--add-binary",
            f"{THIRDPART_DIR / 'libmpv-2.dll'};.",
            "--add-binary",
            f"{THIRDPART_DIR / 'libEGL.dll'};.",
            "--add-binary",
            f"{THIRDPART_DIR / 'libGLESv2.dll'};.",
            str(ROOT / "main.py"),
        ],
        check=True,
        cwd=ROOT,
    )


def build_installer(version: str) -> Path:
    iss_path = ROOT / "packaging" / "installer.iss"
    output_dir = DIST_DIR / "installer"
    output_dir.mkdir(parents=True, exist_ok=True)
    iscc = shutil.which("ISCC.exe")
    if not iscc:
        raise RuntimeError("未找到 Inno Setup 编译器 ISCC.exe")
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
        raise RuntimeError("安装包构建完成，但未找到输出文件")
    return installers[-1]


def main() -> int:
    version = read_version()
    run_pyinstaller()
    installer_path = build_installer(version)
    print(installer_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
