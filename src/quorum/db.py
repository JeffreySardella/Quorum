from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT    NOT NULL UNIQUE,
    type          TEXT    NOT NULL,
    size          INTEGER NOT NULL,
    checksum      TEXT,
    created_at    TEXT,
    modified_at   TEXT,
    duration      REAL,
    source_device TEXT,
    event_id      INTEGER REFERENCES events(id)
);

CREATE TABLE IF NOT EXISTS metadata (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id  INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    key       TEXT    NOT NULL,
    value     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metadata_media ON metadata(media_id);
CREATE INDEX IF NOT EXISTS idx_metadata_key   ON metadata(key);

CREATE TABLE IF NOT EXISTS embeddings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    type     TEXT    NOT NULL,
    vector   BLOB    NOT NULL,
    label    TEXT
);
CREATE INDEX IF NOT EXISTS idx_embeddings_media ON embeddings(media_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_type  ON embeddings(type);

CREATE TABLE IF NOT EXISTS tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    category TEXT    NOT NULL,
    value    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tags_media    ON tags(media_id);
CREATE INDEX IF NOT EXISTS idx_tags_category ON tags(category);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    start_time    TEXT,
    end_time      TEXT,
    auto_detected INTEGER NOT NULL DEFAULT 1,
    metadata      TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    signal_name TEXT    NOT NULL,
    candidate   TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    reasoning   TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_media ON signals(media_id);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id   INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    action     TEXT    NOT NULL,
    original   TEXT    NOT NULL,
    correction TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_media ON feedback(media_id);

CREATE TABLE IF NOT EXISTS actions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    operation  TEXT    NOT NULL,
    source_path TEXT   NOT NULL,
    dest_path  TEXT,
    metadata   TEXT,
    reversible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS processing (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id     INTEGER REFERENCES media(id) ON DELETE SET NULL,
    job_type     TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending',
    progress     REAL    NOT NULL DEFAULT 0.0,
    error        TEXT,
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_processing_status ON processing(status);
"""


class QuorumDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_media(
        self, path: str, media_type: str, size: int,
        checksum: str | None = None, created_at: str | None = None,
        modified_at: str | None = None, duration: float | None = None,
        source_device: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO media (path, type, size, checksum, created_at, modified_at, duration, source_device)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (path, media_type, size, checksum, created_at, modified_at, duration, source_device),
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert_media(
        self, path: str, media_type: str, size: int,
        checksum: str | None = None, created_at: str | None = None,
        modified_at: str | None = None, duration: float | None = None,
        source_device: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO media (path, type, size, checksum, created_at, modified_at, duration, source_device)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET"
            " type=excluded.type, size=excluded.size, checksum=excluded.checksum,"
            " created_at=excluded.created_at, modified_at=excluded.modified_at,"
            " duration=excluded.duration, source_device=excluded.source_device",
            (path, media_type, size, checksum, created_at, modified_at, duration, source_device),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_media(self, media_id: int) -> dict | None:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        self.conn.row_factory = None
        return dict(row) if row else None

    def get_media_by_path(self, path: str) -> dict | None:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM media WHERE path = ?", (path,)).fetchone()
        self.conn.row_factory = None
        return dict(row) if row else None

    def list_media(self, media_type: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if media_type:
            rows = self.conn.execute("SELECT * FROM media WHERE type = ?", (media_type,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM media").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def count_media(self, media_type: str | None = None) -> int:
        if media_type:
            return self.conn.execute("SELECT COUNT(*) FROM media WHERE type = ?", (media_type,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    def delete_media(self, media_id: int) -> None:
        self.conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
        self.conn.commit()
