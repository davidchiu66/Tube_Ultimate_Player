from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
PORTABLE_DIR = DIST_DIR / "Tube_Ultimate_Player_Portable"
THIRDPART_DIR = ROOT / "3rdpart"
VERSION_FILE = ROOT / "app_version.txt"
RELEASE_NAME = "Tube_Ultimate_Player"


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
            RELEASE_NAME,
            "--windowed",
            "--add-data",
            f"{ROOT / 'resources'};resources",
            "--add-data",
            f"{ROOT / 'config'};config",
            str(ROOT / "main.py"),
        ],
        check=True,
        cwd=ROOT,
    )


def _copy_tree_contents(source_dir: Path, target_dir: Path) -> None:
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)


def assemble_portable(version: str) -> Path:
    bundle_dir = DIST_DIR / RELEASE_NAME
    if PORTABLE_DIR.exists():
        shutil.rmtree(PORTABLE_DIR)
    PORTABLE_DIR.mkdir(parents=True, exist_ok=True)

    _copy_tree_contents(bundle_dir, PORTABLE_DIR)
    shutil.copytree(ROOT / "3rdpart", PORTABLE_DIR / "3rdpart", dirs_exist_ok=True)
    shutil.copy2(ROOT / "README.md", PORTABLE_DIR / "README.md")
    shutil.copy2(ROOT / "app_version.txt", PORTABLE_DIR / "app_version.txt")

    zip_path = DIST_DIR / f"Tube_Ultimate_Player_portable_v{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in PORTABLE_DIR.rglob("*"):
            archive.write(path, path.relative_to(PORTABLE_DIR))
    return zip_path


def main() -> int:
    version = read_version()
    run_pyinstaller()
    zip_path = assemble_portable(version)
    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
