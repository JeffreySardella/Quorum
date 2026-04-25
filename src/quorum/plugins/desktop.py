from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


class DesktopPlugin:
    """Organizes cluttered desktop by age and file type."""

    name = "desktop"
    file_types = [".*"]

    def __init__(self) -> None:
        self._archive_after_days: int = 30
        self._archive_to: str = "Archive/{year}/{month}"

    def on_register(self, context: dict[str, Any]) -> None:
        self._archive_after_days = context.get("archive_after_days", 30)
        self._archive_to = context.get("archive_to", "Archive/{year}/{month}")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        threshold = datetime.now() - timedelta(days=self._archive_after_days)

        for f in files:
            if not f.exists() or not f.is_file():
                continue
            # Skip hidden files and system files
            if f.name.startswith(".") or f.name.startswith("~"):
                continue

            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue

            if mtime >= threshold:
                continue  # Too recent to archive

            age_days = (datetime.now() - mtime).days
            year = mtime.strftime("%Y")
            month = mtime.strftime("%m")
            dest_template = self._archive_to.replace("{year}", year).replace("{month}", month)
            dest = Path(dest_template) / f.name

            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(dest),
                confidence=0.7,
                metadata={
                    "age_days": age_days,
                    "modified": mtime.isoformat(timespec="seconds"),
                    "action": "archive",
                },
            ))
        return proposals

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        results: list[dict] = []
        for p in proposals:
            src = Path(p.source_path)
            dst = Path(p.dest_path)
            if not src.exists():
                results.append({"source": str(src), "status": "skipped"})
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                results.append({"source": str(src), "dest": str(dst), "status": "archived"})
            except OSError as e:
                results.append({"source": str(src), "status": "failed", "error": str(e)})
        return results


def desktop_stats(path: Path) -> dict[str, Any]:
    """Get age distribution of files on the Desktop."""
    if not path.exists():
        return {"error": "Path not found"}

    now = datetime.now()
    buckets = {"<7 days": 0, "7-30 days": 0, "30-90 days": 0, "90-365 days": 0, ">1 year": 0}
    total_size = 0
    file_count = 0

    for f in path.iterdir():
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            stat = f.stat()
            age = (now - datetime.fromtimestamp(stat.st_mtime)).days
            total_size += stat.st_size
            file_count += 1

            if age < 7:
                buckets["<7 days"] += 1
            elif age < 30:
                buckets["7-30 days"] += 1
            elif age < 90:
                buckets["30-90 days"] += 1
            elif age < 365:
                buckets["90-365 days"] += 1
            else:
                buckets[">1 year"] += 1
        except OSError:
            continue

    return {"buckets": buckets, "total_size": total_size, "file_count": file_count}
