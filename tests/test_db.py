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
            db.insert_media(path="/videos/test.mkv", media_type="video", size=1024000)
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
