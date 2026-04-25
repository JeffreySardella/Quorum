from __future__ import annotations

from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.backup import create_manifest, verify_manifest, diff_manifests


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestCreateManifest:
    def test_empty_db(self, tmp_path: Path, tmp_db_path: Path) -> None:
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(tmp_db_path) as db:
            result = create_manifest(db, manifest_path)
        assert result["files"] == 0
        assert manifest_path.exists()

    def test_with_media(self, tmp_path: Path, tmp_db_path: Path) -> None:
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=1000)
            db.insert_media(path="/b.jpg", media_type="photo", size=500)
            result = create_manifest(db, manifest_path)
        assert result["files"] == 2

    def test_includes_metadata_and_tags(self, tmp_path: Path, tmp_db_path: Path) -> None:
        import sqlite3
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.set_metadata(mid, "title", "Test")
            db.insert_tag(mid, "face", "Sophia")
            create_manifest(db, manifest_path)

        conn = sqlite3.connect(str(manifest_path))
        meta = conn.execute("SELECT * FROM metadata").fetchall()
        tags = conn.execute("SELECT * FROM tags").fetchall()
        conn.close()
        assert len(meta) == 1
        assert len(tags) == 1

    def test_since_filter(self, tmp_path: Path, tmp_db_path: Path) -> None:
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/old.mkv", media_type="video", size=100, created_at="2020-01-01T00:00:00")
            db.insert_media(path="/new.mkv", media_type="video", size=100, created_at="2024-06-15T00:00:00")
            result = create_manifest(db, manifest_path, since="2024-01")
        assert result["files"] == 1


class TestVerifyManifest:
    def test_verify_existing_files(self, tmp_path: Path) -> None:
        # Create a real file
        f = tmp_path / "test.txt"
        f.write_text("hello")

        # Create manifest with that file
        db_path = tmp_path / "quorum.db"
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path=str(f), media_type="video", size=f.stat().st_size)
            create_manifest(db, manifest_path)

        result = verify_manifest(manifest_path)
        assert result["verified"] == 1
        assert result["missing"] == 0

    def test_verify_missing_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        manifest_path = tmp_path / "manifest.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/nonexistent.mkv", media_type="video", size=100)
            create_manifest(db, manifest_path)

        result = verify_manifest(manifest_path)
        assert result["missing"] == 1


class TestDiffManifests:
    def test_diff_identical(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        m1 = tmp_path / "m1.db"
        m2 = tmp_path / "m2.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
            create_manifest(db, m1)
            create_manifest(db, m2)
        result = diff_manifests(m1, m2)
        assert len(result["added"]) == 0
        assert len(result["removed"]) == 0
        assert len(result["unchanged"]) == 1

    def test_diff_with_changes(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        m1 = tmp_path / "m1.db"
        m2 = tmp_path / "m2.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
            create_manifest(db, m1)
            db.insert_media(path="/b.mkv", media_type="video", size=200)
            create_manifest(db, m2)
        result = diff_manifests(m1, m2)
        assert len(result["added"]) == 1
        assert result["added"][0] == "/b.mkv"
