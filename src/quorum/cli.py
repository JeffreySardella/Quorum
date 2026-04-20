from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import load_settings
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


def _settings(config: Path | None):
    cfg = config or Path("config.toml")
    return load_settings(cfg if cfg.exists() else None)


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
):
    """Watch each video and generate Plex-compatible .nfo sidecars.

    For every video under ROOT, extracts keyframes + audio, runs vision LLM
    and Whisper on them, and synthesizes a title + description. Writes a
    `.nfo` sidecar next to each video so Plex picks up real metadata instead
    of just filenames.

    Also produces an `enrich-mislabels-*.log` listing videos whose content
    looks like it disagrees with the folder name — review those by hand.
    """
    settings = _settings(config)
    console.print(f"[bold cyan]enrich[/] root=[dim]{root}[/]")
    summary, log_path, mislabel_path = run_enrich(settings, root, force=force, use_whisper=(not no_whisper))
    print_enrich_summary(summary, log_path, mislabel_path)


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


if __name__ == "__main__":
    app()
