from __future__ import annotations

from pathlib import Path

from quorum.plugins.projects import ProjectPlugin, detect_project_clusters


class TestDetectClusters:
    def test_shared_stems(self, tmp_path: Path) -> None:
        (tmp_path / "report.docx").write_bytes(b"\x00")
        (tmp_path / "report.pdf").write_bytes(b"\x00")
        files = list(tmp_path.iterdir())
        clusters = detect_project_clusters(files)
        assert any(len(v) >= 2 for v in clusters.values())

    def test_version_variants(self, tmp_path: Path) -> None:
        (tmp_path / "design.psd").write_bytes(b"\x00")
        (tmp_path / "design.png").write_bytes(b"\x00")
        files = list(tmp_path.iterdir())
        clusters = detect_project_clusters(files)
        assert len(clusters) >= 1

    def test_project_markers(self, tmp_path: Path) -> None:
        proj = tmp_path / "myproject"
        proj.mkdir()
        (proj / "package.json").write_text("{}")
        (proj / "index.js").write_text("//")
        files = list(proj.iterdir())
        clusters = detect_project_clusters(files)
        assert "myproject" in clusters

    def test_no_clusters_for_unrelated(self, tmp_path: Path) -> None:
        (tmp_path / "photo.jpg").write_bytes(b"\x00")
        (tmp_path / "video.mkv").write_bytes(b"\x00")
        (tmp_path / "song.mp3").write_bytes(b"\x00")
        files = list(tmp_path.iterdir())
        clusters = detect_project_clusters(files)
        # No cluster should have 2+ files
        assert all(len(v) < 2 for v in clusters.values())

    def test_empty_input(self) -> None:
        clusters = detect_project_clusters([])
        assert clusters == {}


class TestProjectPlugin:
    def test_plugin_name(self) -> None:
        assert ProjectPlugin().name == "projects"

    def test_scan_finds_clusters(self, tmp_path: Path) -> None:
        (tmp_path / "report.docx").write_bytes(b"\x00")
        (tmp_path / "report.pdf").write_bytes(b"\x00")
        p = ProjectPlugin()
        p.on_register({})
        proposals = p.on_scan(list(tmp_path.iterdir()))
        assert len(proposals) >= 2
        assert all("Projects" in prop.dest_path for prop in proposals)

    def test_apply_only_suggests(self, tmp_path: Path) -> None:
        p = ProjectPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(media_id=0, source_path="/a.psd", dest_path="/out/a.psd", confidence=0.6)]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "suggested"

    def test_scan_empty(self) -> None:
        p = ProjectPlugin()
        p.on_register({})
        assert p.on_scan([]) == []
