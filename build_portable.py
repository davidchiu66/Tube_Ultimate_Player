from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
PORTABLE_DIR = DIST_DIR / "Tube_Ultimate_Player_Portable"
ENHANCED_PORTABLE_DIR = DIST_DIR / "Tube_Ultimate_Player_Portable_with_deno_ffmpeg"
THIRDPART_DIR = ROOT / "3rdpart"
VERSION_FILE = ROOT / "app_version.txt"
RELEASE_NAME = "Tube_Ultimate_Player"
MIN_DLL_SIZE = 10 * 1024 * 1024
ICON_FILE = ROOT / "docs" / "assets" / "icons" / "app-icon.ico"
ENHANCED_RUNTIME_FILES = ("deno.exe", "ffmpeg.exe", "ffprobe.exe")


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
            RELEASE_NAME,
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


def validate_enhanced_runtime() -> None:
    missing = [name for name in ENHANCED_RUNTIME_FILES if not (THIRDPART_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing enhanced runtime files: {', '.join(missing)}")


def _copy_tree_contents(source_dir: Path, target_dir: Path) -> None:
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)


def assemble_portable(version: str, *, with_deno_ffmpeg: bool = False) -> Path:
    bundle_dir = DIST_DIR / RELEASE_NAME
    portable_dir = ENHANCED_PORTABLE_DIR if with_deno_ffmpeg else PORTABLE_DIR
    if portable_dir.exists():
        shutil.rmtree(portable_dir)
    portable_dir.mkdir(parents=True, exist_ok=True)

    _copy_tree_contents(bundle_dir, portable_dir)
    shutil.copytree(THIRDPART_DIR, portable_dir / "3rdpart", dirs_exist_ok=True)
    shutil.copy2(ROOT / "README.md", portable_dir / "README.md")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", portable_dir / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(ROOT / "app_version.txt", portable_dir / "app_version.txt")

    suffix = "_with_deno_ffmpeg" if with_deno_ffmpeg else ""
    zip_path = DIST_DIR / f"Tube_Ultimate_Player_portable_v{version}{suffix}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in portable_dir.rglob("*"):
            archive.write(path, path.relative_to(portable_dir))
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-deno-ffmpeg",
        action="store_true",
        help="Build a portable ZIP containing bundled Deno, FFmpeg and FFprobe.",
    )
    args = parser.parse_args(argv)
    version = read_version()
    if args.with_deno_ffmpeg:
        validate_enhanced_runtime()
    run_pyinstaller()
    zip_path = assemble_portable(version, with_deno_ffmpeg=args.with_deno_ffmpeg)
    print(zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
