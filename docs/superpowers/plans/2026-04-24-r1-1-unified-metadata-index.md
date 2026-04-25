# R1-1: Unified Metadata Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered state files (`.nfo` sidecars, `faces.db`, `watch-state.json`) with a single SQLite database (`quorum.db`) that serves as Quorum's source of truth, while continuing to write Plex-compatible sidecars.

**Architecture:** A new `src/quorum/db.py` module provides a `QuorumDB` class wrapping raw SQLite (no ORM). It manages schema creation, migrations from existing data, and CRUD for all tables (media, metadata, embeddings, tags, events, signals, feedback, actions, processing). Existing modules get updated to dual-write (sidecar + index) and read from the index first. The `sqlite-vec` extension enables vector similarity search for future semantic search.

**Tech Stack:** Python stdlib `sqlite3`, `sqlite-vec` (pip: `sqlite-vec`), existing `rich` for CLI output.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/quorum/db.py` (create) | `QuorumDB` class — schema, connection, all CRUD operations |
| `tests/test_db.py` (create) | Unit tests for `QuorumDB` |
| `tests/conftest.py` (create) | Shared pytest fixtures (in-memory DB, temp dirs) |
| `src/quorum/cli.py` (modify) | Add `db` subcommand group (`stats`, `migrate`, `export`) |
| `tests/test_cli_db.py` (create) | CLI integration tests for `quorum db` commands |
| `src/quorum/config.py` (modify) | Add `db_path` setting |
| `pyproject.toml` (modify) | Add `pytest` + `sqlite-vec` dependencies |

---

### Task 1: Project test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add test dependencies to pyproject.toml**

Open `pyproject.toml` and add a `test` optional dependency group and pytest config:

```toml
[project.optional-dependencies]
directml = ["onnxruntime-directml>=1.17"]
test = ["pytest>=8.0", "pytest-tmp-files>=0.0.2"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create test package**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 3: Create conftest.py with shared fixtures**

Create `tests/conftest.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    p = tmp_path / "sample.mkv"
    p.write_bytes(b"\x1a\x45\xdf\xa3")  # Matroska magic bytes
    return p


@pytest.fixture
def sample_photo(tmp_path: Path) -> Path:
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic bytes
    return p
```

- [ ] **Step 4: Verify pytest runs with no tests**

Run: `python -m pytest tests/ -v --co`
Expected: "no tests ran" (collected 0 items), exit code 5 (no tests found — that's OK)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/conftest.py
git commit -m "feat: add pytest infrastructure and shared test fixtures"
```

---

### Task 2: QuorumDB class — schema creation and connection

**Files:**
- Create: `tests/test_db.py`
- Create: `src/quorum/db.py`

- [ ] **Step 1: Write failing test for DB creation and schema**

Create `tests/test_db.py`:

```python
from __future__ import annotations

from pathlib import Path

from quorum.db import QuorumDB


class TestQuorumDBInit:
    def test_creates_db_file(self, tmp_db_path: Path) -> None:
        db = QuorumDB(tmp_db_path)
        try:
            assert tmp_db_path.exists()
        finally:
            db.close()

    def test_creates_all_tables(self, tmp_db_path: Path) -> None:
        db = QuorumDB(tmp_db_path)
        try:
            tables = db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [t[0] for t in tables]
            assert "media" in table_names
            assert "metadata" in table_names
            assert "embeddings" in table_names
            assert "tags" in table_names
            assert "events" in table_names
            assert "signals" in table_names
            assert "feedback" in table_names
            assert "actions" in table_names
            assert "processing" in table_names
        finally:
            db.close()

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "dir" / "quorum.db"
        db = QuorumDB(db_path)
        try:
            assert db_path.exists()
        finally:
            db.close()

    def test_reopen_existing_db(self, tmp_db_path: Path) -> None:
        db1 = QuorumDB(tmp_db_path)
        db1.close()
        db2 = QuorumDB(tmp_db_path)
        try:
            tables = db2.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            assert len(tables) >= 9
        finally:
            db2.close()

    def test_wal_mode_enabled(self, tmp_db_path: Path) -> None:
        db = QuorumDB(tmp_db_path)
        try:
            mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'quorum.db'`

- [ ] **Step 3: Implement QuorumDB with schema**

Create `src/quorum/db.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add QuorumDB class with full schema creation"
```

---

### Task 3: Media table CRUD operations

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for media CRUD**

Append to `tests/test_db.py`:

```python
from datetime import datetime


class TestMediaCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            media_id = db.insert_media(
                path="/videos/test.mkv",
                media_type="video",
                size=1024000,
                checksum="abc123",
                created_at="2024-06-15T10:30:00",
            )
            assert media_id == 1
        finally:
            db.close()

    def test_get_media_by_path(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(
                path="/videos/test.mkv",
                media_type="video",
                size=1024000,
            )
            row = db.get_media_by_path("/videos/test.mkv")
            assert row is not None
            assert row["path"] == "/videos/test.mkv"
            assert row["type"] == "video"
            assert row["size"] == 1024000
        finally:
            db.close()

    def test_get_media_by_path_missing(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            row = db.get_media_by_path("/no/such/file.mkv")
            assert row is None
        finally:
            db.close()

    def test_get_media_by_id(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            row = db.get_media(mid)
            assert row is not None
            assert row["id"] == mid
        finally:
            db.close()

    def test_insert_duplicate_path_raises(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
            import sqlite3 as _sql
            with pytest.raises(_sql.IntegrityError):
                db.insert_media(path="/a.mkv", media_type="video", size=200)
        finally:
            db.close()

    def test_upsert_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            id1 = db.upsert_media(path="/a.mkv", media_type="video", size=100)
            id2 = db.upsert_media(path="/a.mkv", media_type="video", size=200)
            assert id1 == id2
            row = db.get_media(id1)
            assert row["size"] == 200
        finally:
            db.close()

    def test_list_media_by_type(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_media(path="/b.jpg", media_type="photo", size=200)
            db.insert_media(path="/c.mkv", media_type="video", size=300)
            videos = db.list_media(media_type="video")
            assert len(videos) == 2
            photos = db.list_media(media_type="photo")
            assert len(photos) == 1
        finally:
            db.close()

    def test_count_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_media(path="/b.jpg", media_type="photo", size=200)
            assert db.count_media() == 2
            assert db.count_media(media_type="video") == 1
        finally:
            db.close()

    def test_delete_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.delete_media(mid)
            assert db.get_media(mid) is None
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestMediaCRUD -v`
Expected: FAIL — `AttributeError: 'QuorumDB' object has no attribute 'insert_media'`

- [ ] **Step 3: Implement media CRUD methods**

Add to `src/quorum/db.py`, inside the `QuorumDB` class after `close()`:

```python
    def insert_media(
        self,
        path: str,
        media_type: str,
        size: int,
        checksum: str | None = None,
        created_at: str | None = None,
        modified_at: str | None = None,
        duration: float | None = None,
        source_device: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO media (path, type, size, checksum, created_at, modified_at, duration, source_device)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (path, media_type, size, checksum, created_at, modified_at, duration, source_device),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def upsert_media(
        self,
        path: str,
        media_type: str,
        size: int,
        checksum: str | None = None,
        created_at: str | None = None,
        modified_at: str | None = None,
        duration: float | None = None,
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
        return cur.lastrowid  # type: ignore[return-value]

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestMediaCRUD -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add media table CRUD operations to QuorumDB"
```

---

### Task 4: Metadata and tags CRUD

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for metadata and tags**

Append to `tests/test_db.py`:

```python
class TestMetadataCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_metadata(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_metadata(mid, "title", "My Video")
            db.insert_metadata(mid, "description", "A great video")
            rows = db.get_metadata(mid)
            assert len(rows) == 2
            keys = {r["key"] for r in rows}
            assert keys == {"title", "description"}
        finally:
            db.close()

    def test_get_metadata_by_key(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_metadata(mid, "title", "My Video")
            db.insert_metadata(mid, "transcript", "Hello world")
            val = db.get_metadata_value(mid, "title")
            assert val == "My Video"
            assert db.get_metadata_value(mid, "missing") is None
        finally:
            db.close()

    def test_set_metadata_upserts(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.set_metadata(mid, "title", "Version 1")
            db.set_metadata(mid, "title", "Version 2")
            assert db.get_metadata_value(mid, "title") == "Version 2"
            assert len(db.get_metadata(mid)) == 1
        finally:
            db.close()


class TestTagsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_tags(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "scene", "beach")
            db.insert_tag(mid, "scene", "sunset")
            db.insert_tag(mid, "face", "Sophia")
            tags = db.get_tags(mid)
            assert len(tags) == 3
        finally:
            db.close()

    def test_get_tags_by_category(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "scene", "beach")
            db.insert_tag(mid, "face", "Sophia")
            scenes = db.get_tags(mid, category="scene")
            assert len(scenes) == 1
            assert scenes[0]["value"] == "beach"
        finally:
            db.close()

    def test_delete_media_cascades_tags(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "scene", "beach")
            db.insert_metadata(mid, "title", "Test")
            db.delete_media(mid)
            assert db.get_tags(mid) == []
            assert db.get_metadata(mid) == []
        finally:
            db.close()

    def test_clear_tags(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "scene", "beach")
            db.insert_tag(mid, "face", "Sophia")
            db.clear_tags(mid, category="scene")
            tags = db.get_tags(mid)
            assert len(tags) == 1
            assert tags[0]["category"] == "face"
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestMetadataCRUD tests/test_db.py::TestTagsCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement metadata and tags methods**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── metadata ─────────────────────────────────────────────────────────

    def insert_metadata(self, media_id: int, key: str, value: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO metadata (media_id, key, value) VALUES (?, ?, ?)",
            (media_id, key, value),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_metadata(self, media_id: int) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM metadata WHERE media_id = ?", (media_id,)
        ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def get_metadata_value(self, media_id: int, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE media_id = ? AND key = ?",
            (media_id, key),
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, media_id: int, key: str, value: str) -> None:
        existing = self.conn.execute(
            "SELECT id FROM metadata WHERE media_id = ? AND key = ?",
            (media_id, key),
        ).fetchone()
        if existing:
            self.conn.execute(
                "UPDATE metadata SET value = ? WHERE id = ?",
                (value, existing[0]),
            )
        else:
            self.conn.execute(
                "INSERT INTO metadata (media_id, key, value) VALUES (?, ?, ?)",
                (media_id, key, value),
            )
        self.conn.commit()

    # ── tags ─────────────────────────────────────────────────────────────

    def insert_tag(self, media_id: int, category: str, value: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO tags (media_id, category, value) VALUES (?, ?, ?)",
            (media_id, category, value),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_tags(self, media_id: int, category: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if category:
            rows = self.conn.execute(
                "SELECT * FROM tags WHERE media_id = ? AND category = ?",
                (media_id, category),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM tags WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def clear_tags(self, media_id: int, category: str | None = None) -> None:
        if category:
            self.conn.execute(
                "DELETE FROM tags WHERE media_id = ? AND category = ?",
                (media_id, category),
            )
        else:
            self.conn.execute("DELETE FROM tags WHERE media_id = ?", (media_id,))
        self.conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestMetadataCRUD tests/test_db.py::TestTagsCRUD -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add metadata and tags CRUD to QuorumDB"
```

---

### Task 5: Signals and feedback CRUD

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for signals and feedback**

Append to `tests/test_db.py`:

```python
class TestSignalsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_signals(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.9, "year in filename", "2024-01-01T00:00:00")
            db.insert_signal(mid, "vision", "The Matrix", 0.7, "Neo visible", "2024-01-01T00:00:00")
            sigs = db.get_signals(mid)
            assert len(sigs) == 2
        finally:
            db.close()

    def test_get_signals_by_name(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.9, "", "2024-01-01T00:00:00")
            db.insert_signal(mid, "vision", "The Matrix", 0.7, "", "2024-01-01T00:00:00")
            sigs = db.get_signals(mid, signal_name="filename")
            assert len(sigs) == 1
            assert sigs[0]["confidence"] == 0.9
        finally:
            db.close()


class TestFeedbackCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_feedback(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_feedback(mid, "approve", "The Matrix (1999)", created_at="2024-01-01T00:00:00")
            fb = db.get_feedback(mid)
            assert len(fb) == 1
            assert fb[0]["action"] == "approve"
        finally:
            db.close()

    def test_insert_correction(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_feedback(
                mid, "correct", "The Matri (1999)",
                correction="The Matrix (1999)",
                created_at="2024-01-01T00:00:00",
            )
            fb = db.get_feedback(mid)
            assert fb[0]["correction"] == "The Matrix (1999)"
        finally:
            db.close()

    def test_count_feedback(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            m1 = db.insert_media(path="/a.mkv", media_type="video", size=100)
            m2 = db.insert_media(path="/b.mkv", media_type="video", size=100)
            db.insert_feedback(m1, "approve", "X", created_at="2024-01-01T00:00:00")
            db.insert_feedback(m2, "reject", "Y", created_at="2024-01-01T00:00:00")
            db.insert_feedback(m2, "correct", "Z", correction="W", created_at="2024-01-01T00:00:00")
            assert db.count_feedback() == 3
            assert db.count_feedback(action="approve") == 1
            assert db.count_feedback(action="correct") == 1
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestSignalsCRUD tests/test_db.py::TestFeedbackCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement signals and feedback methods**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── signals ──────────────────────────────────────────────────────────

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
        return cur.lastrowid  # type: ignore[return-value]

    def get_signals(self, media_id: int, signal_name: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if signal_name:
            rows = self.conn.execute(
                "SELECT * FROM signals WHERE media_id = ? AND signal_name = ?",
                (media_id, signal_name),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM signals WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ── feedback ─────────────────────────────────────────────────────────

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
        return cur.lastrowid  # type: ignore[return-value]

    def get_feedback(self, media_id: int) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT * FROM feedback WHERE media_id = ?", (media_id,)
        ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def count_feedback(self, action: str | None = None) -> int:
        if action:
            return self.conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE action = ?", (action,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestSignalsCRUD tests/test_db.py::TestFeedbackCRUD -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add signals and feedback CRUD to QuorumDB"
```

---

### Task 6: Actions and processing CRUD

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for actions and processing**

Append to `tests/test_db.py`:

```python
import json


class TestActionsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_list_actions(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_action(
                operation="move",
                source_path="/src/a.mkv",
                dest_path="/dst/a.mkv",
                created_at="2024-01-01T00:00:00",
            )
            db.insert_action(
                operation="quarantine",
                source_path="/src/b.mkv",
                dest_path="/quarantine/b.mkv",
                created_at="2024-01-01T00:01:00",
            )
            actions = db.list_actions()
            assert len(actions) == 2
        finally:
            db.close()

    def test_list_actions_reversed(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_action(operation="move", source_path="/first", created_at="2024-01-01T00:00:00")
            db.insert_action(operation="move", source_path="/second", created_at="2024-01-01T00:01:00")
            actions = db.list_actions(reverse=True)
            assert actions[0]["source_path"] == "/second"
        finally:
            db.close()

    def test_action_metadata_json(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            meta = json.dumps({"confidence": 0.87, "title": "Test"})
            db.insert_action(
                operation="move",
                source_path="/a.mkv",
                metadata=meta,
                created_at="2024-01-01T00:00:00",
            )
            actions = db.list_actions()
            parsed = json.loads(actions[0]["metadata"])
            assert parsed["confidence"] == 0.87
        finally:
            db.close()


class TestProcessingCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_job(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            jid = db.insert_job("enrich", started_at="2024-01-01T00:00:00")
            job = db.get_job(jid)
            assert job is not None
            assert job["job_type"] == "enrich"
            assert job["status"] == "pending"
            assert job["progress"] == 0.0
        finally:
            db.close()

    def test_update_job_progress(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            jid = db.insert_job("enrich", started_at="2024-01-01T00:00:00")
            db.update_job(jid, status="running", progress=0.5)
            job = db.get_job(jid)
            assert job["status"] == "running"
            assert job["progress"] == 0.5
        finally:
            db.close()

    def test_complete_job(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            jid = db.insert_job("enrich", started_at="2024-01-01T00:00:00")
            db.update_job(jid, status="completed", progress=1.0, completed_at="2024-01-01T00:10:00")
            job = db.get_job(jid)
            assert job["status"] == "completed"
            assert job["completed_at"] == "2024-01-01T00:10:00"
        finally:
            db.close()

    def test_fail_job_with_error(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            jid = db.insert_job("enrich", started_at="2024-01-01T00:00:00")
            db.update_job(jid, status="failed", error="disk full")
            job = db.get_job(jid)
            assert job["status"] == "failed"
            assert job["error"] == "disk full"
        finally:
            db.close()

    def test_list_jobs_by_status(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            j1 = db.insert_job("enrich", started_at="2024-01-01T00:00:00")
            j2 = db.insert_job("auto", started_at="2024-01-01T00:00:00")
            db.update_job(j1, status="completed")
            pending = db.list_jobs(status="pending")
            assert len(pending) == 1
            assert pending[0]["job_type"] == "auto"
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestActionsCRUD tests/test_db.py::TestProcessingCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement actions and processing methods**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── actions ──────────────────────────────────────────────────────────

    def insert_action(
        self,
        operation: str,
        source_path: str,
        dest_path: str | None = None,
        metadata: str | None = None,
        reversible: bool = True,
        created_at: str = "",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO actions (operation, source_path, dest_path, metadata, reversible, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (operation, source_path, dest_path, metadata, int(reversible), created_at),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def list_actions(self, reverse: bool = False) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        order = "DESC" if reverse else "ASC"
        rows = self.conn.execute(f"SELECT * FROM actions ORDER BY id {order}").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    # ── processing ───────────────────────────────────────────────────────

    def insert_job(
        self,
        job_type: str,
        media_id: int | None = None,
        started_at: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO processing (media_id, job_type, started_at) VALUES (?, ?, ?)",
            (media_id, job_type, started_at),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

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
        updates: list[str] = []
        params: list = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if progress is not None:
            updates.append("progress = ?")
            params.append(progress)
        if error is not None:
            updates.append("error = ?")
            params.append(error)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if not updates:
            return
        params.append(job_id)
        self.conn.execute(f"UPDATE processing SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()

    def list_jobs(self, status: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if status:
            rows = self.conn.execute(
                "SELECT * FROM processing WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM processing ORDER BY id").fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestActionsCRUD tests/test_db.py::TestProcessingCRUD -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add actions and processing CRUD to QuorumDB"
```

---

### Task 7: Events CRUD

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for events**

Append to `tests/test_db.py`:

```python
class TestEventsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_event(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(
                name="Beach Day 2024",
                start_time="2024-06-15T10:00:00",
                end_time="2024-06-15T18:00:00",
            )
            event = db.get_event(eid)
            assert event is not None
            assert event["name"] == "Beach Day 2024"
            assert event["auto_detected"] == 1
        finally:
            db.close()

    def test_list_events(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_event(name="Event A", start_time="2024-01-01T00:00:00")
            db.insert_event(name="Event B", start_time="2024-06-01T00:00:00")
            events = db.list_events()
            assert len(events) == 2
        finally:
            db.close()

    def test_assign_media_to_event(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
            row = db.get_media(mid)
            assert row["event_id"] == eid
        finally:
            db.close()

    def test_get_event_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            m1 = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            m2 = db.insert_media(path="/b.mkv", media_type="video", size=200)
            db.insert_media(path="/c.jpg", media_type="photo", size=300)
            db.assign_media_to_event(m1, eid)
            db.assign_media_to_event(m2, eid)
            media = db.get_event_media(eid)
            assert len(media) == 2
        finally:
            db.close()

    def test_unlink_media_from_event(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
            db.unlink_media_from_event(mid)
            row = db.get_media(mid)
            assert row["event_id"] is None
        finally:
            db.close()

    def test_update_event(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Old Name", start_time="2024-01-01T00:00:00")
            db.update_event(eid, name="New Name")
            event = db.get_event(eid)
            assert event["name"] == "New Name"
        finally:
            db.close()

    def test_delete_event_unlinks_media(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
            db.delete_event(eid)
            assert db.get_event(eid) is None
            row = db.get_media(mid)
            assert row["event_id"] is None
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestEventsCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement events methods**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── events ───────────────────────────────────────────────────────────

    def insert_event(
        self,
        name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        auto_detected: bool = True,
        metadata: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (name, start_time, end_time, auto_detected, metadata)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, start_time, end_time, int(auto_detected), metadata),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

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

    def update_event(
        self,
        event_id: int,
        name: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        metadata: str | None = None,
    ) -> None:
        updates: list[str] = []
        params: list = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if start_time is not None:
            updates.append("start_time = ?")
            params.append(start_time)
        if end_time is not None:
            updates.append("end_time = ?")
            params.append(end_time)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(metadata)
        if not updates:
            return
        params.append(event_id)
        self.conn.execute(f"UPDATE events SET {', '.join(updates)} WHERE id = ?", params)
        self.conn.commit()

    def delete_event(self, event_id: int) -> None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestEventsCRUD -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add events CRUD to QuorumDB"
```

---

### Task 8: Embeddings CRUD (sqlite-vec preparation)

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Add sqlite-vec dependency**

In `pyproject.toml`, add `"sqlite-vec>=0.1"` to the `dependencies` list.

- [ ] **Step 2: Write failing tests for embeddings**

Append to `tests/test_db.py`:

```python
import struct


def _make_embedding(dim: int = 4) -> bytes:
    return struct.pack(f"{dim}f", *[float(i) / dim for i in range(dim)])


class TestEmbeddingsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_embeddings(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            vec = _make_embedding()
            db.insert_embedding(mid, "face", vec, label="Sophia")
            embs = db.get_embeddings(mid)
            assert len(embs) == 1
            assert embs[0]["type"] == "face"
            assert embs[0]["label"] == "Sophia"
            assert embs[0]["vector"] == vec
        finally:
            db.close()

    def test_get_embeddings_by_type(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_embedding(mid, "face", _make_embedding(), label="Sophia")
            db.insert_embedding(mid, "scene", _make_embedding())
            face_embs = db.get_embeddings(mid, emb_type="face")
            assert len(face_embs) == 1
        finally:
            db.close()

    def test_delete_embeddings(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_embedding(mid, "face", _make_embedding())
            db.delete_embeddings(mid, emb_type="face")
            assert db.get_embeddings(mid) == []
        finally:
            db.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestEmbeddingsCRUD -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 4: Implement embeddings methods**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── embeddings ───────────────────────────────────────────────────────

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
        return cur.lastrowid  # type: ignore[return-value]

    def get_embeddings(self, media_id: int, emb_type: str | None = None) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        if emb_type:
            rows = self.conn.execute(
                "SELECT * FROM embeddings WHERE media_id = ? AND type = ?",
                (media_id, emb_type),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM embeddings WHERE media_id = ?", (media_id,)
            ).fetchall()
        self.conn.row_factory = None
        return [dict(r) for r in rows]

    def delete_embeddings(self, media_id: int, emb_type: str | None = None) -> None:
        if emb_type:
            self.conn.execute(
                "DELETE FROM embeddings WHERE media_id = ? AND type = ?",
                (media_id, emb_type),
            )
        else:
            self.conn.execute("DELETE FROM embeddings WHERE media_id = ?", (media_id,))
        self.conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestEmbeddingsCRUD -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/quorum/db.py tests/test_db.py
git commit -m "feat: add embeddings CRUD and sqlite-vec dependency"
```

---

### Task 9: Aggregate statistics for dashboard

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`

- [ ] **Step 1: Write failing tests for stats**

Append to `tests/test_db.py`:

```python
class TestStats:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_stats_empty_db(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            stats = db.stats()
            assert stats["total_media"] == 0
            assert stats["by_type"] == {}
            assert stats["total_size"] == 0
            assert stats["total_events"] == 0
            assert stats["total_tags"] == 0
            assert stats["pending_jobs"] == 0
        finally:
            db.close()

    def test_stats_populated_db(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(path="/a.mkv", media_type="video", size=1000)
            db.insert_media(path="/b.mkv", media_type="video", size=2000)
            db.insert_media(path="/c.jpg", media_type="photo", size=500)
            mid = 1
            db.insert_tag(mid, "scene", "beach")
            db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            db.insert_job("enrich")

            stats = db.stats()
            assert stats["total_media"] == 3
            assert stats["by_type"]["video"] == 2
            assert stats["by_type"]["photo"] == 1
            assert stats["total_size"] == 3500
            assert stats["total_events"] == 1
            assert stats["total_tags"] == 1
            assert stats["pending_jobs"] == 1
        finally:
            db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestStats -v`
Expected: FAIL — `AttributeError: 'QuorumDB' object has no attribute 'stats'`

- [ ] **Step 3: Implement stats method**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    # ── aggregate stats ──────────────────────────────────────────────────

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestStats -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/db.py tests/test_db.py
git commit -m "feat: add aggregate stats method to QuorumDB"
```

---

### Task 10: Context manager and db_path config

**Files:**
- Modify: `tests/test_db.py`
- Modify: `src/quorum/db.py`
- Modify: `src/quorum/config.py`

- [ ] **Step 1: Write failing tests for context manager**

Append to `tests/test_db.py`:

```python
class TestContextManager:
    def test_context_manager_closes(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
        # Verify the connection is closed by trying to use it
        import sqlite3 as _sql
        with pytest.raises(_sql.ProgrammingError):
            db.conn.execute("SELECT 1")

    def test_context_manager_on_error(self, tmp_db_path: Path) -> None:
        try:
            with QuorumDB(tmp_db_path) as db:
                db.insert_media(path="/a.mkv", media_type="video", size=100)
                raise ValueError("test error")
        except ValueError:
            pass
        # DB should still be closed
        import sqlite3 as _sql
        with pytest.raises(_sql.ProgrammingError):
            db.conn.execute("SELECT 1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::TestContextManager -v`
Expected: FAIL — `TypeError: ... __enter__`

- [ ] **Step 3: Add context manager to QuorumDB**

Add to `src/quorum/db.py` inside `QuorumDB` (after `__init__`):

```python
    def __enter__(self) -> QuorumDB:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py::TestContextManager -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Add db_path to config**

In `src/quorum/config.py`, add a `db_path` field to the `Settings` class:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cpu_only: bool = False
    db_path: Path = Path("quorum.db")
    # ... rest of fields unchanged
```

- [ ] **Step 6: Commit**

```bash
git add src/quorum/db.py src/quorum/config.py tests/test_db.py
git commit -m "feat: add context manager to QuorumDB and db_path config"
```

---

### Task 11: CLI `quorum db stats` command

**Files:**
- Create: `tests/test_cli_db.py`
- Modify: `src/quorum/cli.py`

- [ ] **Step 1: Write failing test for `quorum db stats`**

Create `tests/test_cli_db.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from quorum.cli import app

runner = CliRunner()


class TestDBStats:
    def test_stats_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["db", "stats", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "0" in result.output  # total media = 0

    def test_stats_with_data(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB

        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=1000)
            db.insert_media(path="/b.jpg", media_type="photo", size=500)

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["db", "stats", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "2" in result.output  # total media
        assert "video" in result.output
        assert "photo" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_db.py -v`
Expected: FAIL — no `db` subcommand

- [ ] **Step 3: Add `db` subcommand group to CLI**

In `src/quorum/cli.py`, add after the existing imports:

```python
from .db import QuorumDB
```

Then add the `db` subcommand group:

```python
db_app = typer.Typer(help="Manage the Quorum metadata index.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command()
def stats(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show summary statistics for the metadata index."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        st = db.stats()
    t = Table(title="Quorum Index Stats")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("total media files", str(st["total_media"]))
    for media_type, count in sorted(st["by_type"].items()):
        t.add_row(f"  {media_type}", str(count))
    t.add_row("total size (bytes)", f"{st['total_size']:,}")
    t.add_row("events", str(st["total_events"]))
    t.add_row("tags", str(st["total_tags"]))
    t.add_row("pending jobs", str(st["pending_jobs"]))
    console.print(t)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_db.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/quorum/cli.py tests/test_cli_db.py
git commit -m "feat: add 'quorum db stats' CLI command"
```

---

### Task 12: CLI `quorum db migrate` command

**Files:**
- Modify: `tests/test_cli_db.py`
- Modify: `src/quorum/db.py`
- Modify: `src/quorum/cli.py`

- [ ] **Step 1: Write failing test for migration from .nfo files**

Append to `tests/test_cli_db.py`:

```python
import xml.etree.ElementTree as ET


def _write_nfo(path: Path, title: str, plot: str, year: int | None = None) -> None:
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "plot").text = plot
    if year:
        ET.SubElement(root, "year").text = str(year)
    tree = ET.ElementTree(root)
    tree.write(str(path), encoding="unicode", xml_declaration=True)


class TestDBMigrate:
    def test_migrate_imports_nfo(self, tmp_path: Path) -> None:
        video = tmp_path / "Movies" / "Test (2024)" / "Test (2024).mkv"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"\x00" * 100)

        nfo = video.with_suffix(".nfo")
        _write_nfo(nfo, "Test Movie", "A great movie", 2024)

        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")

        result = runner.invoke(app, ["db", "migrate", str(tmp_path), "--config", str(config_path)])
        assert result.exit_code == 0

        from quorum.db import QuorumDB
        with QuorumDB(db_path) as db:
            media = db.list_media()
            assert len(media) >= 1
            mid = media[0]["id"]
            title = db.get_metadata_value(mid, "title")
            assert title == "Test Movie"

    def test_migrate_imports_faces_db(self, tmp_path: Path) -> None:
        import sqlite3

        faces_db_path = tmp_path / "faces.db"
        conn = sqlite3.connect(str(faces_db_path))
        conn.execute("""
            CREATE TABLE faces (
                id INTEGER PRIMARY KEY,
                photo_path TEXT NOT NULL,
                bbox_x REAL NOT NULL, bbox_y REAL NOT NULL,
                bbox_w REAL NOT NULL, bbox_h REAL NOT NULL,
                embedding BLOB NOT NULL,
                cluster_id INTEGER,
                label TEXT,
                label_source TEXT,
                confidence REAL
            )
        """)
        conn.execute(
            "INSERT INTO faces (photo_path, bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id, label)"
            " VALUES (?, 0.1, 0.2, 0.3, 0.4, ?, 1, 'Sophia')",
            (str(tmp_path / "photo.jpg"), b"\x00" * 64),
        )
        conn.commit()
        conn.close()

        photo = tmp_path / "photo.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 96)

        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")

        result = runner.invoke(app, ["db", "migrate", str(tmp_path), "--config", str(config_path)])
        assert result.exit_code == 0

        from quorum.db import QuorumDB
        with QuorumDB(db_path) as db:
            media = db.list_media()
            photo_media = [m for m in media if m["type"] == "photo"]
            assert len(photo_media) >= 1
            tags = db.get_tags(photo_media[0]["id"], category="face")
            assert any(t["value"] == "Sophia" for t in tags)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_db.py::TestDBMigrate -v`
Expected: FAIL — no `migrate` subcommand

- [ ] **Step 3: Add migrate function to db.py**

Add to `src/quorum/db.py`, outside the class:

```python
import xml.etree.ElementTree as ET


_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg"}
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".webp"}


def migrate_from_legacy(db: QuorumDB, root: Path) -> dict[str, int]:
    counts = {"nfo_imported": 0, "faces_imported": 0, "media_indexed": 0}

    # Index media files and import .nfo sidecars
    for ext in _VIDEO_EXTS | _PHOTO_EXTS:
        for f in root.rglob(f"*{ext}"):
            if ".quorum-cache" in f.parts:
                continue
            media_type = "video" if ext in _VIDEO_EXTS else "photo"
            mid = db.upsert_media(
                path=str(f),
                media_type=media_type,
                size=f.stat().st_size,
            )
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

    # Import watch-state.json
    watch_state = root / "watch-state.json"
    if watch_state.exists():
        import json
        try:
            state = json.loads(watch_state.read_text(encoding="utf-8"))
            for fpath, info in state.get("files", {}).items():
                existing = db.get_media_by_path(fpath)
                if existing:
                    db.set_metadata(existing["id"], "watch_status", info.get("status", "done"))
        except (json.JSONDecodeError, KeyError):
            pass

    # Import faces.db
    faces_db = root / "faces.db"
    if faces_db.exists():
        import sqlite3 as _sql
        fconn = _sql.connect(str(faces_db))
        try:
            rows = fconn.execute(
                "SELECT photo_path, embedding, cluster_id, label FROM faces"
            ).fetchall()
            for photo_path, embedding, cluster_id, label in rows:
                existing = db.get_media_by_path(photo_path)
                if not existing:
                    p = Path(photo_path)
                    if p.exists():
                        mid = db.upsert_media(
                            path=photo_path,
                            media_type="photo",
                            size=p.stat().st_size,
                        )
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
```

- [ ] **Step 4: Add migrate CLI command**

In `src/quorum/cli.py`, add to the `db_app` group (add import for `migrate_from_legacy`):

```python
from .db import QuorumDB, migrate_from_legacy
```

And the command:

```python
@db_app.command()
def migrate(
    root: Path = typer.Argument(..., help="Library root to scan for legacy data."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Import existing .nfo sidecars, faces.db, and watch-state into quorum.db."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        counts = migrate_from_legacy(db, root)
    console.print(f"[green]Migration complete:[/]")
    console.print(f"  Media files indexed: {counts['media_indexed']}")
    console.print(f"  .nfo files imported: {counts['nfo_imported']}")
    console.print(f"  Face records imported: {counts['faces_imported']}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_db.py::TestDBMigrate -v`
Expected: All 2 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/quorum/db.py src/quorum/cli.py tests/test_cli_db.py
git commit -m "feat: add 'quorum db migrate' to import legacy data into quorum.db"
```

---

### Task 13: CLI `quorum db export` command

**Files:**
- Modify: `tests/test_cli_db.py`
- Modify: `src/quorum/db.py`
- Modify: `src/quorum/cli.py`

- [ ] **Step 1: Write failing test for export**

Append to `tests/test_cli_db.py`:

```python
class TestDBExport:
    def test_export_json(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB

        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=1000)
            db.insert_metadata(mid, "title", "Test Video")
            db.insert_tag(mid, "scene", "beach")

        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        output = tmp_path / "export.json"

        result = runner.invoke(app, ["db", "export", str(output), "--config", str(config_path)])
        assert result.exit_code == 0
        assert output.exists()

        import json
        data = json.loads(output.read_text(encoding="utf-8"))
        assert len(data["media"]) == 1
        assert data["media"][0]["path"] == "/a.mkv"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_db.py::TestDBExport -v`
Expected: FAIL

- [ ] **Step 3: Add export method to QuorumDB**

Add to `src/quorum/db.py` inside `QuorumDB`:

```python
    def export_all(self) -> dict:
        media = self.list_media()
        for m in media:
            m["metadata"] = self.get_metadata(m["id"])
            m["tags"] = self.get_tags(m["id"])
            m["signals"] = self.get_signals(m["id"])
        events = self.list_events()
        return {
            "media": media,
            "events": events,
            "stats": self.stats(),
        }
```

- [ ] **Step 4: Add export CLI command**

In `src/quorum/cli.py`, add to the `db_app` group:

```python
import json as _json

@db_app.command("export")
def db_export(
    output: Path = typer.Argument(..., help="Output JSON file path."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Export the entire metadata index to a JSON file."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        data = db.export_all()
    output.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Exported to {output}[/]")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_db.py::TestDBExport -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/quorum/db.py src/quorum/cli.py tests/test_cli_db.py
git commit -m "feat: add 'quorum db export' command"
```

---

### Task 14: Full test suite run and final cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (approximately 33 tests across 2 test files)

- [ ] **Step 2: Run ruff linter**

Run: `python -m ruff check src/quorum/db.py tests/`
Expected: No lint errors (or fix any that appear)

- [ ] **Step 3: Verify CLI works end-to-end**

Run: `python -m quorum db stats`
Expected: Shows stats table (all zeros for a fresh DB)

Run: `python -m quorum db --help`
Expected: Shows `stats`, `migrate`, `export` subcommands

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup for unified metadata index"
```

---

## Summary

After completing all 14 tasks, the project will have:

- `src/quorum/db.py` — complete `QuorumDB` class with CRUD for all 9 tables, stats aggregation, context manager, JSON export, and legacy migration
- `src/quorum/config.py` — `db_path` setting added
- `src/quorum/cli.py` — `quorum db stats|migrate|export` commands
- `tests/test_db.py` — ~30 unit tests covering all CRUD operations
- `tests/test_cli_db.py` — ~5 integration tests for CLI commands
- `pyproject.toml` — pytest and sqlite-vec dependencies added

This foundation supports all subsequent Ring 1 features (dashboard, search, dedup, events, review, notifications, feedback loop).

## Follow-Up Work (Separate Plans)

These items are part of the R1-1 spec but are scoped as separate plans to keep this one focused:

1. **Dual-write integration** — Update `enrich.py`, `enrich_photos.py`, `organize.py`, and `watch.py` to write to `quorum.db` alongside sidecars. Update read paths to query the index first, fall back to sidecars. This is a cross-cutting change that touches every module and should be its own plan.

2. **`quorum db rebuild`** — Re-index the entire library from source files and sidecars into a fresh `quorum.db`. Depends on the dual-write paths being established so we know exactly what to rebuild from. Plan this after dual-write integration.
