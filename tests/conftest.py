from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


@pytest.fixture
def sample_video(tmp_path: Path) -> Path:
    p = tmp_path / "sample.mkv"
    p.write_bytes(b"\x1a\x45\xdf\xa3")
    return p


@pytest.fixture
def sample_photo(tmp_path: Path) -> Path:
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    return p
