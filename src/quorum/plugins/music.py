from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_MUSIC_EXTS = [".mp3", ".flac", ".ogg", ".m4a", ".wav", ".aac", ".wma", ".opus"]

_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")


class MusicPlugin:
    """Organizes music files into Plex-friendly Artist/Album/Track structure."""

    name = "music"
    file_types = _MUSIC_EXTS

    def __init__(self) -> None:
        self._dest_root: Path | None = None
        self._db = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")
        self._db = context.get("db")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            info = extract_tags(f)
            if not info:
                continue

            artist = _safe(info.get("artist", "Unknown Artist"))
            album = _safe(info.get("album", "Unknown Album"))
            title = _safe(info.get("title", f.stem))
            track = info.get("track", 0)
            ext = f.suffix.lower()

            if track:
                filename = f"{track:02d} - {title}{ext}"
            else:
                filename = f"{title}{ext}"

            dest = Path("Music") / artist / album / filename

            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(dest),
                confidence=info.get("confidence", 0.5),
                metadata=info,
            ))
        return proposals

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        results: list[dict] = []
        for p in proposals:
            src = Path(p.source_path)
            if self._dest_root:
                dst = self._dest_root / p.dest_path
            else:
                dst = Path(p.dest_path)

            if not src.exists():
                results.append({"source": str(src), "status": "skipped", "reason": "not found"})
                continue

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                results.append({"source": str(src), "dest": str(dst), "status": "moved"})
            except OSError as e:
                results.append({"source": str(src), "status": "failed", "error": str(e)})

        return results


def extract_tags(path: Path) -> dict | None:
    """Extract music metadata using mutagen."""
    try:
        import mutagen
        from mutagen.easyid3 import EasyID3  # noqa: F401
        from mutagen.flac import FLAC  # noqa: F401
        from mutagen.mp4 import MP4  # noqa: F401
        from mutagen.oggvorbis import OggVorbis  # noqa: F401
    except ImportError:
        return _extract_from_filename(path)

    info: dict[str, Any] = {"confidence": 0.3}  # default low confidence

    try:
        audio = mutagen.File(str(path), easy=True)
        if audio is None:
            return _extract_from_filename(path)

        info["artist"] = _first(audio.get("artist"))
        info["album"] = _first(audio.get("album"))
        info["title"] = _first(audio.get("title"))
        info["genre"] = _first(audio.get("genre"))
        info["date"] = _first(audio.get("date"))

        track_raw = _first(audio.get("tracknumber"))
        if track_raw:
            try:
                info["track"] = int(track_raw.split("/")[0])
            except (ValueError, IndexError):
                info["track"] = 0

        if audio.info:
            info["duration"] = getattr(audio.info, "length", None)
            info["bitrate"] = getattr(audio.info, "bitrate", None)

        # Confidence based on tag completeness
        filled = sum(1 for k in ["artist", "album", "title"] if info.get(k))
        info["confidence"] = [0.3, 0.5, 0.7, 0.9][filled]

    except Exception:
        return _extract_from_filename(path)

    return info


def _first(val: Any) -> str | None:
    if isinstance(val, list) and val:
        return str(val[0])
    if isinstance(val, str):
        return val
    return None


def _extract_from_filename(path: Path) -> dict:
    """Fallback: parse artist/title from filename patterns like 'Artist - Title.mp3'."""
    stem = path.stem
    info: dict[str, Any] = {"confidence": 0.3}

    # Try "01 - Title" or "01. Title" pattern first (track number prefix)
    m = re.match(r"^(\d{1,3})[\s._-]+(.+)$", stem)
    if m:
        info["track"] = int(m.group(1))
        info["title"] = m.group(2).strip()
    else:
        # Try "Artist - Title" pattern
        m = re.match(r"^(.+?)\s*-\s*(.+)$", stem)
        if m:
            info["artist"] = m.group(1).strip()
            info["title"] = m.group(2).strip()
            info["confidence"] = 0.5
        else:
            info["title"] = stem

    return info
