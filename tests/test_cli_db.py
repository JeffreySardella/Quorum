from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

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
        assert "0" in result.output

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
        assert "2" in result.output
        assert "video" in result.output
        assert "photo" in result.output


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
        conn.execute("""CREATE TABLE faces (
            id INTEGER PRIMARY KEY, photo_path TEXT NOT NULL,
            bbox_x REAL NOT NULL, bbox_y REAL NOT NULL,
            bbox_w REAL NOT NULL, bbox_h REAL NOT NULL,
            embedding BLOB NOT NULL, cluster_id INTEGER,
            label TEXT, label_source TEXT, confidence REAL)""")
        conn.execute(
            "INSERT INTO faces (photo_path, bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id, label)"
            " VALUES (?, 0.1, 0.2, 0.3, 0.4, ?, 1, 'Sophia')",
            (str(tmp_path / "photo.jpg"), b"\x00" * 64))
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


class TestDashboard:
    def test_dashboard_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["dashboard", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Library Overview" in result.output

    def test_dashboard_with_data(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/a.mkv", media_type="video", size=1000, created_at="2024-06-15T10:00:00")
            db.insert_tag(1, "face", "Sophia")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["dashboard", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "video" in result.output
        assert "Sophia" in result.output


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


class TestSearch:
    def test_search_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["search", "beach", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_with_results(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB

        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/beach.mkv", media_type="video", size=1000)
            db.set_metadata(mid, "title", "Beach Day")
            db.set_metadata(mid, "description", "Family playing at the beach")
            db.index_media_text(mid)
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["search", "beach", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "beach" in result.output.lower()

    def test_search_with_type_filter(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB

        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            m1 = db.insert_media(path="/beach.mkv", media_type="video", size=1000)
            db.set_metadata(m1, "title", "Beach video")
            db.index_media_text(m1)
            m2 = db.insert_media(path="/beach.jpg", media_type="photo", size=500)
            db.set_metadata(m2, "title", "Beach photo")
            db.index_media_text(m2)
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["search", "beach", "--type", "photo", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "photo" in result.output


class TestDBIndex:
    def test_index_command(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB

        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.set_metadata(mid, "title", "Test")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["db", "index", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Indexing complete" in result.output


class TestEventsCommands:
    def test_events_detect(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            db.insert_media(path="/a.jpg", media_type="photo", size=100, created_at="2024-06-15T10:00:00")
            db.insert_media(path="/b.jpg", media_type="photo", size=100, created_at="2024-06-15T10:30:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["events", "detect", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Events created: 1" in result.output

    def test_events_list(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["events", "list", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Beach Day" in result.output

    def test_events_show(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            eid = db.insert_event(name="Beach Day", start_time="2024-06-15T10:00:00")
            mid = db.insert_media(path="/beach.jpg", media_type="photo", size=100)
            db.assign_media_to_event(mid, eid)
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["events", "show", "1", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Beach Day" in result.output

    def test_events_rename(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            db.insert_event(name="Old Name", start_time="2024-06-15T10:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["events", "rename", "1", "New Name", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Renamed" in result.output


class TestDedupCommands:
    def test_dedup_scan_no_duplicates(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        f1 = tmp_path / "a.mkv"
        f2 = tmp_path / "b.mkv"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        with QuorumDB(db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=100)
            db.insert_media(path=str(f2), media_type="video", size=100)
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = runner.invoke(app, ["dedup", "scan", "--config", str(config_path)])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Duplicate clusters: 0" in result.output

    def test_dedup_scan_with_duplicates(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        f1 = tmp_path / "a.mkv"
        f2 = tmp_path / "b.mkv"
        f1.write_bytes(b"identical")
        f2.write_bytes(b"identical")
        with QuorumDB(db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=9)
            db.insert_media(path=str(f2), media_type="video", size=9)
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = runner.invoke(app, ["dedup", "scan", "--config", str(config_path)])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "Duplicate clusters: 1" in result.output


class TestReviewCommands:
    def test_review_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["review", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "No items pending" in result.output

    def test_review_with_items(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/test.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.7, "", "2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["review", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Review Queue" in result.output

    def test_review_stats(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "f", "X", 0.7, "", "2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["review", "--stats", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Pending" in result.output

    def test_approve(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.9, "", "2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["approve", "1", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Approved" in result.output

    def test_reject(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "Test", 0.5, "", "2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["reject", "1", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Rejected" in result.output

    def test_correct(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matri", 0.5, "", "2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["correct", "1", "The Matrix (1999)", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Corrected" in result.output
        assert "The Matrix (1999)" in result.output


class TestNotifyCommands:
    def test_notify_test(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
        result = runner.invoke(app, ["notify", "test", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Test notification sent" in result.output

    def test_notify_history(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
        result = runner.invoke(app, ["notify", "history", "--config", str(config_path)])
        assert result.exit_code == 0


class TestSignalsCommands:
    def test_signals_weights_default(self, tmp_path: Path) -> None:
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config_path = tmp_path / "config.toml"
            config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
            result = runner.invoke(app, ["signals", "weights", "--config", str(config_path)])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "filename" in result.output
        assert "vision" in result.output

    def test_signals_retune_no_feedback(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        result = runner.invoke(app, ["signals", "retune", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "No feedback" in result.output

    def test_signals_retune_with_feedback(self, tmp_path: Path) -> None:
        from quorum.db import QuorumDB
        db_path = tmp_path / "quorum.db"
        with QuorumDB(db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "Test", 0.9, "", "2024-01-01T00:00:00")
            db.insert_feedback(mid, "approve", "Test", created_at="2024-01-01T00:00:00")
        config_path = tmp_path / "config.toml"
        config_path.write_text(f'db_path = "{db_path.as_posix()}"', encoding="utf-8")
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            result = runner.invoke(app, ["signals", "retune", "--config", str(config_path)])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "filename" in result.output

    def test_signals_reset(self, tmp_path: Path) -> None:
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            # Create a weights file first
            import json
            (tmp_path / "signal_weights.json").write_text(json.dumps({"filename": 1.5}))
            config_path = tmp_path / "config.toml"
            config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
            result = runner.invoke(app, ["signals", "reset", "--config", str(config_path)])
        finally:
            os.chdir(old_cwd)
        assert result.exit_code == 0
        assert "reset" in result.output.lower()


class TestPluginsCommands:
    def test_plugins_list_empty(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
        result = runner.invoke(app, ["plugins", "list", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "No plugins" in result.output or "Registered" in result.output

    def test_plugins_info_missing(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text('db_path = "quorum.db"', encoding="utf-8")
        result = runner.invoke(app, ["plugins", "info", "nonexistent", "--config", str(config_path)])
        assert result.exit_code == 1
