from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build" / "macos"
THIRDPART_DIR = ROOT / "3rdpart"
VERSION_FILE = ROOT / "app_version.txt"
APP_NAME = "Tube_Ultimate_Player"
APP_BUNDLE = DIST_DIR / f"{APP_NAME}.app"
ARCH_MACHINES = {
    "mac_x86_64": {"x86_64", "amd64"},
    "mac_arm64": {"arm64", "aarch64"},
}


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("app_version.txt is empty")
    return version


def require_macos(arch: str) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("macOS packages must be built on macOS")
    machine = platform.machine().strip().lower()
    if machine not in ARCH_MACHINES[arch]:
        raise RuntimeError(f"Requested {arch}, but the runner architecture is {machine}")


def require_runtime_files() -> tuple[Path, Path, Path]:
    libmpv = THIRDPART_DIR / "libmpv.dylib"
    ytdlp = THIRDPART_DIR / "yt-dlp_macos"
    deno = THIRDPART_DIR / "deno"
    missing = [str(path) for path in (libmpv, ytdlp, deno) if not path.is_file()]
    if missing:
        raise RuntimeError("Missing macOS runtime files: " + ", ".join(missing))
    return libmpv, ytdlp, deno


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_icns() -> Path:
    source = ROOT / "docs" / "assets" / "icons" / "app-icon-512.png"
    if not source.is_file():
        raise RuntimeError(f"Missing macOS icon source: {source}")
    iconset = BUILD_DIR / "app-icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)
    sizes = (16, 32, 128, 256, 512)
    for size in sizes:
        subprocess.run(
            ["sips", "-z", str(size), str(size), str(source), "--out", str(iconset / f"icon_{size}x{size}.png")],
            check=True,
        )
        double = size * 2
        subprocess.run(
            ["sips", "-z", str(double), str(double), str(source), "--out", str(iconset / f"icon_{size}x{size}@2x.png")],
            check=True,
        )
    output = BUILD_DIR / "app-icon.icns"
    output.unlink(missing_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)], check=True)
    return output


def run_pyinstaller(icon: Path, version: str) -> Path:
    libmpv, ytdlp, deno = require_runtime_files()
    for executable in (ytdlp, deno):
        make_executable(executable)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--windowed",
        "--osx-bundle-identifier",
        "com.tubeultimateplayer.app",
        "--icon",
        str(icon),
        "--add-data",
        f"{ROOT / 'resources'}:resources",
        "--add-data",
        f"{ROOT / 'config'}:config",
        "--add-data",
        f"{ROOT / 'docs' / 'assets' / 'icons'}:docs/assets/icons",
        "--add-data",
        f"{ROOT / 'docs' / 'assets' / 'pip'}:docs/assets/pip",
        "--add-data",
        f"{ROOT / 'app_version.txt'}:.",
        "--add-data",
        f"{ROOT / 'README.md'}:.",
        "--add-data",
        f"{ROOT / 'THIRD_PARTY_NOTICES.md'}:.",
        "--add-binary",
        f"{libmpv}:3rdpart",
        "--add-binary",
        f"{ytdlp}:3rdpart",
        "--add-binary",
        f"{deno}:3rdpart",
        str(ROOT / "main.py"),
    ]
    env = dict(os.environ)
    env["MACOSX_DEPLOYMENT_TARGET"] = env.get("MACOSX_DEPLOYMENT_TARGET", "12.0")
    subprocess.run(command, check=True, cwd=ROOT, env=env)
    if not APP_BUNDLE.is_dir():
        raise RuntimeError(f"PyInstaller did not create {APP_BUNDLE}")
    info_plist = APP_BUNDLE / "Contents" / "Info.plist"
    subprocess.run(
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            f"Set :CFBundleShortVersionString {version}",
            str(info_plist),
        ],
        check=True,
    )
    subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", f"Set :CFBundleVersion {version}", str(info_plist)],
        check=True,
    )
    return APP_BUNDLE


def bundled_libmpv(app_bundle: Path) -> Path:
    candidates = sorted(app_bundle.rglob("libmpv.dylib"))
    if not candidates:
        raise RuntimeError("The app bundle does not contain libmpv.dylib")
    return candidates[0]


def bundle_libmpv_dependencies(app_bundle: Path) -> Path:
    bundler = shutil.which("dylibbundler")
    if not bundler:
        raise RuntimeError("dylibbundler is required to package Homebrew libmpv dependencies")
    libmpv = bundled_libmpv(app_bundle)
    destination = app_bundle / "Contents" / "Frameworks" / "mpv"
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            bundler,
            "-od",
            "-b",
            "-x",
            str(libmpv),
            "-d",
            str(destination),
            "-p",
            "@executable_path/../Frameworks/mpv/",
        ],
        check=True,
    )
    return libmpv


def verify_no_homebrew_links(app_bundle: Path) -> None:
    targets = [bundled_libmpv(app_bundle), *sorted((app_bundle / "Contents" / "Frameworks" / "mpv").glob("*.dylib"))]
    forbidden = ("/opt/homebrew/", "/usr/local/Cellar/", "/usr/local/opt/")
    for target in targets:
        result = subprocess.run(["otool", "-L", str(target)], check=True, capture_output=True, text=True)
        leaked = [line.strip() for line in result.stdout.splitlines() if any(prefix in line for prefix in forbidden)]
        if leaked:
            raise RuntimeError(f"Unbundled Homebrew dependency in {target}: {leaked}")


def sign_app(app_bundle: Path) -> None:
    # Ad-hoc signing keeps the nested Mach-O files internally consistent. A
    # notarized public release can replace this identity in CI later.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app_bundle)], check=True)


def package_outputs(app_bundle: Path, version: str, arch: str) -> tuple[Path, Path]:
    output_dir = DIST_DIR / "macos"
    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"Tube_Ultimate_Player_v{version}_{arch}"
    zip_path = output_dir / f"{base}.zip"
    dmg_path = output_dir / f"{base}.dmg"
    zip_path.unlink(missing_ok=True)
    dmg_path.unlink(missing_ok=True)
    subprocess.run(
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_bundle), str(zip_path)],
        check=True,
    )
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(app_bundle),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ],
        check=True,
    )
    return dmg_path, zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build macOS app, DMG and ZIP release artifacts")
    parser.add_argument("--arch", required=True, choices=sorted(ARCH_MACHINES))
    args = parser.parse_args(argv)
    require_macos(args.arch)
    version = read_version()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    icon = build_icns()
    app_bundle = run_pyinstaller(icon, version)
    bundle_libmpv_dependencies(app_bundle)
    verify_no_homebrew_links(app_bundle)
    sign_app(app_bundle)
    for path in package_outputs(app_bundle, version, args.arch):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
