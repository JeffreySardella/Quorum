from __future__ import annotations

from pathlib import Path

from quorum.plugins.audio import AudioMemoPlugin, _extract_memo_info, _is_likely_music


class TestExtractMemoInfo:
    def test_date_in_filename(self) -> None:
        info = _extract_memo_info(Path("2024-06-15_meeting.m4a"))
        assert info["date"] == "2024-06-15"
        assert info["topic"] == "meeting"

    def test_date_with_underscores(self) -> None:
        info = _extract_memo_info(Path("2024_06_15_notes.wav"))
        assert info["date"] == "2024-06-15"

    def test_no_date_uses_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "memo.m4a"
        f.write_bytes(b"\x00")
        info = _extract_memo_info(f)
        assert "date" in info

    def test_voice_memo_prefix_stripped(self) -> None:
        info = _extract_memo_info(Path("voice_memo_2024-06-15_shopping.m4a"))
        assert info["topic"] == "shopping"

    def test_recording_prefix_stripped(self) -> None:
        info = _extract_memo_info(Path("Recording_2024-06-15.wav"))
        assert info["topic"] == "Voice Memo"  # nothing left after stripping

    def test_plain_filename(self) -> None:
        info = _extract_memo_info(Path("my_thoughts.m4a"))
        assert info["topic"] == "my_thoughts"


class TestIsLikelyMusic:
    def test_non_audio_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.m4a"
        f.write_bytes(b"\x00" * 100)
        assert _is_likely_music(f) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _is_likely_music(tmp_path / "nope.mp3") is False


class TestAudioMemoPlugin:
    def test_plugin_name(self) -> None:
        p = AudioMemoPlugin()
        assert p.name == "audio_memo"

    def test_file_types(self) -> None:
        p = AudioMemoPlugin()
        assert ".m4a" in p.file_types
        assert ".wav" in p.file_types

    def test_scan_generates_path(self, tmp_path: Path) -> None:
        f = tmp_path / "2024-06-15_meeting.m4a"
        f.write_bytes(b"\x00" * 100)
        p = AudioMemoPlugin()
        p.on_register({})
        proposals = p.on_scan([f])
        assert len(proposals) == 1
        assert "Audio Memos" in proposals[0].dest_path
        assert "2024" in proposals[0].dest_path
        assert "meeting" in proposals[0].dest_path

    def test_scan_empty(self) -> None:
        p = AudioMemoPlugin()
        p.on_register({})
        assert p.on_scan([]) == []

    def test_apply_moves_file(self, tmp_path: Path) -> None:
        src = tmp_path / "memo.m4a"
        src.write_bytes(b"\x00" * 100)
        dest_root = tmp_path / "library"

        p = AudioMemoPlugin()
        p.on_register({"dest_root": dest_root})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path=str(src),
            dest_path="Audio Memos/2024/2024-06-15 — meeting.m4a",
            confidence=0.5,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "moved"
        assert not src.exists()

    def test_apply_missing_file(self) -> None:
        p = AudioMemoPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/nonexistent.m4a",
            dest_path="out.m4a", confidence=0.5,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "skipped"
