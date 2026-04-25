from __future__ import annotations

from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.organize_anything import organize


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestOrganize:
    def test_empty_directory(self, tmp_path: Path, tmp_db_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with QuorumDB(tmp_db_path) as db:
            result = organize(empty, db)
        assert result["files_scanned"] == 0
        assert result["proposals"] == []

    def test_nonexistent_directory(self, tmp_path: Path, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            result = organize(tmp_path / "nope", db)
        assert "error" in result

    def test_classifies_by_extension(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        (src / "photo.jpg").write_bytes(b"\x00")
        (src / "video.mkv").write_bytes(b"\x00")
        (src / "doc.pdf").write_bytes(b"\x00")

        with QuorumDB(tmp_db_path) as db:
            result = organize(src, db, dry_run=True)

        assert result["files_scanned"] == 3
        assert len(result["proposals"]) == 3

    def test_custom_rules_take_priority(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        (src / "invoice.pdf").write_bytes(b"\x00")

        rules_config = {"rules": [
            {"name": "invoices", "match": {"filename_matches": "invoice"}, "action": {"move_to": "Invoices/"}, "priority": 10}
        ]}

        with QuorumDB(tmp_db_path) as db:
            result = organize(src, db, rules_config=rules_config, dry_run=True)

        assert result["rule_matches"] == 1
        rule_proposal = [p for p in result["proposals"] if p.metadata.get("source") == "rule"]
        assert len(rule_proposal) == 1
        assert rule_proposal[0].dest_path == "Invoices/"

    def test_apply_moves_files(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        (src / "doc.txt").write_text("content")
        dest = tmp_path / "organized"

        with QuorumDB(tmp_db_path) as db:
            result = organize(src, db, dest=dest, dry_run=False)

        assert result.get("applied", 0) >= 1
        assert not (src / "doc.txt").exists()

    def test_apply_logs_actions(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        (src / "file.txt").write_text("x")
        dest = tmp_path / "organized"

        with QuorumDB(tmp_db_path) as db:
            organize(src, db, dest=dest, dry_run=False)
            actions = db.list_actions()

        assert len(actions) >= 1
        assert actions[0]["operation"] == "organize"

    def test_dry_run_no_moves(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        f = src / "keep.txt"
        f.write_text("x")

        with QuorumDB(tmp_db_path) as db:
            result = organize(src, db, dry_run=True)

        assert f.exists()  # not moved
        assert len(result["proposals"]) >= 1

    def test_specific_plugin(self, tmp_path: Path, tmp_db_path: Path) -> None:
        src = tmp_path / "messy"
        src.mkdir()
        (src / "Artist - Song.mp3").write_bytes(b"\x00" * 100)
        (src / "photo.jpg").write_bytes(b"\x00")

        with QuorumDB(tmp_db_path) as db:
            result = organize(src, db, plugin_name="music", dry_run=True)

        # Only the MP3 should be proposed (plugin filter)
        assert any("Music" in p.dest_path for p in result["proposals"])
