from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build" / "linux"
PYINSTALLER_DIR = DIST_DIR / "Tube_Ultimate_Player"
APPDIR = BUILD_DIR / "AppDir"
THIRDPART_DIR = ROOT / "3rdpart"
LINUX_PACKAGING_DIR = ROOT / "packaging" / "linux"
VERSION_FILE = ROOT / "app_version.txt"
APP_NAME = "Tube_Ultimate_Player"


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("app_version.txt is empty")
    return version


def require_linux() -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux packages must be built on Linux")
    if os.uname().machine not in ("x86_64", "amd64"):
        raise RuntimeError(f"The first Linux release only supports x86_64, current architecture: {os.uname().machine}")


def bundled_libmpv() -> Path:
    candidates = sorted(THIRDPART_DIR.glob("libmpv.so*"))
    files = [path for path in candidates if path.is_file()]
    if not files:
        raise RuntimeError("Missing bundled libmpv under 3rdpart/libmpv.so*")
    return files[0]


def validate_enhanced_runtime() -> None:
    required = ("deno", "ffmpeg", "ffprobe", "yt-dlp_linux")
    missing = [name for name in required if not (THIRDPART_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing enhanced Linux runtime files: {', '.join(missing)}")
    bundled_libmpv()
    licenses = THIRDPART_DIR / "licenses"
    if not licenses.is_dir() or not any(path.is_file() for path in licenses.rglob("*")):
        raise RuntimeError("Missing third-party license files under 3rdpart/licenses")


def run_pyinstaller() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",
        "--add-data",
        f"{ROOT / 'resources'}:resources",
        "--add-data",
        f"{ROOT / 'config'}:config",
        "--add-data",
        f"{ROOT / 'docs' / 'assets' / 'icons'}:docs/assets/icons",
        "--add-data",
        f"{ROOT / 'app_version.txt'}:.",
        "--add-data",
        f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.",
        str(ROOT / "main.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(child, destination, follow_symlinks=False)


def stage_appdir() -> Path:
    if not PYINSTALLER_DIR.is_dir():
        raise RuntimeError(f"PyInstaller output not found: {PYINSTALLER_DIR}")
    if APPDIR.exists():
        shutil.rmtree(APPDIR)

    app_bin = APPDIR / "usr" / "bin"
    copy_tree_contents(PYINSTALLER_DIR, app_bin)

    bundled_dir = app_bin / "3rdpart"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    for name in ("deno", "ffmpeg", "ffprobe", "yt-dlp_linux"):
        shutil.copy2(THIRDPART_DIR / name, bundled_dir / name)
        make_executable(bundled_dir / name)
    libmpv = bundled_libmpv()
    shutil.copy2(libmpv, bundled_dir / libmpv.name, follow_symlinks=True)

    licenses_target = app_bin / "licenses"
    shutil.copytree(THIRDPART_DIR / "licenses", licenses_target, dirs_exist_ok=True)
    shutil.copy2(LINUX_PACKAGING_DIR / "THIRD_PARTY_BUNDLED.md", app_bin / "THIRD_PARTY_BUNDLED.md")
    manifest = THIRDPART_DIR / "third-party-manifest.sha256"
    if manifest.is_file():
        shutil.copy2(manifest, app_bin / manifest.name)

    desktop_dir = APPDIR / "usr" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LINUX_PACKAGING_DIR / "tube-ultimate-player.desktop", desktop_dir)

    icon_dir = APPDIR / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docs" / "assets" / "icons" / "app-icon-256.png", icon_dir / "tube-ultimate-player.png")

    shutil.copy2(LINUX_PACKAGING_DIR / "AppRun", APPDIR / "AppRun")
    make_executable(APPDIR / "AppRun")
    make_executable(app_bin / APP_NAME)
    return APPDIR


def build_deb(appdir: Path, version: str) -> Path:
    dpkg_deb = shutil.which("dpkg-deb")
    if not dpkg_deb:
        raise RuntimeError("dpkg-deb is required to build the DEB package")

    package_root = BUILD_DIR / "deb-root"
    if package_root.exists():
        shutil.rmtree(package_root)
    control_dir = package_root / "DEBIAN"
    control_dir.mkdir(parents=True)

    app_root = package_root / "opt" / APP_NAME
    copy_tree_contents(appdir / "usr" / "bin", app_root)
    if (appdir / "usr" / "lib").is_dir():
        shutil.copytree(appdir / "usr" / "lib", app_root / "lib", dirs_exist_ok=True, symlinks=True)

    launcher_dir = package_root / "usr" / "bin"
    launcher_dir.mkdir(parents=True)
    shutil.copy2(LINUX_PACKAGING_DIR / "tube-ultimate-player", launcher_dir / "tube-ultimate-player")
    make_executable(launcher_dir / "tube-ultimate-player")

    desktop_dir = package_root / "usr" / "share" / "applications"
    desktop_dir.mkdir(parents=True)
    desktop_text = (LINUX_PACKAGING_DIR / "tube-ultimate-player.desktop").read_text(encoding="utf-8")
    desktop_text = desktop_text.replace("Exec=Tube_Ultimate_Player %U", "Exec=tube-ultimate-player %U")
    (desktop_dir / "tube-ultimate-player.desktop").write_text(desktop_text, encoding="utf-8")
    icon_dir = package_root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icon_dir.mkdir(parents=True)
    shutil.copy2(
        ROOT / "docs" / "assets" / "icons" / "app-icon-256.png",
        icon_dir / "tube-ultimate-player.png",
    )

    installed_size = sum(path.stat().st_size for path in package_root.rglob("*") if path.is_file()) // 1024
    control = (
        "Package: tube-ultimate-player\n"
        f"Version: {version}\n"
        "Section: video\n"
        "Priority: optional\n"
        "Architecture: amd64\n"
        "Maintainer: davidchiu66 <chinamen@gmail.com>\n"
        "Depends: libc6, libgl1, libegl1, libxcb1, libxkbcommon-x11-0, libdbus-1-3, libfontconfig1\n"
        f"Installed-Size: {installed_size}\n"
        "Description: YouTube and Bilibili desktop video player\n"
        " Enhanced x86_64 build with bundled libmpv, Deno, FFmpeg and yt-dlp.\n"
    )
    (control_dir / "control").write_text(control, encoding="utf-8")

    output_dir = DIST_DIR / "linux"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"tube-ultimate-player_{version}_amd64_with_deno_ffmpeg.deb"
    subprocess.run([dpkg_deb, "--build", "--root-owner-group", str(package_root), str(output)], check=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build enhanced Linux AppDir/DEB staging artifacts")
    parser.add_argument("--skip-pyinstaller", action="store_true")
    parser.add_argument("--build-deb", action="store_true")
    parser.add_argument(
        "--reuse-appdir",
        action="store_true",
        help="Reuse an AppDir already processed by linuxdeploy (required before building the final DEB).",
    )
    args = parser.parse_args(argv)

    require_linux()
    validate_enhanced_runtime()
    version = read_version()
    if args.reuse_appdir:
        if not APPDIR.is_dir():
            raise RuntimeError(f"Existing AppDir not found: {APPDIR}")
        appdir = APPDIR
    else:
        if not args.skip_pyinstaller:
            run_pyinstaller()
        appdir = stage_appdir()
    print(appdir)
    if args.build_deb:
        print(build_deb(appdir, version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
