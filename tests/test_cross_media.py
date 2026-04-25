from __future__ import annotations

from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.events import enrich_event, export_event


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestEnrichEvent:
    def test_enrich_missing_event(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            result = enrich_event(db, 999)
        assert "error" in result

    def test_enrich_empty_event(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            eid = db.insert_event(name="Test", start_time="2024-06-15T10:00:00")
            result = enrich_event(db, eid)
        assert "error" in result

    def test_enrich_with_media(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            m1 = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            m2 = db.insert_media(path="/b.mkv", media_type="video", size=200)
            db.assign_media_to_event(m1, eid)
            db.assign_media_to_event(m2, eid)
            db.insert_tag(m1, "face", "Sophia")
            db.insert_tag(m2, "face", "Sophia")
            db.insert_tag(m1, "scene", "beach")
            result = enrich_event(db, eid)
        assert result["media_count"] == 2
        assert result["media_types"]["photo"] == 1
        assert result["media_types"]["video"] == 1
        assert result["people"][0][0] == "Sophia"
        assert result["scenes"][0][0] == "beach"

    def test_enrich_updates_event_metadata(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            eid = db.insert_event(name="Test", start_time="2024-01-01T00:00:00")
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
            enrich_event(db, eid)
            event = db.get_event(eid)
        assert event["metadata"] is not None


class TestExportEvent:
    def test_export_missing_event(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            result = export_event(db, 999, tmp_path / "out")
        assert "error" in result

    def test_export_with_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        src_file = tmp_path / "photo.jpg"
        src_file.write_bytes(b"\xff\xd8" + b"\x00" * 100)

        with QuorumDB(db_path) as db:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            mid = db.insert_media(path=str(src_file), media_type="photo", size=102)
            db.assign_media_to_event(mid, eid)
            output = tmp_path / "export"
            result = export_event(db, eid, output)

        assert result["files"] == 1
        assert (output / "Beach Day" / "photo.jpg").exists()
        assert (output / "Beach Day" / "event-metadata.json").exists()

    def test_export_skips_missing_files(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            eid = db.insert_event(name="Test", start_time="2024-01-01T00:00:00")
            mid = db.insert_media(path="/nonexistent.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
            result = export_event(db, eid, tmp_path / "out")
        assert result["files"] == 0
