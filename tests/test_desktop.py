from __future__ import annotations

import os
import time
from pathlib import Path

from quorum.plugins.desktop import DesktopPlugin, desktop_stats


class TestDesktopPlugin:
    def test_plugin_name(self) -> None:
        assert DesktopPlugin().name == "desktop"

    def test_scan_skips_recent(self, tmp_path: Path) -> None:
        f = tmp_path / "recent.txt"
        f.write_text("new file")
        p = DesktopPlugin()
        p.on_register({"archive_after_days": 30})
        proposals = p.on_scan([f])
        assert len(proposals) == 0  # too recent

    def test_scan_finds_old(self, tmp_path: Path) -> None:
        f = tmp_path / "old.txt"
        f.write_text("old file")
        # Set mtime to 60 days ago
        old_time = time.time() - (60 * 86400)
        os.utime(str(f), (old_time, old_time))
        p = DesktopPlugin()
        p.on_register({"archive_after_days": 30})
        proposals = p.on_scan([f])
        assert len(proposals) == 1
        assert proposals[0].metadata["age_days"] >= 59

    def test_scan_skips_hidden(self, tmp_path: Path) -> None:
        f = tmp_path / ".hidden"
        f.write_text("hidden")
        old_time = time.time() - (60 * 86400)
        os.utime(str(f), (old_time, old_time))
        p = DesktopPlugin()
        p.on_register({"archive_after_days": 30})
        proposals = p.on_scan([f])
        assert len(proposals) == 0

    def test_apply_archives(self, tmp_path: Path) -> None:
        src = tmp_path / "old.txt"
        src.write_text("archive me")
        dest_dir = tmp_path / "archive"

        p = DesktopPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path=str(src),
            dest_path=str(dest_dir / "2024" / "06" / "old.txt"),
            confidence=0.7,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "archived"
        assert not src.exists()

    def test_scan_empty(self) -> None:
        p = DesktopPlugin()
        p.on_register({})
        assert p.on_scan([]) == []


class TestDesktopStats:
    def test_stats_empty_dir(self, tmp_path: Path) -> None:
        stats = desktop_stats(tmp_path)
        assert stats["file_count"] == 0

    def test_stats_with_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("content")
        stats = desktop_stats(tmp_path)
        assert stats["file_count"] == 1
        assert stats["buckets"]["<7 days"] == 1

    def test_stats_missing_dir(self, tmp_path: Path) -> None:
        stats = desktop_stats(tmp_path / "nonexistent")
        assert "error" in stats
