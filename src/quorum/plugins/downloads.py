from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_INSTALLER_EXTS = {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage"}
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".tar.gz", ".tgz"}
_DOC_EXTS = {".pdf", ".docx", ".doc", ".txt", ".rtf", ".odt", ".xlsx", ".csv", ".pptx"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic"}
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".webm", ".m4v"}
_AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".aac", ".opus"}
_CODE_EXTS = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".sh", ".bat"}

ALL_EXTS = list(
    _INSTALLER_EXTS | _ARCHIVE_EXTS | _DOC_EXTS | _IMAGE_EXTS |
    _VIDEO_EXTS | _AUDIO_EXTS | _CODE_EXTS | {".*"}
)

_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")


def classify_file(path: Path) -> dict[str, Any]:
    """Classify a file by extension and basic heuristics."""
    ext = path.suffix.lower()
    info: dict[str, Any] = {"extension": ext, "category": "Unknown"}

    if ext in _INSTALLER_EXTS:
        info["category"] = "Apps"
    elif ext in _ARCHIVE_EXTS:
        info["category"] = "Archives"
    elif ext in _DOC_EXTS:
        info["category"] = "Documents"
    elif ext in _IMAGE_EXTS:
        info["category"] = "Images"
    elif ext in _VIDEO_EXTS:
        info["category"] = "Videos"
    elif ext in _AUDIO_EXTS:
        info["category"] = "Audio"
    elif ext in _CODE_EXTS:
        info["category"] = "Code"
    else:
        info["category"] = "Unsorted"

    return info


class DownloadsPlugin:
    """Tames messy download folders by classifying and routing files."""

    name = "downloads"
    file_types = ALL_EXTS

    def __init__(self) -> None:
        self._dest_root: Path | None = None
        self._route_installers: str = "Apps"
        self._route_unknown: str = "Unsorted"

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")
        self._route_installers = context.get("route_installers", "Apps")
        self._route_unknown = context.get("route_unknown", "Unsorted")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            if not f.exists() or not f.is_file():
                continue
            info = classify_file(f)
            category = info["category"]
            dest = Path(category) / f.name

            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(dest),
                confidence=0.8 if category != "Unsorted" else 0.3,
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
                results.append({"source": str(src), "status": "skipped"})
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                results.append({"source": str(src), "dest": str(dst), "status": "moved"})
            except OSError as e:
                results.append({"source": str(src), "status": "failed", "error": str(e)})
        return results
