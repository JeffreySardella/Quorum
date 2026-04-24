"""Folder-rename pass — renames fully-enriched home-video event folders
using LLM-generated names derived from .nfo sidecar metadata.

Walks Home Videos/YYYY/ and for each event folder where every video has
a .nfo sidecar, reads the titles/descriptions and asks the text LLM to
propose a clean, descriptive folder name.

Safety:
  - Writes a JSONL log compatible with ``quorum undo``
  - Dry-run mode shows proposals without renaming
  - Skips if proposed name collides with an existing folder
  - Skips folders that are not fully enriched (some videos lack .nfo)

Can run standalone (``quorum rename-folders <root>``) or be auto-triggered
at the end of ``quorum enrich``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .ollama_client import OllamaClient
from .pipeline import VIDEO_EXTS

console = Console()

_INVALID = set('<>:"/\\|?*')

RENAME_PROMPT = """You are renaming a home-video event folder for a Plex library.

The folder currently contains {count} videos. It is currently named:
"{current_name}"

Here are the titles and descriptions from the .nfo metadata of each video:
{nfo_summary}

Propose a single clean, descriptive folder name that captures the event.
Rules:
- Under 80 characters
- Include the year if evident from content
- Concrete and specific (e.g. "Sophia's 4th Birthday 2006" not "Birthday Party")
- Do not include quotes, colons, or special characters
- If the current name already describes the content well, return it unchanged

