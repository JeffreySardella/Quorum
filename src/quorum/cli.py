from __future__ import annotations

import json as _json
import sys
from pathlib import Path

# Force UTF-8 I/O on Windows — filenames with Korean / emoji / non-cp1252 chars
# otherwise crash the pipeline with UnicodeEncodeError during logging.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import typer
from rich.console import Console
from rich.table import Table

from .config import load_settings
from .db import QuorumDB, migrate_from_legacy
from .enrich import print_summary as print_enrich_summary
from .enrich import run_enrich
from .home_videos import print_summary as print_home_summary
from .home_videos import run_home_videos
from .organize import print_summary, run_auto, undo_log
from .photos import print_summary as print_photos_summary
from .photos import run_photos
from .pipeline import Pipeline, apply_queue, write_queue
from .triage import print_summary as print_triage_summary
from .triage import run_triage


app = typer.Typer(
    help="Quorum — multi-signal video identification for Plex libraries.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

# ------------------------------------------------------------------
# db sub-command group
# ------------------------------------------------------------------

db_app = typer.Typer(help="Manage the Quorum metadata index.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command()
def stats(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show statistics about the Quorum metadata index."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        data = db.stats()
    table = Table(title="Quorum DB Stats", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total media", str(data["total_media"]))
    for media_type, count in data["by_type"].items():
        table.add_row(f"  {media_type}", str(count))
    table.add_row("Total size (bytes)", str(data["total_size"]))
    table.add_row("Total events", str(data["total_events"]))
    table.add_row("Total tags", str(data["total_tags"]))
    table.add_row("Pending jobs", str(data["pending_jobs"]))
    console.print(table)


@db_app.command()
def migrate(
    root: Path = typer.Argument(..., help="Library root to scan for legacy data."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Import existing .nfo sidecars, faces.db, and watch-state into quorum.db."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        counts = migrate_from_legacy(db, root)
    console.print("[green]Migration complete:[/]")
    console.print(f"  Media files indexed: {counts['media_indexed']}")
    console.print(f"  .nfo files imported: {counts['nfo_imported']}")
    console.print(f"  Face records imported: {counts['faces_imported']}")


@db_app.command("export")
def db_export(
    output: Path = typer.Argument(..., help="Output JSON file path."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Export the entire metadata index to a JSON file."""
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        data = db.export_all()
    output.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Exported to {output}[/]")

@db_app.command("index")
def db_index(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Build or rebuild the search index for all media."""
    from .db import QuorumDB
    from .search import SearchEngine

    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        engine = SearchEngine(s, db)
        try:
            counts = engine.index_all()
        finally:
            engine.close()
    console.print("[green]Indexing complete:[/]")
    console.print(f"  Text (FTS5): {counts['text_indexed']} media files indexed")
    console.print(f"  Vector: {counts['vector_indexed']} embeddings generated")


# ------------------------------------------------------------------
# events sub-command group
# ------------------------------------------------------------------

events_app = typer.Typer(help="Manage auto-detected events.", no_args_is_help=True)
app.add_typer(events_app, name="events")


@events_app.command("detect")
def events_detect(
    gap_hours: float = typer.Option(None, "--gap", help="Hours between events (overrides config)."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Auto-detect events by clustering media on timestamps."""
    from .db import QuorumDB
    from .events import detect_events
    s = _settings(config)
    gap = gap_hours if gap_hours is not None else s.events.gap_hours
    with QuorumDB(s.db_path) as db:
        result = detect_events(db, gap_hours=gap, ollama_url=s.ollama_url, model=s.models.text)
    console.print("[green]Event detection complete:[/]")
    console.print(f"  Events created: {result['events_created']}")
    console.print(f"  Media assigned: {result['media_assigned']}")


@events_app.command("list")
def events_list(
    year: str = typer.Option(None, "--year", help="Filter by year (e.g. 2024)."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """List all events."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        events = db.list_events()
        if year:
            events = [e for e in events if e.get("start_time", "").startswith(year)]
        t = Table(title="Events")
        t.add_column("ID", justify="right")
        t.add_column("name")
        t.add_column("date")
        t.add_column("files", justify="right")
        for event in events:
            media_count = len(db.get_event_media(event["id"]))
            date_str = event.get("start_time", "")[:10] if event.get("start_time") else "-"
            t.add_row(str(event["id"]), event["name"], date_str, str(media_count))
    console.print(t)


@events_app.command("show")
def events_show(
    event: str = typer.Argument(..., help="Event ID or name."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show files in an event."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        # Try as ID first, then name search
        try:
            eid = int(event)
            ev = db.get_event(eid)
        except ValueError:
            ev = None
            for e in db.list_events():
                if event.lower() in e["name"].lower():
                    ev = e
                    break
        if not ev:
            console.print(f"[red]Event not found:[/] {event}")
            raise typer.Exit(1)
        console.print(f"[bold]{ev['name']}[/]")
        start_str = (ev.get("start_time") or "")[:10]
        end_str = (ev.get("end_time") or "")[:10]
        console.print(f"Date: {start_str} to {end_str}")
        media = db.get_event_media(ev["id"])
        t = Table()
        t.add_column("type")
        t.add_column("file")
        for m in media:
            t.add_row(m["type"], Path(m["path"]).name)
    console.print(t)


@events_app.command("merge")
def events_merge(
    id1: int = typer.Argument(..., help="First event ID."),
    id2: int = typer.Argument(..., help="Second event ID."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Merge two events into one."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        e1 = db.get_event(id1)
        e2 = db.get_event(id2)
        if not e1 or not e2:
            console.print("[red]One or both events not found.[/]")
            raise typer.Exit(1)
        # Move all media from e2 to e1
        for m in db.get_event_media(id2):
            db.assign_media_to_event(m["id"], id1)
        # Update time range
        starts = [e1.get("start_time", ""), e2.get("start_time", "")]
        ends = [e1.get("end_time", ""), e2.get("end_time", "")]
        db.update_event(id1, start_time=min(s for s in starts if s) if any(starts) else None,
                        end_time=max(s for s in ends if s) if any(ends) else None)
        db.delete_event(id2)
    console.print(f"[green]Merged event {id2} into {id1}[/]")


@events_app.command("rename")
def events_rename(
    event_id: int = typer.Argument(..., help="Event ID."),
    name: str = typer.Argument(..., help="New name."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Rename an event."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        if not db.get_event(event_id):
            console.print(f"[red]Event {event_id} not found.[/]")
            raise typer.Exit(1)
        db.update_event(event_id, name=name)
    console.print(f"[green]Renamed event {event_id} to '{name}'[/]")


# Global state set by the --cpu-only callback
_cpu_only_override: bool = False

@app.callback()
def _main_callback(
    cpu_only: bool = typer.Option(False, "--cpu-only", envvar="QUORUM_CPU_ONLY",
                                   help="Force all ONNX components to use CPU (no DirectML)."),
):
    global _cpu_only_override
    _cpu_only_override = cpu_only


def _settings(config: Path | None):
    cfg = config or Path("config.toml")
    s = load_settings(cfg if cfg.exists() else None)
    if _cpu_only_override:
        s.cpu_only = True
    return s


@app.command()
def scan(
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                                help="Directory to scan recursively"),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
):
    """Walk ROOT, identify videos, and write proposals to the review queue."""
    settings = _settings(config)
    pipe = Pipeline(settings)
    try:
        proposals = pipe.scan(root)
        # Write dedup log if fingerprint signal collected fingerprints
        for sig in pipe.signals:
            if hasattr(sig, "find_duplicates"):
                duplicates = sig.find_duplicates()
                if duplicates:
                    from .signals.fingerprint import write_dedup_log
                    dedup_path = write_dedup_log(duplicates, root)
                    console.print(f"[cyan]Found {len(duplicates)} potential duplicate pair(s) → {dedup_path}[/]")
    finally:
        pipe.close()

    n = write_queue(proposals, settings.paths.review_queue, settings.thresholds.review_floor)

    table = Table(title=f"Quorum scan — {len(proposals)} files, {n} queued", show_lines=False)
    table.add_column("conf", justify="right", width=5)
    table.add_column("current", overflow="ellipsis", max_width=55)
    table.add_column("proposed", overflow="ellipsis", max_width=55)
    for p in sorted(proposals, key=lambda x: -x.confidence)[:30]:
        if p.confidence >= settings.thresholds.auto_apply:
            color = "green"
        elif p.confidence >= settings.thresholds.review_floor:
            color = "yellow"
        else:
            color = "red"
        table.add_row(f"[{color}]{p.confidence:.2f}[/]", p.current_name, p.proposed_name)
    console.print(table)
    console.print(f"Queue written to [bold]{settings.paths.review_queue}[/].")
    console.print(
        f"Thresholds — auto_apply=[green]{settings.thresholds.auto_apply}[/] "
        f"review_floor=[yellow]{settings.thresholds.review_floor}[/]"
    )


@app.command()
def apply(
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be renamed without doing it."),
):
    """Apply high-confidence renames from the review queue (in-place, same directory)."""
    settings = _settings(config)
    q = settings.paths.review_queue
    if not q.exists():
        console.print(f"[red]Queue not found:[/] {q}. Run `quorum scan` first.")
        raise typer.Exit(1)
    applied, skipped, failed = apply_queue(q, settings.thresholds.auto_apply, dry_run=dry_run)
    console.print(f"\napplied={applied} skipped={skipped} failed={failed}")


@app.command()
def auto(
    src: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                               help="Source directory (messy library) — processed recursively"),
    dest: Path = typer.Argument(..., resolve_path=True,
                                help="Destination root. Plex structure is created here."),
    quarantine: Path = typer.Option(
        None, "--quarantine", "-q", resolve_path=True,
        help="Where to park low-confidence files. Default: <dest>/_quarantine",
    ),
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Process everything but don't move any files."),
):
    """Fully autonomous: identify, organize into Plex structure, and quarantine the uncertain.

    Writes a JSONL log at <dest>/auto-<timestamp>.log — pass that to `quorum undo`
    to reverse the run.

    Layout created under DEST:
      Movies/Title (Year)/Title (Year).ext
      TV Shows/Show Name/Season 01/Show Name - s01e02.ext
    """
    settings = _settings(config)
    q_dir = quarantine or (dest / "_quarantine")

    console.print(f"[bold cyan]auto[/] src=[dim]{src}[/] dest=[dim]{dest}[/] quarantine=[dim]{q_dir}[/]")
    if dry_run:
        console.print("[yellow]DRY RUN — no files will move.[/]")

    summary, log_path = run_auto(settings, src, dest, q_dir, dry_run=dry_run)
    print_summary(summary, log_path, dry_run=dry_run)


@app.command("home-videos")
def home_videos_cmd(
    src: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                               help="Source directory of home-video event folders"),
    dest: Path = typer.Argument(..., resolve_path=True,
                                help="Destination root. Creates `Home Videos/YYYY/YYYY-MM - Event/`"),
    quarantine: Path = typer.Option(
        None, "--quarantine", "-q", resolve_path=True,
        help="Where to park folders with no parseable year. Default: <dest>/_quarantine",
    ),
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate, move nothing"),
    no_llm: bool = typer.Option(
        False, "--no-llm",
        help="Skip the LLM folder-name parser and use regex only (faster, less accurate).",
    ),
):
    """Organize home-video folders by year/event. Trusts the folder name.

    No TMDB, no Whisper, no vision. One text-LLM call per folder to parse the
    folder name into (year, month, description). Fast. Designed for family
    archives where the folder name is already the best source of truth.
    """
    settings = _settings(config)
    q_dir = quarantine or (dest / "_quarantine")

    console.print(
        f"[bold cyan]home-videos[/] src=[dim]{src}[/] dest=[dim]{dest}[/] "
        f"quarantine=[dim]{q_dir}[/]"
        + ("  [yellow][dry-run][/]" if dry_run else "")
        + ("  [dim][regex-only][/]" if no_llm else "")
    )
    summary, log_path = run_home_videos(
        settings, src, dest, q_dir, dry_run=dry_run, use_llm=(not no_llm),
    )
    print_home_summary(summary, log_path, dry_run=dry_run)


@app.command()
def triage(
    src: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                               help="Directory of mixed home + commercial video files"),
    config: Path = typer.Option(None, "--config", "-c"),
):
    """Classify each video filename in SRC as home vs commercial.

    Writes two plain-text manifests (`triage-home-*.txt` and
    `triage-commercial-*.txt`) alongside a full JSONL reasoning log.
    Nothing moves — you then feed each manifest to the right tool:

        # home videos (year/event organizer)
        quorum home-videos <folder you built from the home manifest> <dest>

        # commercial movies (identify mode)
        quorum auto <folder you built from the commercial manifest> <dest>
    """
    settings = _settings(config)
    console.print(f"[bold cyan]triage[/] src=[dim]{src}[/]")
    summary, log_path, h, c, u = run_triage(settings, src)
    print_triage_summary(summary, log_path, h, c, u)


@app.command()
def photos(
    src: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                               help="Source directory of photos (scanned recursively)"),
    dest: Path = typer.Argument(..., resolve_path=True,
                                help="Destination root. Creates `Photos/YYYY/YYYY-MM-DD/` layout."),
    quarantine: Path = typer.Option(
        None, "--quarantine", "-q", resolve_path=True,
        help="Where to park photos with no resolvable date. Default: <dest>/_quarantine",
    ),
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate, move nothing."),
):
    """Organize photos by EXIF capture date.

    For each image: tries EXIF DateTimeOriginal → filename date → parent folder
    year → file mtime, in that order. Moves into `Photos/YYYY/YYYY-MM-DD/`.

    HARD SKIPS any file inside an Aperture / iPhoto managed library — those
    have internal metadata that would corrupt the library if moved.
    """
    settings = _settings(config)
    q_dir = quarantine or (dest / "_quarantine")

    console.print(
        f"[bold cyan]photos[/] src=[dim]{src}[/] dest=[dim]{dest}[/] quarantine=[dim]{q_dir}[/]"
        + ("  [yellow][dry-run][/]" if dry_run else "")
    )
    summary, log_path = run_photos(settings, src, dest, q_dir, dry_run=dry_run)
    print_photos_summary(summary, log_path, dry_run=dry_run)


@app.command()
def enrich(
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                                help="Organized root (must contain a `Home Videos` subfolder)"),
    config: Path = typer.Option(None, "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Regenerate .nfo even if one already exists."),
    no_whisper: bool = typer.Option(
        False, "--no-whisper",
        help="Skip audio transcription. ~2-3x faster. Loses audio-derived detail (names, quotes).",
    ),
    no_rename: bool = typer.Option(
        False, "--no-rename",
        help="Skip automatic folder rename after enrichment.",
    ),
    no_subs: bool = typer.Option(
        False, "--no-subs",
        help="Skip subtitle (.srt) generation.",
    ),
    no_chapters: bool = typer.Option(
        False, "--no-chapters",
        help="Skip chapter detection for long videos.",
    ),
):
    """Watch each video and generate Plex-compatible .nfo sidecars.

    For every video under ROOT, extracts keyframes + audio, runs vision LLM
    and Whisper on them, and synthesizes a title + description. Writes a
    `.nfo` sidecar next to each video so Plex picks up real metadata instead
    of just filenames.

    Also produces an `enrich-mislabels-*.log` listing videos whose content
    looks like it disagrees with the folder name — review those by hand.

    After enrichment, automatically runs a folder-rename pass on fully
    enriched folders. Use --no-rename to skip this step.
    """
    settings = _settings(config)
    console.print(f"[bold cyan]enrich[/] root=[dim]{root}[/]")
    summary, log_path, mislabel_path = run_enrich(
        settings, root, force=force, use_whisper=(not no_whisper),
        no_rename=no_rename, no_subs=no_subs, no_chapters=no_chapters,
    )
    print_enrich_summary(summary, log_path, mislabel_path)


@app.command("enrich-photos")
def enrich_photos_cmd(
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                                help="Photo library root (must contain Photos/ subfolder)"),
    config: Path = typer.Option(None, "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Regenerate all sidecars."),
    no_faces: bool = typer.Option(False, "--no-faces", help="Scene tagging only, skip face clustering."),
):
    """Tag photos with scene descriptions and cluster faces.

    Walks Photos/YYYY/YYYY-MM-DD/ directories, runs vision LLM on each photo
    for scene tags, and optionally clusters faces using InsightFace. Writes
    .quorum.json + .nfo sidecars.
    """
    settings = _settings(config)
    console.print(f"[bold cyan]enrich-photos[/] root=[dim]{root}[/]")
    from .enrich_photos import print_summary as print_ep_summary
    from .enrich_photos import run_enrich_photos
    summary, log_path = run_enrich_photos(settings, root, force=force, do_faces=(not no_faces))
    print_ep_summary(summary, log_path)


@app.command("rename-folders")
def rename_folders_cmd(
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                                help="Library root (must contain Home Videos/ subfolder)"),
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show proposed renames without doing them."),
):
    """Rename event folders based on enriched .nfo metadata.

    Walks Home Videos/YYYY/ directories and proposes clean folder names
    using an LLM that reads the .nfo titles and descriptions. Only renames
    fully-enriched folders (every video has an .nfo).

    Writes rename-folders-<timestamp>.log — pass to `quorum undo` to reverse.
    """
    settings = _settings(config)
    console.print(f"[bold cyan]rename-folders[/] root=[dim]{root}[/]")
    if dry_run:
        console.print("[yellow]DRY RUN — no folders will be renamed.[/]")
    from .rename_folders import print_summary as print_rf_summary
    from .rename_folders import run_rename_folders
    summary, log_path = run_rename_folders(settings, root, dry_run=dry_run)
    print_rf_summary(summary, log_path, dry_run=dry_run)


@app.command()
def watch(
    config: Path = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log what would be processed without doing it."),
):
    """Start the watch-folder daemon.

    Monitors inbox directories defined in config.toml [watch] section and
    automatically processes new files through the appropriate pipeline.
    Press Ctrl+C to stop.
    """
    settings = _settings(config)
    if not settings.watch.inboxes:
        console.print("[red]No watch inboxes configured. Add [[watch.inbox]] sections to config.toml.[/]")
        raise typer.Exit(1)
    from .watch import run_watch
    run_watch(settings, dry_run=dry_run)


@app.command()
def serve(
    config: Path = typer.Option(None, "--config", "-c"),
    port: int = typer.Option(None, "--port", "-p", help="Override port (default: from config or 8080)."),
    no_watch: bool = typer.Option(False, "--no-watch", help="Don't start the watch daemon."),
):
    """Start the Quorum web UI (and optionally the watch daemon).

    Opens a browser-based interface for managing your media library.
    Also starts the watch-folder daemon in the background unless --no-watch.
    """
    import threading

    import uvicorn

    from .web.app import create_app
    from .web.jobs import JobRegistry

    settings = _settings(config)
    actual_port = port or settings.web.port

    jobs = JobRegistry()
    web_app = create_app(settings, jobs)

    if not no_watch and settings.watch.inboxes:
        from .watch import run_watch
        t = threading.Thread(target=run_watch, args=(settings,), daemon=True)
        t.start()
        console.print("[green]Watch daemon started in background.[/]")

    console.print(f"[bold cyan]Quorum web UI[/] → http://localhost:{actual_port}")
    uvicorn.run(web_app, host="0.0.0.0", port=actual_port, log_level="warning")


@app.command()
def collections(
    root: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True,
                                help="Library root with enriched content"),
    config: Path = typer.Option(None, "--config", "-c"),
    min_person: int = typer.Option(3, "--min-person", help="Min distinct folders a person must appear in."),
    min_theme: int = typer.Option(3, "--min-theme", help="Min distinct events for a theme collection."),
):
    """Auto-generate Plex collections from face clusters and scene tags.

    Injects <set> tags into existing .nfo files so Plex groups related
    content into browsable collections like 'Videos with Sophia' or 'Beach'.
    """
    settings = _settings(config)
    console.print(f"[bold cyan]collections[/] root=[dim]{root}[/]")
    from .collections import print_summary as print_coll_summary
    from .collections import run_collections
    summary, log_path = run_collections(settings, root, min_person, min_theme)
    print_coll_summary(summary, log_path)


@app.command()
def dashboard(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show a rich dashboard overview of your media library."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        stats = db.dashboard_stats()

    from rich.panel import Panel
    from rich.text import Text

    # Key metrics panel
    metrics = Text()
    metrics.append(f"  Total files: {stats['total_media']}\n")
    total_gb = stats['total_size'] / (1024 ** 3)
    metrics.append(f"  Total size:  {total_gb:.2f} GB\n")
    metrics.append(f"  Events:      {stats['total_events']}\n")
    metrics.append(f"  Tags:        {stats['total_tags']}\n")
    metrics.append(f"  Pending:     {stats['pending_jobs']}\n")
    console.print(Panel(metrics, title="Library Overview"))

    # Files by type
    if stats["by_type"]:
        t = Table(title="Files by Type")
        t.add_column("type")
        t.add_column("count", justify="right")
        t.add_column("size", justify="right")
        for media_type, count in sorted(stats["by_type"].items()):
            size_mb = stats["storage_by_type"].get(media_type, 0) / (1024 ** 2)
            t.add_row(media_type, str(count), f"{size_mb:.1f} MB")
        console.print(t)

    # Files by year
    if stats["by_year"]:
        t = Table(title="Files by Year")
        t.add_column("year")
        t.add_column("count", justify="right")
        for year, count in sorted(stats["by_year"].items()):
            t.add_row(year, str(count))
        console.print(t)

    # Top faces
    if stats["top_faces"]:
        t = Table(title="Top Faces")
        t.add_column("person")
        t.add_column("appearances", justify="right")
        for face in stats["top_faces"]:
            t.add_row(face["name"], str(face["count"]))
        console.print(t)

    # Confidence distribution
    if any(stats["confidence_dist"]):
        t = Table(title="Confidence Distribution")
        t.add_column("range")
        t.add_column("count", justify="right")
        t.add_column("bar")
        max_val = max(stats["confidence_dist"]) or 1
        for i, count in enumerate(stats["confidence_dist"]):
            low = i / 10
            high = (i + 1) / 10
            bar_len = int(count / max_val * 20) if max_val else 0
            t.add_row(f"{low:.1f}-{high:.1f}", str(count), "█" * bar_len)
        console.print(t)

    # Recent activity
    if stats["recent_actions"]:
        t = Table(title=f"Recent Activity (last {len(stats['recent_actions'])})")
        t.add_column("time")
        t.add_column("action")
        t.add_column("source")
        for action in stats["recent_actions"][:20]:
            t.add_row(
                action.get("created_at", "")[:19],
                action.get("operation", ""),
                Path(action.get("source_path", "")).name,
            )
        console.print(t)


@app.command()
def gui():
    """Launch the Quorum desktop GUI (customtkinter wrapper over all commands)."""
    from .gui import main as gui_main
    gui_main()


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural language search query."),
    type: str = typer.Option(None, "--type", "-t", help="Filter by media type (video, photo)."),
    after: str = typer.Option(None, "--after", help="Only results after this date (YYYY-MM-DD)."),
    before: str = typer.Option(None, "--before", help="Only results before this date (YYYY-MM-DD)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Search your media library with natural language."""
    from .db import QuorumDB
    from .search import SearchEngine

    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        engine = SearchEngine(s, db)
        try:
            results = engine.search(query, media_type=type, after=after, before=before, limit=limit)
        finally:
            engine.close()

    if not results:
        console.print(f"[yellow]No results for:[/] {query}")
        return

    t = Table(title=f"Search: {query}")
    t.add_column("#", justify="right")
    t.add_column("type")
    t.add_column("score", justify="right")
    t.add_column("file")
    t.add_column("snippet")
    for i, r in enumerate(results, 1):
        score = f"{r['score']:.2f}"
        name = Path(r["path"]).name
        snippet = r.get("snippet", "")[:60]
        t.add_row(str(i), r["type"], score, name, snippet)
    console.print(t)
    method = results[0].get("search_method", "unknown") if results else "none"
    console.print(f"[dim]Search method: {method} | {len(results)} results[/]")


@app.command()
def undo(
    log: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True,
                               help="Path to an auto-*.log JSONL file from a previous auto run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be reverted without touching files."),
):
    """Reverse every move recorded in an auto-run log."""
    reversed_count, skipped, failed = undo_log(log, dry_run=dry_run)
    console.print(
        f"\nreverted={reversed_count} skipped={skipped} failed={failed}"
        + ("  [yellow](dry run)[/]" if dry_run else "")
    )


dedup_app = typer.Typer(help="Detect and manage duplicate files.", no_args_is_help=True)
app.add_typer(dedup_app, name="dedup")


@dedup_app.command("scan")
def dedup_scan(
    aggressive: bool = typer.Option(False, "--aggressive", help="Include near-matches and cross-media."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Scan for duplicate files."""
    from .db import QuorumDB
    from .dedup import scan_duplicates, save_report
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        report = scan_duplicates(db, aggressive=aggressive)
    report_path = Path("dedup-report.json")
    save_report(report, report_path)
    console.print("[green]Dedup scan complete:[/]")
    console.print(f"  Files scanned: {report.total_files_scanned}")
    console.print(f"  Duplicate clusters: {len(report.clusters)}")
    console.print(f"  Total duplicates: {report.total_duplicates}")
    console.print(f"  Report saved to: {report_path}")

    if report.clusters:
        t = Table(title="Duplicate Clusters")
        t.add_column("cluster", justify="right")
        t.add_column("strategy")
        t.add_column("files", justify="right")
        t.add_column("keep")
        for cluster in report.clusters:
            keep_file = next((f for f in cluster.files if f.media_id == cluster.recommended_keep), None)
            keep_name = Path(keep_file.path).name if keep_file else "-"
            t.add_row(str(cluster.id), cluster.strategy, str(len(cluster.files)), keep_name)
        console.print(t)


@dedup_app.command("report")
def dedup_report(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """View the last dedup scan results."""
    from .dedup import load_report
    report_path = Path("dedup-report.json")
    if not report_path.exists():
        console.print("[yellow]No dedup report found. Run 'quorum dedup scan' first.[/]")
        return
    report = load_report(report_path)
    console.print(f"Scanned at: {report.scanned_at}")
    console.print(f"Files: {report.total_files_scanned} | Duplicates: {report.total_duplicates}")

    for cluster in report.clusters:
        console.print(f"\n[bold]Cluster {cluster.id}[/] ({cluster.strategy})")
        t = Table()
        t.add_column("keep?")
        t.add_column("file")
        t.add_column("size", justify="right")
        t.add_column("type")
        for f in cluster.files:
            keep = "✓" if f.media_id == cluster.recommended_keep else ""
            t.add_row(keep, Path(f.path).name, str(f.size), f.media_type)
        console.print(t)


@dedup_app.command("apply")
def dedup_apply(
    cluster: int = typer.Option(None, "--cluster", help="Apply to specific cluster only."),
    holding: Path = typer.Option(Path("_dedup_holding"), "--holding", help="Holding directory."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Move duplicate files to holding directory (reversible via undo)."""
    from .db import QuorumDB
    from .dedup import load_report, apply_dedup
    report_path = Path("dedup-report.json")
    if not report_path.exists():
        console.print("[yellow]No dedup report found. Run 'quorum dedup scan' first.[/]")
        return
    report = load_report(report_path)
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        result = apply_dedup(db, report, holding, cluster_id=cluster)
    console.print("[green]Dedup apply complete:[/]")
    console.print(f"  Moved: {result['moved']}")
    console.print(f"  Skipped: {result['skipped']}")
    console.print(f"  Failed: {result['failed']}")
    console.print(f"  Holding dir: {holding}")


@app.command("review")
def review_cmd(
    count: int = typer.Option(10, "--count", "-n", help="Number of items to show."),
    sort: str = typer.Option("confidence", "--sort", "-s", help="Sort: confidence or newest."),
    type: str = typer.Option(None, "--type", "-t", help="Filter by media type."),
    stats_only: bool = typer.Option(False, "--stats", help="Show stats only."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show items pending review."""
    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        if stats_only:
            st = db.review_stats()
            t = Table(title="Review Stats")
            t.add_column("metric")
            t.add_column("count", justify="right")
            t.add_row("Total with signals", str(st["total_with_signals"]))
            t.add_row("[green]Approved[/]", str(st["approved"]))
            t.add_row("[red]Rejected[/]", str(st["rejected"]))
            t.add_row("[yellow]Corrected[/]", str(st["corrected"]))
            t.add_row("[bold]Pending[/]", str(st["pending"]))
            console.print(t)
            return

        queue = db.get_review_queue(sort=sort, media_type=type, limit=count)

    if not queue:
        console.print("[green]No items pending review.[/]")
        return

    t = Table(title=f"Review Queue ({len(queue)} items)")
    t.add_column("ID", justify="right")
    t.add_column("type")
    t.add_column("confidence", justify="right")
    t.add_column("candidates")
    t.add_column("file")
    for item in queue:
        conf = f"{item.get('max_conf', 0):.2f}"
        candidates = item.get("candidates", "")[:50]
        name = Path(item["path"]).name
        t.add_row(str(item["id"]), item["type"], conf, candidates, name)
    console.print(t)
    console.print("[dim]Use 'quorum approve <ID>', 'quorum reject <ID>', or 'quorum correct <ID> \"Title\"' to review.[/]")


@app.command()
def approve(
    media_id: int = typer.Argument(..., help="Media ID to approve."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Approve a proposed identification."""
    from datetime import datetime

    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        item = db.get_review_item(media_id)
        if not item:
            console.print(f"[red]Media {media_id} not found.[/]")
            raise typer.Exit(1)
        if not item["signals"]:
            console.print(f"[yellow]No signals for media {media_id}.[/]")
            raise typer.Exit(1)
        candidate = item["top_candidate"] or "unknown"
        db.insert_feedback(
            media_id, "approve", candidate,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
    console.print(f"[green]Approved:[/] {candidate} for {Path(item['path']).name}")


@app.command()
def reject(
    media_id: int = typer.Argument(..., help="Media ID to reject."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Reject a proposed identification (keep original)."""
    from datetime import datetime

    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        item = db.get_review_item(media_id)
        if not item:
            console.print(f"[red]Media {media_id} not found.[/]")
            raise typer.Exit(1)
        candidate = item["top_candidate"] or "unknown"
        db.insert_feedback(
            media_id, "reject", candidate,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
    console.print(f"[yellow]Rejected:[/] {candidate} for {Path(item['path']).name}")


@app.command()
def correct(
    media_id: int = typer.Argument(..., help="Media ID to correct."),
    title: str = typer.Argument(..., help="Correct title (e.g. 'The Matrix (1999)')."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Manually correct an identification."""
    from datetime import datetime

    from .db import QuorumDB
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        item = db.get_review_item(media_id)
        if not item:
            console.print(f"[red]Media {media_id} not found.[/]")
            raise typer.Exit(1)
        original = item["top_candidate"] or "unknown"
        db.insert_feedback(
            media_id, "correct", original,
            correction=title,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
    console.print(f"[green]Corrected:[/] {Path(item['path']).name} → {title}")


notify_app = typer.Typer(help="Manage processing notifications.", no_args_is_help=True)
app.add_typer(notify_app, name="notify")


@notify_app.command("test")
def notify_test(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Send a test notification to all enabled channels."""
    from .notify import setup_notifications
    s = _settings(config)
    bus = setup_notifications(s)
    bus.emit("test", "This is a test notification from Quorum.", {"source": "notify test"})
    console.print("[green]Test notification sent to all enabled channels.[/]")
    notify_cfg = getattr(s, "notify", None)
    if notify_cfg:
        console.print(f"  Desktop: {'enabled' if notify_cfg.desktop else 'disabled'}")
        console.print(f"  Webhook: {notify_cfg.webhook or 'disabled'}")
    else:
        console.print("  [yellow]No notification channels configured.[/]")


@notify_app.command("history")
def notify_history(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show recent notification history (current session only)."""
    console.print("[yellow]Notification history is only available during a running session.[/]")
    console.print("Start the web UI with 'quorum serve' to see live notifications.")


signals_app = typer.Typer(help="Manage signal weights and accuracy.", no_args_is_help=True)
app.add_typer(signals_app, name="signals")


@signals_app.command("weights")
def signals_weights(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show current signal weights."""
    import json
    weights_path = Path("signal_weights.json")
    if weights_path.exists():
        weights = json.loads(weights_path.read_text(encoding="utf-8"))
    else:
        weights = {}

    t = Table(title="Signal Weights")
    t.add_column("signal")
    t.add_column("weight", justify="right")
    t.add_column("status")
    default_signals = ["filename", "vision", "transcript", "ocr", "fingerprint"]
    shown = set()
    for name in default_signals:
        w = weights.get(name, 1.0)
        status = "adjusted" if name in weights else "default"
        t.add_row(name, f"{w:.2f}", status)
        shown.add(name)
    for name, w in weights.items():
        if name not in shown:
            t.add_row(name, f"{w:.2f}", "adjusted")
    console.print(t)


@signals_app.command("retune")
def signals_retune(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show changes without applying."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Recalculate signal weights from review feedback."""
    from .db import QuorumDB
    from .feedback_loop import retune_signals
    s = _settings(config)
    with QuorumDB(s.db_path) as db:
        changes = retune_signals(db, dry_run=dry_run)
    if not changes:
        console.print("[yellow]No feedback data available for retuning.[/]")
        return
    t = Table(title="Signal Weight Changes" + (" (DRY RUN)" if dry_run else ""))
    t.add_column("signal")
    t.add_column("old", justify="right")
    t.add_column("new", justify="right")
    t.add_column("delta", justify="right")
    for name, info in changes.items():
        delta = info["delta"]
        color = "green" if delta > 0 else "red" if delta < 0 else "dim"
        t.add_row(name, f"{info['old']:.2f}", f"{info['new']:.2f}", f"[{color}]{delta:+.2f}[/{color}]")
    console.print(t)
    if not dry_run:
        console.print("[green]Weights saved to signal_weights.json[/]")


@signals_app.command("reset")
def signals_reset(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Reset signal weights to defaults."""
    weights_path = Path("signal_weights.json")
    if weights_path.exists():
        weights_path.unlink()
    console.print("[green]Signal weights reset to defaults (1.0 for all signals).[/]")


plugins_app = typer.Typer(help="Manage Quorum plugins.", no_args_is_help=True)
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list(
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """List all registered plugins."""
    from .engine import PluginRegistry
    registry = PluginRegistry.discover()
    plugins = registry.list_plugins()
    if not plugins:
        console.print("[yellow]No plugins found.[/]")
        console.print("Install plugins via pip or register built-in plugins.")
        return
    t = Table(title="Registered Plugins")
    t.add_column("name")
    t.add_column("file types")
    for p in plugins:
        t.add_row(p["name"], ", ".join(p["file_types"]))
    console.print(t)


@plugins_app.command("info")
def plugins_info(
    name: str = typer.Argument(..., help="Plugin name."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Show details about a plugin."""
    from .engine import PluginRegistry
    registry = PluginRegistry.discover()
    plugin = registry.get(name)
    if not plugin:
        console.print(f"[red]Plugin '{name}' not found.[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{plugin.name}[/]")
    console.print(f"File types: {', '.join(plugin.file_types)}")


music_app = typer.Typer(help="Organize music files.", no_args_is_help=True)
app.add_typer(music_app, name="music")


@music_app.command("scan")
def music_scan(
    src: Path = typer.Argument(..., help="Source directory with music files."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Scan music files and show organization proposals."""
    from .plugins.music import MusicPlugin
    plugin = MusicPlugin()
    plugin.on_register({})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No music files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    t = Table(title=f"Music Scan ({len(proposals)} files)")
    t.add_column("source")
    t.add_column("→")
    t.add_column("destination")
    t.add_column("confidence", justify="right")
    for p in proposals:
        t.add_row(Path(p.source_path).name, "→", p.dest_path, f"{p.confidence:.1f}")
    console.print(t)


@music_app.command("apply")
def music_apply(
    src: Path = typer.Argument(..., help="Source directory with music files."),
    dest: Path = typer.Argument(..., help="Destination root for organized music."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Organize music files into Plex-friendly structure."""
    from .plugins.music import MusicPlugin
    plugin = MusicPlugin()
    plugin.on_register({"dest_root": dest})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No music files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    results = plugin.on_apply(proposals)
    moved = sum(1 for r in results if r["status"] == "moved")
    failed = sum(1 for r in results if r["status"] == "failed")
    console.print("[green]Music organization complete:[/]")
    console.print(f"  Moved: {moved}")
    console.print(f"  Failed: {failed}")


audio_app = typer.Typer(help="Organize audio memos.", no_args_is_help=True)
app.add_typer(audio_app, name="audio")


@audio_app.command("scan")
def audio_scan(
    src: Path = typer.Argument(..., help="Source directory with audio files."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Scan audio memos and show organization proposals."""
    from .plugins.audio import AudioMemoPlugin
    plugin = AudioMemoPlugin()
    plugin.on_register({})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No audio memo files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    t = Table(title=f"Audio Scan ({len(proposals)} memos)")
    t.add_column("source")
    t.add_column("→")
    t.add_column("destination")
    for p in proposals:
        t.add_row(Path(p.source_path).name, "→", p.dest_path)
    console.print(t)


@audio_app.command("apply")
def audio_apply(
    src: Path = typer.Argument(..., help="Source directory."),
    dest: Path = typer.Argument(..., help="Destination root."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Organize audio memos by date and topic."""
    from .plugins.audio import AudioMemoPlugin
    plugin = AudioMemoPlugin()
    plugin.on_register({"dest_root": dest})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No audio memo files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    results = plugin.on_apply(proposals)
    moved = sum(1 for r in results if r["status"] == "moved")
    console.print(f"[green]Audio organization complete:[/] {moved} memos organized")


docs_app = typer.Typer(help="Organize documents.", no_args_is_help=True)
app.add_typer(docs_app, name="docs")


@docs_app.command("scan")
def docs_scan(
    src: Path = typer.Argument(..., help="Source directory with documents."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Scan documents and show organization proposals."""
    from .plugins.docs import DocumentPlugin
    plugin = DocumentPlugin()
    plugin.on_register({})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No document files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    t = Table(title=f"Document Scan ({len(proposals)} files)")
    t.add_column("source")
    t.add_column("category")
    t.add_column("→ destination")
    for p in proposals:
        cat = p.metadata.get("category", "?")
        t.add_row(Path(p.source_path).name, cat, p.dest_path)
    console.print(t)


@docs_app.command("apply")
def docs_apply(
    src: Path = typer.Argument(..., help="Source directory."),
    dest: Path = typer.Argument(..., help="Destination root."),
    config: Path = typer.Option(None, "--config", "-c", help="Path to config.toml"),
) -> None:
    """Organize documents by category and date."""
    from .plugins.docs import DocumentPlugin
    plugin = DocumentPlugin()
    plugin.on_register({"dest_root": dest})
    files = [f for f in src.rglob("*") if f.suffix.lower() in plugin.file_types]
    if not files:
        console.print(f"[yellow]No document files found in {src}[/]")
        return
    proposals = plugin.on_scan(files)
    results = plugin.on_apply(proposals)
    moved = sum(1 for r in results if r["status"] == "moved")
    console.print(f"[green]Document organization complete:[/] {moved} documents organized")


if __name__ == "__main__":
    app()
