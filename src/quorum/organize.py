"""Autonomous organization mode — scan a source dir, move identified files into a
Plex-compliant structure at a destination dir, and log every action so it can be
reversed.

Run `quorum auto <src> <dest>` overnight. In the morning check the log file in
the destination, and `quorum undo <log>` if something looks wrong.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .pipeline import Pipeline, Proposal, iter_videos


console = Console()


# ── destination path ──────────────────────────────────────────────────────
_BAD = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD).strip().strip(".")


def plex_path(dest_root: Path, proposal: Proposal) -> Path | None:
    """Compute a Plex-compliant destination for a high-confidence proposal.

    Returns None when the proposal lacks the fields Plex needs (year for movies,
    season + episode for TV). Those files get quarantined instead.
    """
    picked = proposal.picked
    if not picked:
        return None
    title = _safe(str(picked.get("title") or ""))
    if not title:
        return None
    ext = Path(proposal.path).suffix.lower()

    if proposal.kind == "tv":
        season = picked.get("season")
        episode = picked.get("episode")
        if not isinstance(season, int) or not isinstance(episode, int):
            return None
        return (
            dest_root / "TV Shows" / title / f"Season {season:02d}"
            / f"{title} - s{season:02d}e{episode:02d}{ext}"
        )

    year = picked.get("year")
    if not isinstance(year, int):
        return None
    stem = f"{title} ({year})"
    return dest_root / "Movies" / stem / f"{stem}{ext}"


# ── run summary ───────────────────────────────────────────────────────────
@dataclass
class AutoSummary:
    total: int = 0
    moved: int = 0
    quarantined_low_conf: int = 0
    quarantined_incomplete: int = 0
    skipped_below_floor: int = 0
    skipped_collision: int = 0
    failed: int = 0


# ── the main event ────────────────────────────────────────────────────────
def run_auto(
    settings: Settings,
    src: Path,
    dest: Path,
    quarantine: Path,
    dry_run: bool = False,
) -> tuple[AutoSummary, Path]:
    """Process every video under `src`, move confident hits into Plex structure
    under `dest`, quarantine the rest, log everything to `dest/auto-*.log`.
    """
    dest.mkdir(parents=True, exist_ok=True)
    quarantine.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = dest / f"auto-{stamp}.log"

    videos = iter_videos(src)
    summary = AutoSummary(total=len(videos))
    if not videos:
        console.print(f"[yellow]No video files found under[/] {src}")
        log_path.touch()
        return summary, log_path

    pipe = Pipeline(settings)
    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    try:
        with log_path.open("w", encoding="utf-8") as log_f, Progress(*columns, console=console) as progress:
            task = progress.add_task("auto", total=len(videos))
            for video in videos:
                progress.update(task, description=video.name[:60])
                entry = _process_one(pipe, video, settings, dest, quarantine, dry_run, summary)
                log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log_f.flush()  # survive a crash / interrupt
                progress.advance(task)
    finally:
        pipe.close()

    return summary, log_path


def _process_one(
    pipe: Pipeline,
    video: Path,
    settings: Settings,
    dest: Path,
    quarantine: Path,
    dry_run: bool,
    summary: AutoSummary,
) -> dict:
    base = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "src": str(video),
    }
    try:
        proposal = pipe.identify_one(video)
    except Exception as e:
        summary.failed += 1
        return {**base, "action": "fail", "error": f"identify: {e}"}

    base["confidence"] = proposal.confidence
    base["kind"] = proposal.kind
    base["tmdb_id"] = proposal.tmdb_id

    if proposal.confidence < settings.thresholds.review_floor:
        summary.skipped_below_floor += 1
        return {**base, "action": "skip_below_floor"}

    if proposal.confidence < settings.thresholds.auto_apply:
        return _quarantine(
            video, proposal, quarantine, dry_run, summary, base, reason="low_conf"
        )

    plex = plex_path(dest, proposal)
    if plex is None:
        return _quarantine(
            video, proposal, quarantine, dry_run, summary, base, reason="incomplete"
        )

    if plex.exists():
        summary.skipped_collision += 1
        return {**base, "action": "skip_collision", "dst": str(plex)}

    if not dry_run:
        try:
            plex.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(video), str(plex))
        except OSError as e:
            summary.failed += 1
            return {**base, "action": "fail", "error": f"move: {e}"}

    summary.moved += 1
    return {
        **base,
        "action": "move",
        "dst": str(plex),
        "title": (proposal.picked or {}).get("title"),
    }


def _quarantine(
    video: Path,
    proposal: Proposal,
    quarantine: Path,
    dry_run: bool,
    summary: AutoSummary,
    base: dict,
    *,
    reason: str,
) -> dict:
    q_dst = quarantine / video.name
    if not dry_run:
        try:
            q_dst.parent.mkdir(parents=True, exist_ok=True)
            if q_dst.exists():
                # Avoid clobber inside quarantine
                q_dst = q_dst.with_stem(q_dst.stem + "." + datetime.now().strftime("%H%M%S"))
            shutil.move(str(video), str(q_dst))
            sidecar = q_dst.with_suffix(q_dst.suffix + ".quorum.json")
            sidecar.write_text(
                json.dumps(asdict(proposal), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            summary.failed += 1
            return {**base, "action": "fail", "error": f"quarantine: {e}"}

    if reason == "low_conf":
        summary.quarantined_low_conf += 1
    else:
        summary.quarantined_incomplete += 1
    return {**base, "action": f"quarantine_{reason}", "dst": str(q_dst)}


# ── undo ─────────────────────────────────────────────────────────────────
def undo_log(log_path: Path, dry_run: bool = False) -> tuple[int, int, int]:
    """Reverse the moves recorded in a Quorum auto-run log."""
    reversed_count = skipped = failed = 0

    # Reverse order — last move first, so nested dir creates collapse cleanly
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for entry in reversed(entries):
        action = entry.get("action", "")
        if not action.startswith(("move", "quarantine")):
            skipped += 1
            continue
        src = Path(entry["src"])
        dst = Path(entry.get("dst", ""))
        if not dst or not dst.exists():
            console.print(f"[yellow]GONE[/]   {dst}")
            skipped += 1
            continue
        if src.exists():
            console.print(f"[yellow]SRC-EXISTS[/] {src} — leaving {dst} alone")
            skipped += 1
            continue
        if dry_run:
            console.print(f"[cyan]DRY[/]   {dst.name}  ->  {src}")
            reversed_count += 1
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            # Best-effort sidecar cleanup
            sidecar = dst.with_suffix(dst.suffix + ".quorum.json")
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass
            console.print(f"[green]OK[/]    {dst.name}  ->  {src.name}")
            reversed_count += 1
        except OSError as e:
            console.print(f"[red]FAIL[/]  {dst}: {e}")
            failed += 1
    return reversed_count, skipped, failed


# ── pretty summary ────────────────────────────────────────────────────────
def print_summary(summary: AutoSummary, log_path: Path, dry_run: bool) -> None:
    title = "Quorum auto-run"
    if dry_run:
        title += " (DRY RUN — nothing moved)"
    t = Table(title=title)
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("[green]moved to Plex structure[/]", str(summary.moved))
    t.add_row("[yellow]quarantined (low confidence)[/]", str(summary.quarantined_low_conf))
    t.add_row("[yellow]quarantined (missing year/episode)[/]", str(summary.quarantined_incomplete))
    t.add_row("[dim]skipped (below review floor)[/]", str(summary.skipped_below_floor))
    t.add_row("[dim]skipped (destination already exists)[/]", str(summary.skipped_collision))
    t.add_row("[red]failed[/]", str(summary.failed))
    t.add_row("[bold]total[/]", str(summary.total))
    console.print(t)
    console.print(f"Log: [bold]{log_path}[/]")
    console.print(f"Undo: [dim]quorum undo {log_path}[/]")
