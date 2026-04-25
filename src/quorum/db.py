from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
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

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    media_id UNINDEXED,
    content,
    tokenize='porter unicode61'
);
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
    # Full-text search
    # ------------------------------------------------------------------

    def index_media_text(self, media_id: int) -> None:
        """Build/rebuild the FTS5 search index entry for a single media item."""
        parts: list[str] = []

        media = self.get_media(media_id)
        if not media:
            return

        # Add filename
        parts.append(
            Path(media["path"]).stem.replace(".", " ").replace("_", " ").replace("-", " ")
        )

        # Add all metadata values
        for meta in self.get_metadata(media_id):
            if meta["value"]:
                parts.append(meta["value"])

        # Add all tag values
        for tag in self.get_tags(media_id):
            if tag["value"]:
                parts.append(tag["value"])

        content = " ".join(parts)
        if not content.strip():
            return

        # Upsert: delete old entry if exists, insert new
        self.conn.execute("DELETE FROM search_index WHERE media_id = ?", (media_id,))
        self.conn.execute(
            "INSERT INTO search_index (media_id, content) VALUES (?, ?)",
            (media_id, content),
        )
        self.conn.commit()

    def reindex_all(self) -> int:
        """Rebuild the FTS5 search index for all media."""
        self.conn.execute("DELETE FROM search_index")
        count = 0
        for row in self.conn.execute("SELECT id FROM media").fetchall():
            self.index_media_text(row[0])
            count += 1
        return count

    def search_text(
        self,
        query: str,
        media_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search across all indexed media content."""
        sql = """
            SELECT s.media_id, s.content, bm25(search_index) as score
            FROM search_index s
            JOIN media m ON m.id = s.media_id
            WHERE search_index MATCH ?
        """
        params: list = [query]

        if media_type:
            sql += " AND m.type = ?"
            params.append(media_type)

        if after:
            sql += " AND m.created_at >= ?"
            params.append(after)

        if before:
            sql += " AND m.created_at <= ?"
            params.append(before)

        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(sql, params).fetchall()
        self.conn.row_factory = None

        results = []
        for row in rows:
            media = self.get_media(row["media_id"])
            if media:
                content = row["content"]
                snippet = content[:150] + "..." if len(content) > 150 else content
                results.append({
                    **media,
                    "score": abs(row["score"]),
                    "snippet": snippet,
                })
        return results

    # ------------------------------------------------------------------
    # Vector search (sqlite-vec)
    # ------------------------------------------------------------------

    def _ensure_vec_table(self, dim: int) -> None:
        """Create the vec0 virtual table if it doesn't exist."""
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
        except (ImportError, Exception):
            return
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS search_vec USING vec0(embedding float[{dim}])"
        )
        self.conn.commit()
        self._vec_dim = dim

    def index_media_vector(self, media_id: int, vector: list[float]) -> None:
        """Store a search embedding vector for a media item."""
        import struct

        dim = len(vector)
        if not hasattr(self, "_vec_dim"):
            self._ensure_vec_table(dim)
        vec_bytes = struct.pack(f"{dim}f", *vector)
        # Delete existing entry for this media_id
        try:
            self.conn.execute("DELETE FROM search_vec WHERE rowid = ?", (media_id,))
        except Exception:
            pass
        self.conn.execute(
            "INSERT INTO search_vec (rowid, embedding) VALUES (?, ?)",
            (media_id, vec_bytes),
        )
        self.conn.commit()

    def search_vector(
        self,
        query_vector: list[float],
        media_type: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Vector similarity search across indexed media."""
        import struct

        dim = len(query_vector)
        vec_bytes = struct.pack(f"{dim}f", *query_vector)

        try:
            rows = self.conn.execute(
                "SELECT rowid, distance FROM search_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                (vec_bytes, limit * 3),
            ).fetchall()
        except Exception:
            return []

        results = []
        for rowid, distance in rows:
            media = self.get_media(rowid)
            if not media:
                continue
            if media_type and media["type"] != media_type:
                continue
            if after and (media.get("created_at") or "") < after:
                continue
            if before and (media.get("created_at") or "") > before:
                continue

            # Get snippet from metadata
            title = self.get_metadata_value(rowid, "title") or ""
            desc = self.get_metadata_value(rowid, "description") or ""
            snippet = title
            if desc:
                snippet = f"{title} — {desc[:100]}" if title else desc[:150]

            results.append({
                **media,
                "score": 1.0 / (1.0 + distance),
                "snippet": snippet or Path(media["path"]).stem,
            })
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    def get_review_queue(
        self,
        sort: str = "confidence",
        media_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Get media items pending review — those with signals but no feedback,
        with max confidence between review_floor and auto_apply thresholds."""
        order = "ASC" if sort == "confidence" else "DESC"
        sort_col = "max_conf" if sort == "confidence" else "s.created_at"

        sql = """
            SELECT m.id, m.path, m.type, m.size, m.created_at,
                   MAX(s.confidence) as max_conf,
                   GROUP_CONCAT(s.candidate, ' | ') as candidates
            FROM media m
            JOIN signals s ON s.media_id = m.id
            LEFT JOIN feedback f ON f.media_id = m.id
            WHERE f.id IS NULL
        """
        params: list = []

        if media_type:
            sql += " AND m.type = ?"
            params.append(media_type)

        sql += f" GROUP BY m.id ORDER BY {sort_col} {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(sql, params).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def get_review_item(self, media_id: int) -> dict | None:
        """Get a single review item with full signal details."""
        media = self.get_media(media_id)
        if not media:
            return None
        signals = self.get_signals(media_id)
        feedback = self.get_feedback(media_id)
        title = self.get_metadata_value(media_id, "title")
        description = self.get_metadata_value(media_id, "description")
        transcript = self.get_metadata_value(media_id, "transcript")
        return {
            **media,
            "signals": signals,
            "feedback": feedback,
            "title": title,
            "description": description,
            "transcript_snippet": (transcript[:200] + "...") if transcript and len(transcript) > 200 else transcript,
            "max_confidence": max((s["confidence"] for s in signals), default=0),
            "top_candidate": max(signals, key=lambda s: s["confidence"])["candidate"] if signals else None,
            "reviewed": len(feedback) > 0,
        }

    def review_stats(self) -> dict:
        """Get counts of review queue status."""
        total_with_signals = self.conn.execute(
            "SELECT COUNT(DISTINCT media_id) FROM signals"
        ).fetchone()[0]
        reviewed = self.conn.execute(
            "SELECT COUNT(DISTINCT media_id) FROM feedback"
        ).fetchone()[0]
        pending = self.conn.execute(
            "SELECT COUNT(DISTINCT s.media_id) FROM signals s "
            "LEFT JOIN feedback f ON f.media_id = s.media_id "
            "WHERE f.id IS NULL"
        ).fetchone()[0]
        approved = self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE action = 'approve'"
        ).fetchone()[0]
        rejected = self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE action = 'reject'"
        ).fetchone()[0]
        corrected = self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE action = 'correct'"
        ).fetchone()[0]
        return {
            "total_with_signals": total_with_signals,
            "reviewed": reviewed,
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "corrected": corrected,
        }

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

    def dashboard_stats(self) -> dict:
        """Rich stats for the dashboard — extends basic stats with breakdowns."""
        base = self.stats()

        # Files by year (from created_at, extract year)
        by_year: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT SUBSTR(created_at, 1, 4) AS yr, COUNT(*) FROM media "
            "WHERE created_at IS NOT NULL GROUP BY yr ORDER BY yr"
        ):
            if row[0]:
                by_year[row[0]] = row[1]

        # Storage by type (bytes)
        storage_by_type: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT type, COALESCE(SUM(size), 0) FROM media GROUP BY type"
        ):
            storage_by_type[row[0]] = row[1]

        # Top faces by tag count
        top_faces: list[dict] = []
        for row in self.conn.execute(
            "SELECT value, COUNT(*) as cnt FROM tags WHERE category = 'face' "
            "GROUP BY value ORDER BY cnt DESC LIMIT 10"
        ):
            top_faces.append({"name": row[0], "count": row[1]})

        # Recent actions (last 50)
        self.conn.row_factory = sqlite3.Row
        recent_actions = [dict(r) for r in self.conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT 50"
        ).fetchall()]
        self.conn.row_factory = None

        # Confidence distribution (histogram buckets: 0-0.1, 0.1-0.2, ..., 0.9-1.0)
        confidence_dist: list[int] = [0] * 10
        for row in self.conn.execute(
            "SELECT confidence FROM signals"
        ):
            bucket = min(int(row[0] * 10), 9)
            confidence_dist[bucket] += 1

        # Events per month
        events_by_month: dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT SUBSTR(start_time, 1, 7) AS mo, COUNT(*) FROM events "
            "WHERE start_time IS NOT NULL GROUP BY mo ORDER BY mo"
        ):
            if row[0]:
                events_by_month[row[0]] = row[1]

        return {
            **base,
            "by_year": by_year,
            "storage_by_type": storage_by_type,
            "top_faces": top_faces,
            "recent_actions": recent_actions,
            "confidence_dist": confidence_dist,
            "events_by_month": events_by_month,
        }

    def export_all(self) -> dict:
        media = self.list_media()
        for m in media:
            m["metadata"] = self.get_metadata(m["id"])
            m["tags"] = self.get_tags(m["id"])
            m["signals"] = self.get_signals(m["id"])
        events = self.list_events()
        return {"media": media, "events": events, "stats": self.stats()}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg"}
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".webp"}


