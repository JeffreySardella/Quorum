from __future__ import annotations

from pathlib import Path

from quorum.plugins.downloads import DownloadsPlugin, classify_file


class TestClassifyFile:
    def test_installer(self) -> None:
        assert classify_file(Path("setup.exe"))["category"] == "Apps"
        assert classify_file(Path("app.msi"))["category"] == "Apps"
        assert classify_file(Path("app.dmg"))["category"] == "Apps"

    def test_document(self) -> None:
        assert classify_file(Path("report.pdf"))["category"] == "Documents"
        assert classify_file(Path("notes.txt"))["category"] == "Documents"

    def test_image(self) -> None:
        assert classify_file(Path("photo.jpg"))["category"] == "Images"
        assert classify_file(Path("icon.png"))["category"] == "Images"

    def test_video(self) -> None:
        assert classify_file(Path("movie.mkv"))["category"] == "Videos"

    def test_audio(self) -> None:
        assert classify_file(Path("song.mp3"))["category"] == "Audio"

    def test_code(self) -> None:
        assert classify_file(Path("main.py"))["category"] == "Code"

    def test_archive(self) -> None:
        assert classify_file(Path("backup.zip"))["category"] == "Archives"

    def test_unknown(self) -> None:
        assert classify_file(Path("weird.xyz"))["category"] == "Unsorted"


class TestDownloadsPlugin:
    def test_plugin_name(self) -> None:
        assert DownloadsPlugin().name == "downloads"

    def test_scan(self, tmp_path: Path) -> None:
        (tmp_path / "setup.exe").write_bytes(b"\x00")
        (tmp_path / "report.pdf").write_bytes(b"\x00")
        (tmp_path / "photo.jpg").write_bytes(b"\x00")

        p = DownloadsPlugin()
        p.on_register({})
        files = [f for f in tmp_path.iterdir() if f.is_file()]
        proposals = p.on_scan(files)
        assert len(proposals) == 3

        categories = {prop.metadata["category"] for prop in proposals}
        assert "Apps" in categories
        assert "Documents" in categories
        assert "Images" in categories

    def test_apply_moves(self, tmp_path: Path) -> None:
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"\x00")
        dest_root = tmp_path / "organized"

        p = DownloadsPlugin()
        p.on_register({"dest_root": dest_root})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path=str(src),
            dest_path="Documents/doc.pdf", confidence=0.8,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "moved"
        assert (dest_root / "Documents" / "doc.pdf").exists()

    def test_scan_empty(self) -> None:
        p = DownloadsPlugin()
        p.on_register({})
        assert p.on_scan([]) == []
