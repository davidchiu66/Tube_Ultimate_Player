from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app_paths import APP_NAME, CONFIG_DIR, DATA_DIR, RUNTIME_ROOT, read_app_version
from download.download_manager import TASKS_FILE
from services.config_service import USER_CONFIG_PATH, ConfigService
from services.site_registry import SITE_KEYS
from services.webdav_client import BACKUP_NAME_RE, RemoteBackup, WebdavClient
from workers.archive_extract_worker import validate_archive_entry


BACKUP_SCHEMA = 1
LOCAL_BACKUP_LIMIT = 3
REMOTE_BACKUP_LIMIT = 20
BACKUP_DIR = RUNTIME_ROOT / "backups"
DB_PATH = DATA_DIR / "tube_ultimate_player.sqlite3"


class BackupError(RuntimeError):
    pass


class BackupVersionTooNew(BackupError):
    def __init__(self, backup_version: str, current_version: str) -> None:
        super().__init__(f"备份来自较新版本 {backup_version}，当前版本为 {current_version}")
        self.backup_version = backup_version
        self.current_version = current_version


def build_backup_archive(
    *,
    include_cookies: bool = False,
    output_dir: Path | None = None,
    archive_name: str | None = None,
    user_config_path: Path = USER_CONFIG_PATH,
    db_path: Path = DB_PATH,
    tasks_path: Path = TASKS_FILE,
    config: ConfigService | None = None,
    cookie_paths: dict[str, Path] | None = None,
) -> Path:
    destination = Path(output_dir) if output_dir is not None else BACKUP_DIR
    destination.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    name = archive_name or f"tube-backup-{read_app_version()}-{now:%Y%m%d-%H%M%S}.zip"
    archive = destination / name
    with tempfile.TemporaryDirectory(prefix="tube-backup-", dir=destination) as temp_dir:
        stage = Path(temp_dir)
        files: list[tuple[str, Path]] = []
        _stage_json(Path(user_config_path), stage / "config" / "user_config.json", files, "config/user_config.json", "{}")
        snapshot = stage / "data" / "tube_ultimate_player.sqlite3"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if Path(db_path).exists():
            _snapshot_database(Path(db_path), snapshot)
        else:
            with closing(sqlite3.connect(snapshot)):
                pass
        files.append(("data/tube_ultimate_player.sqlite3", snapshot))
        _stage_json(Path(tasks_path), stage / "data" / "download_tasks.json", files, "data/download_tasks.json", "[]")
        if include_cookies:
            for site in SITE_KEYS:
                if cookie_paths is not None:
                    configured = cookie_paths.get(site)
                    if not configured:
                        continue
                    source = Path(configured)
                else:
                    cookie_config = config or ConfigService()
                    source = Path(cookie_config.cookie_file_path(site))
                _stage_optional(source, stage / "cookies" / f"cookie_{site}.txt", files, f"cookies/cookie_{site}.txt")

        manifest = {
            "app": APP_NAME,
            "schema": BACKUP_SCHEMA,
            "app_version": read_app_version(),
            "created_at": now.isoformat(timespec="seconds"),
            "platform": sys.platform,
            "include_cookies": bool(include_cookies),
            "files": [
                {"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)}
                for relative, path in files
            ],
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_archive = archive.with_name(archive.name + ".tmp")
        temp_archive.unlink(missing_ok=True)
        with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            handle.write(manifest_path, "manifest.json")
            for relative, path in files:
                handle.write(path, relative)
        os.replace(temp_archive, archive)
    return archive


def validate_backup_archive(archive: Path, *, allow_newer: bool = False) -> dict:
    path = Path(archive)
    try:
        with tempfile.TemporaryDirectory(prefix="tube-validate-") as temp_dir, zipfile.ZipFile(path) as handle:
            root = Path(temp_dir)
            names = handle.namelist()
            for name in names:
                validate_archive_entry(name, root)
            if "manifest.json" not in names:
                raise BackupError("备份包缺少 manifest.json")
            manifest = json.loads(handle.read("manifest.json").decode("utf-8"))
            _validate_manifest_shape(manifest)
            current_version = read_app_version()
            backup_version = str(manifest.get("app_version") or "0")
            if not allow_newer and _version_key(backup_version) > _version_key(current_version):
                raise BackupVersionTooNew(backup_version, current_version)
            entries = {item.filename: item for item in handle.infolist()}
            for item in manifest["files"]:
                relative = item["path"]
                info = entries.get(relative)
                if info is None or info.is_dir():
                    raise BackupError(f"备份包缺少文件：{relative}")
                data = handle.read(relative)
                if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise BackupError(f"备份文件校验失败：{relative}")
            return manifest
    except BackupError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeError) as exc:
        raise BackupError(f"备份包无法读取：{exc}") from exc


def restore_backup_archive(
    archive: Path,
    *,
    allow_newer: bool = False,
    backup_dir: Path | None = None,
    user_config_path: Path = USER_CONFIG_PATH,
    db_path: Path = DB_PATH,
    tasks_path: Path = TASKS_FILE,
    config_dir: Path = CONFIG_DIR,
) -> Path:
    manifest = validate_backup_archive(archive, allow_newer=allow_newer)
    snapshots = Path(backup_dir) if backup_dir is not None else BACKUP_DIR
    snapshot_name = f"pre-restore-{datetime.now().astimezone():%Y%m%d-%H%M%S}.zip"
    targets = {
        "config/user_config.json": Path(user_config_path),
        "data/tube_ultimate_player.sqlite3": Path(db_path),
        "data/download_tasks.json": Path(tasks_path),
        "cookies/cookie_youtube.txt": _cookie_target("youtube", Path(config_dir)),
        "cookies/cookie_bilibili.txt": _cookie_target("bilibili", Path(config_dir)),
        "cookies/cookie_douyin.txt": _cookie_target("douyin", Path(config_dir)),
        "cookies/cookie_tiktok.txt": _cookie_target("tiktok", Path(config_dir)),
    }
    snapshot = build_backup_archive(
        output_dir=snapshots,
        archive_name=snapshot_name,
        include_cookies=True,
        user_config_path=Path(user_config_path),
        db_path=Path(db_path),
        tasks_path=Path(tasks_path),
        cookie_paths={
            "youtube": targets["cookies/cookie_youtube.txt"],
            "bilibili": targets["cookies/cookie_bilibili.txt"],
            "douyin": targets["cookies/cookie_douyin.txt"],
            "tiktok": targets["cookies/cookie_tiktok.txt"],
        },
    )
    try:
        with tempfile.TemporaryDirectory(prefix="tube-restore-", dir=snapshots) as temp_dir, zipfile.ZipFile(archive) as handle:
            root = Path(temp_dir)
            for item in manifest["files"]:
                relative = item["path"]
                source = validate_archive_entry(relative, root)
                source.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(relative) as reader, source.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
            for item in manifest["files"]:
                relative = item["path"]
                target = targets.get(relative)
                if target is None:
                    continue
                _atomic_copy(root / Path(relative), target)
    except Exception as exc:
        raise BackupError(f"恢复过程中部分文件可能已替换，请使用本地快照恢复：{snapshot}；原因：{exc}") from exc
    return snapshot


def prune_local_backups(directory: Path | None = None, limit: int = LOCAL_BACKUP_LIMIT) -> None:
    root = Path(directory) if directory is not None else BACKUP_DIR
    backups = sorted(root.glob("tube-backup-*.zip"), key=_local_backup_sort_key, reverse=True)
    for path in backups[max(0, limit):]:
        path.unlink(missing_ok=True)


def prune_remote_backups(client: WebdavClient, backups: list[RemoteBackup] | None = None, limit: int = REMOTE_BACKUP_LIMIT) -> list[str]:
    items = backups if backups is not None else client.list_backups()
    deleted: list[str] = []
    for item in items[max(0, limit):]:
        client.delete(item.name)
        deleted.append(item.name)
    return deleted


def _snapshot_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source)) as source_conn, closing(sqlite3.connect(target)) as target_conn:
        with target_conn:
            source_conn.backup(target_conn)


