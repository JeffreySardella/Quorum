from __future__ import annotations

import json
import struct
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
            db.insert_feedback(mid, "correct", "The Matri (1999)", correction="The Matrix (1999)", created_at="2024-01-01T00:00:00")
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


class TestActionsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_list_actions(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_action(operation="move", source_path="/src/a.mkv", dest_path="/dst/a.mkv", created_at="2024-01-01T00:00:00")
            db.insert_action(operation="quarantine", source_path="/src/b.mkv", dest_path="/quarantine/b.mkv", created_at="2024-01-01T00:01:00")
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
            db.insert_action(operation="move", source_path="/a.mkv", metadata=meta, created_at="2024-01-01T00:00:00")
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
            db.insert_job("auto", started_at="2024-01-01T00:00:00")
            db.update_job(j1, status="completed")
            pending = db.list_jobs(status="pending")
            assert len(pending) == 1
            assert pending[0]["job_type"] == "auto"
        finally:
            db.close()


class TestEventsCRUD:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_insert_and_get_event(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            eid = db.insert_event(name="Beach Day 2024", start_time="2024-06-15T10:00:00", end_time="2024-06-15T18:00:00")
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


class TestDashboardStats:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_dashboard_stats_empty(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            stats = db.dashboard_stats()
            assert stats["total_media"] == 0
            assert stats["by_year"] == {}
            assert stats["storage_by_type"] == {}
            assert stats["top_faces"] == []
            assert stats["recent_actions"] == []
            assert stats["confidence_dist"] == [0] * 10
            assert stats["events_by_month"] == {}
        finally:
            db.close()

    def test_dashboard_stats_populated(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            db.insert_media(path="/a.mkv", media_type="video", size=1000, created_at="2024-06-15T10:00:00")
            db.insert_media(path="/b.mkv", media_type="video", size=2000, created_at="2024-07-01T10:00:00")
            db.insert_media(path="/c.jpg", media_type="photo", size=500, created_at="2023-12-25T08:00:00")

            db.insert_tag(1, "face", "Sophia")
            db.insert_tag(2, "face", "Sophia")
            db.insert_tag(3, "face", "Max")

            db.insert_signal(1, "filename", "Test", 0.85, "", "2024-01-01T00:00:00")
            db.insert_signal(2, "vision", "Test", 0.42, "", "2024-01-01T00:00:00")

            db.insert_action(operation="move", source_path="/x", dest_path="/y", created_at="2024-01-01T00:00:00")

            db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")

            stats = db.dashboard_stats()
            assert stats["total_media"] == 3
            assert stats["by_year"]["2024"] == 2
            assert stats["by_year"]["2023"] == 1
            assert stats["storage_by_type"]["video"] == 3000
            assert stats["storage_by_type"]["photo"] == 500
            assert len(stats["top_faces"]) == 2
            assert stats["top_faces"][0]["name"] == "Sophia"
            assert stats["top_faces"][0]["count"] == 2
            assert len(stats["recent_actions"]) == 1
            assert stats["confidence_dist"][8] == 1  # 0.85 -> bucket 8
            assert stats["confidence_dist"][4] == 1  # 0.42 -> bucket 4
            assert stats["events_by_month"]["2024-06"] == 1
        finally:
            db.close()


class TestContextManager:
    def test_context_manager_closes(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=100)
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
        import sqlite3 as _sql
        with pytest.raises(_sql.ProgrammingError):
            db.conn.execute("SELECT 1")


class TestSearch:
    def _make_db(self, tmp_db_path: Path) -> QuorumDB:
        return QuorumDB(tmp_db_path)

    def test_index_and_search(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/beach.mkv", media_type="video", size=100, created_at="2024-06-15T10:00:00")
            db.set_metadata(mid, "title", "Beach Day")
            db.set_metadata(mid, "description", "Family playing at the beach with sandcastles")
            db.insert_tag(mid, "scene", "beach")
            db.insert_tag(mid, "face", "Sophia")
            db.index_media_text(mid)

            results = db.search_text("beach")
            assert len(results) == 1
            assert results[0]["id"] == mid
        finally:
            db.close()

    def test_search_returns_no_results(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/beach.mkv", media_type="video", size=100)
            db.set_metadata(mid, "title", "Beach Day")
            db.index_media_text(mid)

            results = db.search_text("mountains")
            assert len(results) == 0
        finally:
            db.close()

    def test_search_filter_by_type(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            m1 = db.insert_media(path="/beach.mkv", media_type="video", size=100)
            db.set_metadata(m1, "title", "Beach video")
            db.index_media_text(m1)

            m2 = db.insert_media(path="/beach.jpg", media_type="photo", size=50)
            db.set_metadata(m2, "title", "Beach photo")
            db.index_media_text(m2)

            results = db.search_text("beach", media_type="photo")
            assert len(results) == 1
            assert results[0]["type"] == "photo"
        finally:
            db.close()

    def test_search_filter_by_date(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            m1 = db.insert_media(path="/old.mkv", media_type="video", size=100, created_at="2020-01-01T00:00:00")
            db.set_metadata(m1, "title", "Beach trip old")
            db.index_media_text(m1)

            m2 = db.insert_media(path="/new.mkv", media_type="video", size=100, created_at="2024-06-15T00:00:00")
            db.set_metadata(m2, "title", "Beach trip new")
            db.index_media_text(m2)

            results = db.search_text("beach", after="2023-01-01")
            assert len(results) == 1
            assert "new" in results[0]["path"]
        finally:
            db.close()

    def test_reindex_all(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            m1 = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.set_metadata(m1, "title", "First video")
            m2 = db.insert_media(path="/b.mkv", media_type="video", size=100)
            db.set_metadata(m2, "title", "Second video")

            count = db.reindex_all()
            assert count == 2

            results = db.search_text("first")
            assert len(results) == 1
        finally:
            db.close()

    def test_search_includes_tags(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/family.mkv", media_type="video", size=100)
            db.set_metadata(mid, "title", "Home video")
            db.insert_tag(mid, "face", "Sophia")
            db.insert_tag(mid, "scene", "birthday party")
            db.index_media_text(mid)

            results = db.search_text("Sophia")
            assert len(results) == 1

            results = db.search_text("birthday")
            assert len(results) == 1
        finally:
            db.close()

    def test_search_snippet(self, tmp_db_path: Path) -> None:
        db = self._make_db(tmp_db_path)
        try:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.set_metadata(mid, "description", "A beautiful sunset at the beach")
            db.index_media_text(mid)

            results = db.search_text("sunset")
            assert len(results) == 1
            assert "snippet" in results[0]
            assert "sunset" in results[0]["snippet"].lower()
        finally:
            db.close()
