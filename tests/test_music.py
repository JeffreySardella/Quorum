from __future__ import annotations

from pathlib import Path

from quorum.plugins.music import MusicPlugin, extract_tags, _extract_from_filename


class TestExtractFromFilename:
    def test_artist_dash_title(self) -> None:
        info = _extract_from_filename(Path("Pink Floyd - Comfortably Numb.mp3"))
        assert info["artist"] == "Pink Floyd"
        assert info["title"] == "Comfortably Numb"
        assert info["confidence"] == 0.5

    def test_track_number_dash_title(self) -> None:
        info = _extract_from_filename(Path("01 - Intro.mp3"))
        assert info["track"] == 1
        assert info["title"] == "Intro"

    def test_track_number_dot_title(self) -> None:
        info = _extract_from_filename(Path("03. Outro.flac"))
        assert info["track"] == 3
        assert info["title"] == "Outro"

    def test_plain_title(self) -> None:
        info = _extract_from_filename(Path("mysong.mp3"))
        assert info["title"] == "mysong"


class TestMusicPlugin:
    def test_plugin_name(self) -> None:
        p = MusicPlugin()
        assert p.name == "music"

    def test_file_types(self) -> None:
        p = MusicPlugin()
        assert ".mp3" in p.file_types
        assert ".flac" in p.file_types
        assert ".m4a" in p.file_types

    def test_scan_with_filename_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "Pink Floyd - Comfortably Numb.mp3"
        f.write_bytes(b"\x00" * 100)  # not a real MP3
        p = MusicPlugin()
        p.on_register({})
        proposals = p.on_scan([f])
        assert len(proposals) == 1
        assert "Pink Floyd" in proposals[0].dest_path
        assert "Comfortably Numb" in proposals[0].dest_path

    def test_scan_generates_plex_structure(self, tmp_path: Path) -> None:
        f = tmp_path / "Artist - Song.mp3"
        f.write_bytes(b"\x00" * 100)
        p = MusicPlugin()
        p.on_register({})
        proposals = p.on_scan([f])
        dest = proposals[0].dest_path
        # Should be Music/Artist/Unknown Album/Song.mp3
        assert dest.startswith("Music")
        assert "Artist" in dest

    def test_scan_empty_list(self) -> None:
        p = MusicPlugin()
        p.on_register({})
        assert p.on_scan([]) == []

    def test_apply_moves_file(self, tmp_path: Path) -> None:
        src = tmp_path / "song.mp3"
        src.write_bytes(b"\x00" * 100)
        dest_root = tmp_path / "library"

        p = MusicPlugin()
        p.on_register({"dest_root": dest_root})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0,
            source_path=str(src),
            dest_path="Music/Artist/Album/song.mp3",
            confidence=0.9,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "moved"
        assert not src.exists()
        assert (dest_root / "Music" / "Artist" / "Album" / "song.mp3").exists()

    def test_apply_missing_file(self, tmp_path: Path) -> None:
        p = MusicPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/nonexistent.mp3",
            dest_path="Music/out.mp3", confidence=0.9,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "skipped"

    def test_on_register(self) -> None:
        p = MusicPlugin()
        p.on_register({"dest_root": Path("/out")})
        assert p._dest_root == Path("/out")


class TestExtractTags:
    def test_non_audio_file(self, tmp_path: Path) -> None:
        f = tmp_path / "not_audio.mp3"
        f.write_bytes(b"\x00" * 100)
        info = extract_tags(f)
        assert info is not None
        assert "title" in info  # falls back to filename

    def test_real_mp3_if_available(self, tmp_path: Path) -> None:
        # This tests graceful handling — mutagen returns None for non-audio
        f = tmp_path / "Artist - Title.mp3"
        f.write_bytes(b"\x00" * 50)
        info = extract_tags(f)
        assert info is not None
