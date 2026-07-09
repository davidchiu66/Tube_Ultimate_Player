from __future__ import annotations

import sqlite3
from pathlib import Path

from app_paths import DATA_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    webpage_url TEXT,
    thumbnail TEXT,
    duration INTEGER DEFAULT 0,
    watched_position INTEGER DEFAULT 0,
    play_count INTEGER DEFAULT 1,
    last_played_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_video_id ON history(video_id);
CREATE INDEX IF NOT EXISTS idx_history_last_played_at ON history(last_played_at);

CREATE TABLE IF NOT EXISTS favorite (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    webpage_url TEXT NOT NULL,
    uploader TEXT,
    duration INTEGER DEFAULT 0,
    thumbnail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_favorite_updated_at ON favorite(updated_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    auto_play_next INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playlist_library_updated_at ON playlist_library(updated_at);

CREATE TABLE IF NOT EXISTS playlist_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_key TEXT NOT NULL,
    playlist_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    webpage_url TEXT NOT NULL,
    uploader TEXT,
    duration INTEGER DEFAULT 0,
    thumbnail TEXT,
    position INTEGER NOT NULL,
    availability TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_playlist_item_key_position ON playlist_item(playlist_key, position);
"""


class SQLiteManager:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DATA_DIR / "tube_ultimate_player.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
