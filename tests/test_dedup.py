from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.dedup import (
    DedupReport, DupCluster, DupFile,
    scan_duplicates, save_report, load_report, apply_dedup,
    _compute_checksum, _pick_best,
)


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestChecksum:
    def test_compute_checksum(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        cs = _compute_checksum(f)
        assert cs is not None
        assert len(cs) == 64  # SHA-256 hex

    def test_same_content_same_checksum(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("same content")
        f2.write_text("same content")
        assert _compute_checksum(f1) == _compute_checksum(f2)

    def test_different_content_different_checksum(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert _compute_checksum(f1) != _compute_checksum(f2)

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _compute_checksum(tmp_path / "nope.txt") is None


class TestPickBest:
    def test_picks_largest(self) -> None:
        files = [
            DupFile(media_id=1, path="/a.mkv", size=100, media_type="video"),
            DupFile(media_id=2, path="/b.mkv", size=500, media_type="video"),
            DupFile(media_id=3, path="/c.mkv", size=200, media_type="video"),
        ]
        assert _pick_best(files) == 2


class TestScanDuplicates:
    def test_no_duplicates(self, tmp_path: Path, tmp_db_path: Path) -> None:
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=100)
            db.insert_media(path=str(f2), media_type="video", size=100)
            report = scan_duplicates(db)
        assert len(report.clusters) == 0
        assert report.total_duplicates == 0

    def test_exact_duplicates(self, tmp_path: Path, tmp_db_path: Path) -> None:
        f1 = tmp_path / "a.mkv"
        f2 = tmp_path / "b.mkv"
        f1.write_bytes(b"identical content here")
        f2.write_bytes(b"identical content here")
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=f1.stat().st_size)
            db.insert_media(path=str(f2), media_type="video", size=f2.stat().st_size)
            report = scan_duplicates(db)
        assert len(report.clusters) == 1
        assert report.clusters[0].strategy == "exact_checksum"
        assert report.total_duplicates == 1

    def test_three_way_duplicate(self, tmp_path: Path, tmp_db_path: Path) -> None:
        content = b"same content"
        for name in ["a.mkv", "b.mkv", "c.mkv"]:
            (tmp_path / name).write_bytes(content)
        with QuorumDB(tmp_db_path) as db:
            for name in ["a.mkv", "b.mkv", "c.mkv"]:
                db.insert_media(path=str(tmp_path / name), media_type="video", size=len(content))
            report = scan_duplicates(db)
        assert len(report.clusters) == 1
        assert len(report.clusters[0].files) == 3
        assert report.total_duplicates == 2

    def test_recommends_largest_as_keep(self, tmp_path: Path, tmp_db_path: Path) -> None:
        content = b"same"
        f1 = tmp_path / "small.mkv"
        f2 = tmp_path / "big.mkv"
        f1.write_bytes(content)
        f2.write_bytes(content)
        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=100)
            db.insert_media(path=str(f2), media_type="video", size=5000)
            report = scan_duplicates(db)
        assert report.clusters[0].recommended_keep == 2  # larger size in DB


class TestReportSerialization:
    def test_round_trip(self, tmp_path: Path) -> None:
        report = DedupReport(
            scanned_at="2024-01-01T00:00:00",
            total_files_scanned=10,
            total_duplicates=2,
            clusters=[
                DupCluster(id=1, strategy="exact_checksum", recommended_keep=1, files=[
                    DupFile(media_id=1, path="/a.mkv", size=100, media_type="video"),
                    DupFile(media_id=2, path="/b.mkv", size=100, media_type="video"),
                ]),
            ],
        )
        path = tmp_path / "report.json"
        save_report(report, path)
        loaded = load_report(path)
        assert loaded.total_duplicates == 2
        assert len(loaded.clusters) == 1
        assert loaded.clusters[0].files[0].path == "/a.mkv"


class TestApplyDedup:
    def test_moves_duplicates(self, tmp_path: Path, tmp_db_path: Path) -> None:
        f1 = tmp_path / "keep.mkv"
        f2 = tmp_path / "dup.mkv"
        f1.write_bytes(b"content")
        f2.write_bytes(b"content")
        holding = tmp_path / "holding"

        report = DedupReport(clusters=[
            DupCluster(id=1, strategy="exact_checksum", recommended_keep=1, files=[
                DupFile(media_id=1, path=str(f1), size=100, media_type="video"),
                DupFile(media_id=2, path=str(f2), size=100, media_type="video"),
            ]),
        ])

        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=100)
            db.insert_media(path=str(f2), media_type="video", size=100)
            result = apply_dedup(db, report, holding)

        assert result["moved"] == 1
        assert f1.exists()  # kept
        assert not f2.exists()  # moved
        assert (holding / "dup.mkv").exists()

    def test_apply_specific_cluster(self, tmp_path: Path, tmp_db_path: Path) -> None:
        f1 = tmp_path / "a.mkv"
        f2 = tmp_path / "b.mkv"
        f3 = tmp_path / "c.mkv"
        f4 = tmp_path / "d.mkv"
        f1.write_bytes(b"x")
        f2.write_bytes(b"x")
        f3.write_bytes(b"y")
        f4.write_bytes(b"y")
        holding = tmp_path / "holding"

        report = DedupReport(clusters=[
            DupCluster(id=1, strategy="exact_checksum", recommended_keep=1, files=[
                DupFile(media_id=1, path=str(f1), size=1, media_type="video"),
                DupFile(media_id=2, path=str(f2), size=1, media_type="video"),
            ]),
            DupCluster(id=2, strategy="exact_checksum", recommended_keep=3, files=[
                DupFile(media_id=3, path=str(f3), size=1, media_type="video"),
                DupFile(media_id=4, path=str(f4), size=1, media_type="video"),
            ]),
        ])

        with QuorumDB(tmp_db_path) as db:
            for i, f in enumerate([f1, f2, f3, f4], 1):
                db.insert_media(path=str(f), media_type="video", size=1)
            result = apply_dedup(db, report, holding, cluster_id=2)

        assert result["moved"] == 1
        assert f2.exists()  # cluster 1 untouched
        assert f3.exists()  # kept in cluster 2
        assert not f4.exists()  # moved from cluster 2

    def test_logs_actions(self, tmp_path: Path, tmp_db_path: Path) -> None:
        f1 = tmp_path / "keep.mkv"
        f2 = tmp_path / "dup.mkv"
        f1.write_bytes(b"c")
        f2.write_bytes(b"c")
        holding = tmp_path / "holding"

        report = DedupReport(clusters=[
            DupCluster(id=1, strategy="exact_checksum", recommended_keep=1, files=[
                DupFile(media_id=1, path=str(f1), size=1, media_type="video"),
                DupFile(media_id=2, path=str(f2), size=1, media_type="video"),
            ]),
        ])

        with QuorumDB(tmp_db_path) as db:
            db.insert_media(path=str(f1), media_type="video", size=1)
            db.insert_media(path=str(f2), media_type="video", size=1)
            apply_dedup(db, report, holding)
            actions = db.list_actions()
        assert len(actions) == 1
        assert actions[0]["operation"] == "dedup_move"
