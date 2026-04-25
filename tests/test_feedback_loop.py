from __future__ import annotations

from pathlib import Path

import pytest

from quorum.db import QuorumDB
from quorum.feedback_loop import compute_signal_weights, retune_signals, _normalize


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestNormalize:
    def test_basic(self) -> None:
        assert _normalize("The Matrix") == "the matrix"

    def test_none(self) -> None:
        assert _normalize(None) == ""

    def test_strips_punctuation(self) -> None:
        assert _normalize("Hello, World.") == "hello world"


class TestComputeSignalWeights:
    def test_no_feedback(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            weights = compute_signal_weights(db)
        assert weights == {}

    def test_all_correct_signals(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.9, "", "2024-01-01T00:00:00")
            db.insert_feedback(mid, "approve", "The Matrix", created_at="2024-01-01T00:00:00")
            weights = compute_signal_weights(db)
        assert "filename" in weights
        assert weights["filename"] >= 1.0  # should be boosted

    def test_all_wrong_signals(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "vision", "Wrong Movie", 0.7, "", "2024-01-01T00:00:00")
            db.insert_feedback(mid, "correct", "Wrong Movie", correction="Right Movie", created_at="2024-01-01T00:00:00")
            weights = compute_signal_weights(db)
        assert "vision" in weights
        assert weights["vision"] < 1.0  # should be dampened

    def test_weight_clamped(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            for i in range(10):
                mid = db.insert_media(path=f"/{i}.mkv", media_type="video", size=100)
                db.insert_signal(mid, "fingerprint", "Correct", 0.95, "", "2024-01-01T00:00:00")
                db.insert_feedback(mid, "approve", "Correct", created_at="2024-01-01T00:00:00")
            weights = compute_signal_weights(db)
        assert weights["fingerprint"] <= 3.0
        assert weights["fingerprint"] >= 0.1

    def test_multiple_signals(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "The Matrix", 0.9, "", "2024-01-01T00:00:00")
            db.insert_signal(mid, "vision", "Wrong Title", 0.5, "", "2024-01-01T00:00:00")
            db.insert_feedback(mid, "approve", "The Matrix", created_at="2024-01-01T00:00:00")
            weights = compute_signal_weights(db)
        assert weights["filename"] > weights["vision"]


class TestRetune:
    def test_retune_dry_run(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
            db.insert_signal(mid, "filename", "Test", 0.9, "", "2024-01-01T00:00:00")
            db.insert_feedback(mid, "approve", "Test", created_at="2024-01-01T00:00:00")
            changes = retune_signals(db, dry_run=True)
        assert "filename" in changes
        assert "old" in changes["filename"]
        assert "new" in changes["filename"]

    def test_retune_writes_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "quorum.db"
        import os
        old_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with QuorumDB(db_path) as db:
                mid = db.insert_media(path="/a.mkv", media_type="video", size=100)
                db.insert_signal(mid, "filename", "Test", 0.9, "", "2024-01-01T00:00:00")
                db.insert_feedback(mid, "approve", "Test", created_at="2024-01-01T00:00:00")
                retune_signals(db, dry_run=False)
            assert (tmp_path / "signal_weights.json").exists()
        finally:
            os.chdir(old_cwd)

    def test_retune_no_feedback(self, tmp_db_path: Path) -> None:
        with QuorumDB(tmp_db_path) as db:
            changes = retune_signals(db)
        assert changes == {}