def migrate_from_legacy(db: QuorumDB, root: Path) -> dict[str, int]:
    counts = {"nfo_imported": 0, "faces_imported": 0, "media_indexed": 0}

    for ext in _VIDEO_EXTS | _PHOTO_EXTS:
        for f in root.rglob(f"*{ext}"):
            if ".quorum-cache" in f.parts:
                continue
            media_type = "video" if ext in _VIDEO_EXTS else "photo"
            mid = db.upsert_media(path=str(f), media_type=media_type, size=f.stat().st_size)
            counts["media_indexed"] += 1

            nfo = f.with_suffix(".nfo")
            if nfo.exists():
                try:
                    tree = ET.parse(str(nfo))
                    r = tree.getroot()
                    title_el = r.find("title")
                    if title_el is not None and title_el.text:
                        db.set_metadata(mid, "title", title_el.text)
                    plot_el = r.find("plot")
                    if plot_el is not None and plot_el.text:
                        db.set_metadata(mid, "description", plot_el.text)
                    year_el = r.find("year")
                    if year_el is not None and year_el.text:
                        db.set_metadata(mid, "year", year_el.text)
                    for genre_el in r.findall("genre"):
                        if genre_el.text:
                            db.insert_tag(mid, "scene", genre_el.text)
                    for actor_el in r.findall(".//actor/name"):
                        if actor_el.text:
                            db.insert_tag(mid, "face", actor_el.text)
                    counts["nfo_imported"] += 1
                except ET.ParseError:
                    pass

    faces_db = root / "faces.db"
    if faces_db.exists():
        import sqlite3 as _sql
        fconn = _sql.connect(str(faces_db))
        try:
            rows = fconn.execute("SELECT photo_path, embedding, cluster_id, label FROM faces").fetchall()
            for photo_path, embedding, cluster_id, label in rows:
                existing = db.get_media_by_path(photo_path)
                if not existing:
                    p = Path(photo_path)
                    if p.exists():
                        mid = db.upsert_media(path=photo_path, media_type="photo", size=p.stat().st_size)
                    else:
                        continue
                else:
                    mid = existing["id"]
                db.insert_embedding(mid, "face", embedding, label=label)
                if label:
                    db.insert_tag(mid, "face", label)
                counts["faces_imported"] += 1
        finally:
            fconn.close()

    return counts