def _stage_optional(source: Path, target: Path, files: list[tuple[str, Path]], relative: str) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    files.append((relative, target))


def _stage_json(
    source: Path,
    target: Path,
    files: list[tuple[str, Path]],
    relative: str,
    empty_value: str,
) -> None:
    if source.is_file():
        _stage_optional(source, target, files, relative)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(empty_value, encoding="utf-8")
    files.append((relative, target))


def _validate_manifest_shape(manifest: object) -> None:
    if not isinstance(manifest, dict) or manifest.get("app") != APP_NAME:
        raise BackupError("这不是 Tube_Ultimate_Player 备份包")
    schema = manifest.get("schema")
    if not isinstance(schema, int) or schema < 1 or schema > BACKUP_SCHEMA:
        raise BackupError(f"不支持的备份 schema：{schema}")
    for field in ("app_version", "created_at", "platform"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise BackupError(f"manifest 缺少有效字段：{field}")
    if not isinstance(manifest.get("include_cookies"), bool):
        raise BackupError("manifest 缺少有效字段：include_cookies")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BackupError("manifest 文件清单无效")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise BackupError("manifest 文件条目无效")
        relative = str(item.get("path") or "")
        if relative in seen or relative not in {
            "config/user_config.json", "data/tube_ultimate_player.sqlite3",
            "data/download_tasks.json", "cookies/cookie_youtube.txt", "cookies/cookie_bilibili.txt",
            "cookies/cookie_douyin.txt", "cookies/cookie_tiktok.txt",
        }:
            raise BackupError(f"manifest 包含无效文件：{relative}")
        seen.add(relative)
        if relative.startswith("cookies/") and not manifest["include_cookies"]:
            raise BackupError("manifest 标记为不含 Cookie，却包含 Cookie 文件")
        if not isinstance(item.get("size"), int) or item["size"] < 0:
            raise BackupError(f"manifest 文件大小无效：{relative}")
        digest = str(item.get("sha256") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
            raise BackupError(f"manifest 哈希无效：{relative}")
    required = {
        "config/user_config.json",
        "data/tube_ultimate_player.sqlite3",
        "data/download_tasks.json",
    }
    missing = sorted(required - seen)
    if missing:
        raise BackupError(f"manifest 缺少必需文件：{', '.join(missing)}")


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(target.name + ".restore.tmp")
    temp_path.unlink(missing_ok=True)
    shutil.copy2(source, temp_path)
    os.replace(temp_path, target)


def _cookie_target(site: str, config_dir: Path) -> Path:
    if sys.platform.startswith("linux"):
        return config_dir / f"cookie_{site}.txt"
    return config_dir.parent / f"cookie_{site}.txt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_key(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(value or "").split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _local_backup_sort_key(path: Path) -> tuple[str, str]:
    match = BACKUP_NAME_RE.fullmatch(path.name)
    return (match.group("stamp") if match else "", path.name)