Return ONLY the proposed folder name. Nothing else — no quotes, no explanation."""


@dataclass
class RenameSummary:
    total_folders: int = 0
    renamed: int = 0
    skipped_not_enriched: int = 0
    skipped_same_name: int = 0
    skipped_collision: int = 0
    failed: int = 0


def _safe(s: str) -> str:
    """Sanitize a string for use as a folder name."""
    s = "".join(ch if ch not in _INVALID else " " for ch in s)
    return " ".join(s.split()).strip().strip(".")[:80]


def _read_nfo(nfo_path: Path) -> tuple[str, str]:
    """Return (title, plot) from an .nfo file."""
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        title = root.findtext("title", default="")
        plot = root.findtext("plot", default="")
        return title, plot
    except Exception:
        return "", ""


def _find_event_folders(root: Path) -> list[Path]:
    """Find event folders under Home Videos/YYYY/."""
    hv = root / "Home Videos"
    if not hv.exists():
        return []
    folders: list[Path] = []
    for year_dir in sorted(hv.iterdir()):
        if not year_dir.is_dir():
            continue
        for event_dir in sorted(year_dir.iterdir()):
            if not event_dir.is_dir():
                continue
            # Check if it contains any video files
            videos = [f for f in event_dir.iterdir()
                      if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
            if videos:
                folders.append(event_dir)
    return folders


def _is_fully_enriched(folder: Path) -> bool:
    """True if every video in the folder has a .nfo sidecar."""
    videos = [f for f in folder.iterdir()
              if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
    if not videos:
        return False
    return all((v.with_suffix(".nfo")).exists() for v in videos)


def run_rename_folders(
    settings: Settings,
    root: Path,
    dry_run: bool = False,
    log_file=None,
) -> tuple[RenameSummary, Path | None]:
    """Rename event folders using LLM-proposed names.

    If *log_file* is provided (an open file handle), append entries there
    (used by the enrich integration so a single ``quorum undo`` reverses
    both enrichment and renames).  Otherwise create a standalone log file.
    """
    folders = _find_event_folders(root)
    summary = RenameSummary(total_folders=len(folders))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path: Path | None = None
    own_log = log_file is None

    if own_log:
        log_path = root / f"rename-folders-{stamp}.log"

    if not folders:
        console.print(f"[yellow]No event folders found under {root}/Home Videos[/]")
        if log_path:
            log_path.touch()
        return summary, log_path

    ollama = OllamaClient(settings.ollama_url)

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
    ]

    log_f = None
    try:
        if own_log and log_path:
            log_f = log_path.open("w", encoding="utf-8")
        else:
            log_f = log_file

        with Progress(*columns, console=console) as progress:
            task = progress.add_task("rename-folders", total=len(folders))
            for folder in folders:
                progress.update(task, description=folder.name[:60])

                if not _is_fully_enriched(folder):
                    summary.skipped_not_enriched += 1
                    if log_f:
                        log_f.write(json.dumps({
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "action": "skip_not_enriched",
                            "folder": str(folder),
                        }, ensure_ascii=False) + "\n")
                        log_f.flush()
                    progress.advance(task)
                    continue

                # Read all NFOs
                videos = [f for f in folder.iterdir()
                          if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
                nfo_entries = []
                for v in videos:
                    title, plot = _read_nfo(v.with_suffix(".nfo"))
                    if title or plot:
                        nfo_entries.append(f"- {title}: {plot}")

                nfo_summary = "\n".join(nfo_entries) if nfo_entries else "(no metadata available)"

                prompt = RENAME_PROMPT.format(
                    count=len(videos),
                    current_name=folder.name,
                    nfo_summary=nfo_summary,
                )

                try:
                    raw = ollama.generate(settings.models.text, prompt)
                    proposed = _safe(raw.strip().strip('"').strip("'"))
                except Exception as e:
                    summary.failed += 1
                    if log_f:
                        log_f.write(json.dumps({
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "action": "fail",
                            "folder": str(folder),
                            "error": str(e)[:500],
                        }, ensure_ascii=False) + "\n")
                        log_f.flush()
                    progress.advance(task)
                    continue

                if not proposed or proposed.lower() == folder.name.lower():
                    summary.skipped_same_name += 1
                    progress.advance(task)
                    continue

                new_path = folder.parent / proposed
                if new_path.exists():
                    summary.skipped_collision += 1
                    if log_f:
                        log_f.write(json.dumps({
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "action": "skip_collision",
                            "src": str(folder),
                            "proposed": proposed,
                        }, ensure_ascii=False) + "\n")
                        log_f.flush()
                    progress.advance(task)
                    continue

                if not dry_run:
                    try:
                        folder.rename(new_path)
                    except OSError as e:
                        summary.failed += 1
                        if log_f:
                            log_f.write(json.dumps({
                                "ts": datetime.now().isoformat(timespec="seconds"),
                                "action": "fail",
                                "src": str(folder),
                                "dst": str(new_path),
                                "error": str(e)[:500],
                            }, ensure_ascii=False) + "\n")
                            log_f.flush()
                        progress.advance(task)
                        continue

                summary.renamed += 1
                if log_f:
                    log_f.write(json.dumps({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "action": "move",
                        "src": str(folder),
                        "dst": str(new_path),
                    }, ensure_ascii=False) + "\n")
                    log_f.flush()

                label = "[cyan]DRY[/]" if dry_run else "[green]OK[/]"
                console.print(f"  {label} {folder.name} -> {proposed}")

                progress.advance(task)
    finally:
        ollama.close()
        if own_log and log_f:
            log_f.close()

    return summary, log_path


def print_summary(summary: RenameSummary, log_path: Path | None, dry_run: bool = False) -> None:
    t = Table(title="Quorum rename-folders" + (" (dry run)" if dry_run else ""))
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("event folders seen", str(summary.total_folders))
    t.add_row("[green]renamed[/]", str(summary.renamed))
    t.add_row("[dim]skipped (not fully enriched)[/]", str(summary.skipped_not_enriched))
    t.add_row("[dim]skipped (name unchanged)[/]", str(summary.skipped_same_name))
    t.add_row("[yellow]skipped (name collision)[/]", str(summary.skipped_collision))
    t.add_row("[red]failed[/]", str(summary.failed))
    console.print(t)
    if log_path:
        console.print(f"Log: [bold]{log_path}[/]")
