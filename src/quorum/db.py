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
    value     TEXT    NOT NULL,
    UNIQUE(media_id, key)
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

    def __enter__(self) -> QuorumDB:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

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

    # ------------------------------------------------------------------
    # Metadata CRUD
    # ------------------------------------------------------------------

    def insert_metadata(self, media_id: int, key: str, value: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO metadata (media_id, key, value) VALUES (?, ?, ?)",
            (media_id, key, value),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_metadata(self, media_id: int) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM metadata WHERE media_id = ?", (media_id,)
        ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def get_metadata_value(self, media_id: int, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE media_id = ? AND key = ?", (media_id, key)
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, media_id: int, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO metadata (media_id, key, value) VALUES (?, ?, ?)"
            " ON CONFLICT(media_id, key) DO UPDATE SET value=excluded.value",
            (media_id, key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Tags CRUD
    # ------------------------------------------------------------------

    def insert_tag(self, media_id: int, category: str, value: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO tags (media_id, category, value) VALUES (?, ?, ?)",
            (media_id, category, value),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_tags(self, media_id: int, category: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if category is not None:
            rows = self.conn.execute(
                "SELECT * FROM tags WHERE media_id = ? AND category = ?", (media_id, category)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tags WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def clear_tags(self, media_id: int, category: str | None = None) -> None:
        if category is not None:
            self.conn.execute(
                "DELETE FROM tags WHERE media_id = ? AND category = ?", (media_id, category)
            )
        else:
            self.conn.execute("DELETE FROM tags WHERE media_id = ?", (media_id,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Signals CRUD
    # ------------------------------------------------------------------

    def insert_signal(
        self,
        media_id: int,
        signal_name: str,
        candidate: str,
        confidence: float,
        reasoning: str,
        created_at: str,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO signals (media_id, signal_name, candidate, confidence, reasoning, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (media_id, signal_name, candidate, confidence, reasoning, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_signals(self, media_id: int, signal_name: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if signal_name is not None:
            rows = self.conn.execute(
                "SELECT * FROM signals WHERE media_id = ? AND signal_name = ?", (media_id, signal_name)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM signals WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Feedback CRUD
    # ------------------------------------------------------------------

    def insert_feedback(
        self,
        media_id: int,
        action: str,
        original: str,
        correction: str | None = None,
        created_at: str = "",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO feedback (media_id, action, original, correction, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (media_id, action, original, correction, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_feedback(self, media_id: int) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE media_id = ?", (media_id,)
        ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def count_feedback(self, action: str | None = None) -> int:
        if action is not None:
            return self.conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE action = ?", (action,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    # ------------------------------------------------------------------
    # Actions CRUD
    # ------------------------------------------------------------------

    def insert_action(
        self,
        operation: str,
        source_path: str,
        dest_path: str | None = None,
        metadata: str | None = None,
        reversible: int = 1,
        created_at: str = "",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO actions (operation, source_path, dest_path, metadata, reversible, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (operation, source_path, dest_path, metadata, reversible, created_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_actions(self, reverse: bool = False) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        order = "DESC" if reverse else "ASC"
        rows = self.conn.execute(f"SELECT * FROM actions ORDER BY created_at {order}").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Processing (jobs) CRUD
    # ------------------------------------------------------------------

    def insert_job(
        self,
        job_type: str,
        media_id: int | None = None,
        started_at: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO processing (job_type, media_id, started_at) VALUES (?, ?, ?)",
            (job_type, media_id, started_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_job(self, job_id: int) -> dict | None:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM processing WHERE id = ?", (job_id,)).fetchone()
        self.conn.row_factory = None
        return dict(row) if row else None

    def update_job(
        self,
        job_id: int,
        status: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if progress is not None:
            fields.append("progress = ?")
            values.append(progress)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at)
        if not fields:
            return
        values.append(job_id)
        self.conn.execute(f"UPDATE processing SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()

    def list_jobs(self, status: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if status is not None:
            rows = self.conn.execute(
                "SELECT * FROM processing WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM processing").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Events CRUD
    # ------------------------------------------------------------------

    def insert_event(
        self,
        name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        auto_detected: int = 1,
        metadata: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (name, start_time, end_time, auto_detected, metadata)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, start_time, end_time, auto_detected, metadata),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_event(self, event_id: int) -> dict | None:
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        self.conn.row_factory = None
        return dict(row) if row else None

    def list_events(self) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute("SELECT * FROM events ORDER BY start_time").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def update_event(self, event_id: int, **kwargs) -> None:
        fields: list[str] = []
        values: list = []
        for col in ("name", "start_time", "end_time", "auto_detected", "metadata"):
            if col in kwargs:
                fields.append(f"{col} = ?")
                values.append(kwargs[col])
        if not fields:
            return
        values.append(event_id)
        self.conn.execute(f"UPDATE events SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()

    def delete_event(self, event_id: int) -> None:
        # Unlink media first (foreign key ON DELETE for events is not CASCADE on media.event_id)
        self.conn.execute("UPDATE media SET event_id = NULL WHERE event_id = ?", (event_id,))
        self.conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self.conn.commit()

    def assign_media_to_event(self, media_id: int, event_id: int) -> None:
        self.conn.execute("UPDATE media SET event_id = ? WHERE id = ?", (event_id, media_id))
        self.conn.commit()

    def unlink_media_from_event(self, media_id: int) -> None:
        self.conn.execute("UPDATE media SET event_id = NULL WHERE id = ?", (media_id,))
        self.conn.commit()

    def get_event_media(self, event_id: int) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM media WHERE event_id = ?", (event_id,)
        ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Embeddings CRUD
    # ------------------------------------------------------------------

    def insert_embedding(
        self,
        media_id: int,
        emb_type: str,
        vector: bytes,
        label: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO embeddings (media_id, type, vector, label) VALUES (?, ?, ?, ?)",
            (media_id, emb_type, vector, label),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_embeddings(self, media_id: int, emb_type: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if emb_type is not None:
            rows = self.conn.execute(
                "SELECT * FROM embeddings WHERE media_id = ? AND type = ?", (media_id, emb_type)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM embeddings WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def delete_embeddings(self, media_id: int, emb_type: str | None = None) -> None:
        if emb_type is not None:
            self.conn.execute(
                "DELETE FROM embeddings WHERE media_id = ? AND type = ?", (media_id, emb_type)
            )
        else:
            self.conn.execute("DELETE FROM embeddings WHERE media_id = ?", (media_id,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Aggregate statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        total_media = self.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        total_size = self.conn.execute("SELECT COALESCE(SUM(size), 0) FROM media").fetchone()[0]
        total_events = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        total_tags = self.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        pending_jobs = self.conn.execute(
            "SELECT COUNT(*) FROM processing WHERE status = 'pending'"
        ).fetchone()[0]
        by_type: dict[str, int] = {}
        for row in self.conn.execute("SELECT type, COUNT(*) FROM media GROUP BY type"):
            by_type[row[0]] = row[1]
        return {
            "total_media": total_media,
            "by_type": by_type,
            "total_size": total_size,
            "total_events": total_events,
            "total_tags": total_tags,
            "pending_jobs": pending_jobs,
        }
