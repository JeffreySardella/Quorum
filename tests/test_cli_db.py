from __future__ import annotations

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
