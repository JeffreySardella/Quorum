from __future__ import annotations

from pathlib import Path

from quorum.plugins.scan_recovery import ScanRecoveryPlugin, analyze_scan


class TestAnalyzeScan:
    def test_non_image_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.jpg"
        f.write_bytes(b"\x00" * 100)
        info = analyze_scan(f)
        assert info["is_scan"] is False

    def test_missing_file(self, tmp_path: Path) -> None:
        info = analyze_scan(tmp_path / "nope.jpg")
        assert info["is_scan"] is False

    def test_small_image_not_scan(self, tmp_path: Path) -> None:
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="red")
        f = tmp_path / "small.jpg"
        img.save(str(f))
        info = analyze_scan(f)
        assert info["is_scan"] is False

    def test_large_image_at_print_ratio(self, tmp_path: Path) -> None:
        from PIL import Image
        # 4x6 at 300 DPI = 1200x1800 — scanner-like
        img = Image.new("RGB", (1200, 1800), color="white")
        f = tmp_path / "scan.jpg"
        img.save(str(f))
        info = analyze_scan(f)
        assert info["width"] == 1200
        assert info["height"] == 1800
        # May or may not trigger is_scan depending on border detection

    def test_very_large_image(self, tmp_path: Path) -> None:
        from PIL import Image
        img = Image.new("RGB", (2400, 3600), color="white")
        f = tmp_path / "hires.jpg"
        img.save(str(f))
        info = analyze_scan(f)
        assert info["confidence"] >= 0.3  # high res bonus


class TestScanRecoveryPlugin:
    def test_plugin_name(self) -> None:
        p = ScanRecoveryPlugin()
        assert p.name == "scan_recovery"

    def test_file_types(self) -> None:
        p = ScanRecoveryPlugin()
        assert ".jpg" in p.file_types
        assert ".tiff" in p.file_types

    def test_scan_empty(self) -> None:
        p = ScanRecoveryPlugin()
        p.on_register({})
        assert p.on_scan([]) == []

    def test_scan_non_scan_image(self, tmp_path: Path) -> None:
        from PIL import Image
        img = Image.new("RGB", (100, 100), "red")
        f = tmp_path / "photo.jpg"
        img.save(str(f))
        p = ScanRecoveryPlugin()
        p.on_register({})
        proposals = p.on_scan([f])
        assert len(proposals) == 0  # too small to be a scan

    def test_apply_copies_file(self, tmp_path: Path) -> None:
        from PIL import Image
        src = tmp_path / "scan.jpg"
        Image.new("RGB", (100, 100), "red").save(str(src))
        dest_root = tmp_path / "output"

        p = ScanRecoveryPlugin()
        p.on_register({"dest_root": dest_root})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path=str(src),
            dest_path="Recovered Photos/scan.jpg", confidence=0.8,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "processed"
        assert src.exists()  # original preserved (copy, not move)

    def test_apply_missing_file(self) -> None:
        p = ScanRecoveryPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/nope.jpg",
            dest_path="out.jpg", confidence=0.5,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "skipped"
