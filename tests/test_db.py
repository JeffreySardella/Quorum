from __future__ import annotations

from pathlib import Path

import pytest

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
