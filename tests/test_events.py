from __future__ import annotations

from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.events import detect_events, _parse_dt, _generate_event_name


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestParseDateTime:
    def test_iso_format(self) -> None:
        dt = _parse_dt("2024-06-15T10:30:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.hour == 10

    def test_space_format(self) -> None:
        dt = _parse_dt("2024-06-15 10:30:00")
        assert dt is not None

    def test_date_only(self) -> None:
        dt = _parse_dt("2024-06-15")
        assert dt is not None
        assert dt.hour == 0

    def test_none(self) -> None:
        assert _parse_dt(None) is None

    def test_invalid(self) -> None:
        assert _parse_dt("not-a-date") is None


class TestDetectEvents:
    def test_no_media(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            result = detect_events(db)
        assert result["events_created"] == 0
        assert result["media_assigned"] == 0

    def test_single_cluster(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/a.jpg", media_type="photo", size=100, created_at="2024-06-15T10:00:00")
            db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T10:30:00")
            db.insert_media(path="/c.jpg", media_type="photo", size=100, created_at="2024-06-15T11:00:00")
            result = detect_events(db)
        assert result["events_created"] == 1
        assert result["media_assigned"] == 3

    def test_two_clusters_by_time_gap(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            # Morning cluster
            db.insert_media(path="/morning1.jpg", media_type="photo", size=100, created_at="2024-06-15T09:00:00")
            db.insert_media(path="/morning2.jpg", media_type="photo", size=100, created_at="2024-06-15T09:30:00")
            # Evening cluster (3 hours later)
            db.insert_media(path="/evening1.jpg", media_type="photo", size=100, created_at="2024-06-15T14:00:00")
            db.insert_media(path="/evening2.jpg", media_type="photo", size=100, created_at="2024-06-15T14:30:00")
            result = detect_events(db, gap_hours=2.0)
        assert result["events_created"] == 2
        assert result["media_assigned"] == 4

    def test_merge_by_shared_faces(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            # Two clusters that share faces — should merge
            m1 = db.insert_media(path="/a.jpg", media_type="photo", size=100, created_at="2024-06-15T09:00:00")
            m2 = db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T09:30:00")
            # 3 hour gap
            m3 = db.insert_media(path="/c.jpg", media_type="photo", size=100, created_at="2024-06-15T13:00:00")

            # Both clusters have Sophia and Max
            db.insert_tag(m1, "face", "Sophia")
            db.insert_tag(m1, "face", "Max")
            db.insert_tag(m2, "face", "Sophia")
            db.insert_tag(m3, "face", "Sophia")
            db.insert_tag(m3, "face", "Max")

            result = detect_events(db, gap_hours=2.0)
        assert result["events_created"] == 1  # merged because of face overlap
        assert result["media_assigned"] == 3

    def test_no_merge_without_enough_faces(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            m1 = db.insert_media(path="/a.jpg", media_type="photo", size=100, created_at="2024-06-15T09:00:00")
            m2 = db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T14:00:00")
            # Only 1 shared face — not enough to merge
            db.insert_tag(m1, "face", "Sophia")
            db.insert_tag(m2, "face", "Sophia")

            result = detect_events(db, gap_hours=2.0)
        assert result["events_created"] == 2

    def test_skips_already_assigned(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            m1 = db.insert_media(path="/a.jpg", media_type="photo", size=100, created_at="2024-06-15T10:00:00")
            m2 = db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T10:30:00")
            eid = db.insert_event(name="Existing", start_time="2024-06-15T10:00:00")
            db.assign_media_to_event(m1, eid)
            db.assign_media_to_event(m2, eid)

            result = detect_events(db)
        assert result["events_created"] == 0

    def test_media_without_timestamps_ignored(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/a.jpg", media_type="photo", size=100)  # no created_at
            db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T10:00:00")
            result = detect_events(db)
        assert result["events_created"] == 1
        assert result["media_assigned"] == 1

    def test_event_naming_with_scene_tag(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/beach.jpg", media_type="photo", size=100, created_at="2024-06-15T10:00:00")
            db.insert_tag(mid, "scene", "beach")
            detect_events(db)
            events = db.list_events()
        assert len(events) == 1
        assert "beach" in events[0]["name"].lower() or "2024-06-15" in events[0]["name"]


class TestGenerateEventName:
    def test_fallback_with_scene(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "scene", "birthday party")
            name = _generate_event_name(db, [mid], "2024-06-15T10:00:00")
        assert "Birthday Party" in name

    def test_fallback_with_face(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            db.insert_tag(mid, "face", "Sophia")
            name = _generate_event_name(db, [mid], "2024-06-15T10:00:00")
        assert "Sophia" in name

    def test_fallback_date_only(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.jpg", media_type="photo", size=100)
            name = _generate_event_name(db, [mid], "2024-06-15T10:00:00")
        assert "2024-06-15" in name
