from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_AUDIO_EXTS = [".m4a", ".wav", ".ogg", ".mp3"]

_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")


class AudioMemoPlugin:
    """Organizes audio memos/voice recordings by date and topic."""

    name = "audio_memo"
    file_types = _AUDIO_EXTS

    def __init__(self) -> None:
        self._dest_root: Path | None = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            if not f.exists():
                continue
            # Skip files that look like music (check for music tags)
            if _is_likely_music(f):
                continue

            info = _extract_memo_info(f)
            date_str = info.get("date", datetime.now().strftime("%Y-%m-%d"))
            year = date_str[:4]
            topic = _safe(info.get("topic", f.stem))
            ext = f.suffix.lower()

            dest = Path("Audio Memos") / year / f"{date_str} — {topic}{ext}"

            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(dest),
                confidence=info.get("confidence", 0.4),
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


def _is_likely_music(path: Path) -> bool:
    """Check if a file is likely music rather than a voice memo."""
    try:
        import mutagen
        audio = mutagen.File(str(path), easy=True)
        if audio is None:
            return False
        # Has artist + album tags → probably music
        has_artist = bool(audio.get("artist"))
        has_album = bool(audio.get("album"))
        if has_artist and has_album:
            return True
        # Long duration (>10 min) without tags is ambiguous, keep as memo
        if audio.info and hasattr(audio.info, "length"):
            if audio.info.length > 600 and has_artist:
                return True
    except Exception:
        pass
    return False


def _extract_memo_info(path: Path) -> dict[str, Any]:
    """Extract date and topic from an audio memo file."""
    info: dict[str, Any] = {"confidence": 0.4}
    stem = path.stem

    # Try to extract date from filename
    date_match = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", stem)
    if date_match:
        info["date"] = date_match.group(1).replace("_", "-")
        info["confidence"] = 0.6
    else:
        # Use file modification time
        try:
            mtime = path.stat().st_mtime
            info["date"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except OSError:
            info["date"] = datetime.now().strftime("%Y-%m-%d")

    # Topic from filename (strip date and common prefixes)
    topic = stem
    topic = re.sub(r"\d{4}[-_]\d{2}[-_]\d{2}[-_T]?\d{0,6}[-_]?", "", topic)
    topic = re.sub(r"^(voice[-_]?memo|recording|audio|rec)[-_]?", "", topic, flags=re.IGNORECASE)
    topic = topic.strip("-_ ")
    if topic:
        info["topic"] = topic
    else:
        info["topic"] = "Voice Memo"

    return info
