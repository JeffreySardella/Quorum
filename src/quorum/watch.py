"""Watch-folder daemon -- monitors inbox directories and auto-processes new files.

Starts one watchdog Observer per configured inbox, queues new files, stabilizes
them (waits for writes to finish), then runs the appropriate Quorum pipeline
(auto / home-videos / photos) followed by enrichment.  Optionally refreshes
Plex libraries when processing completes.

State is persisted in ``watch-state.json`` at each inbox's dest root so the
daemon survives restarts without reprocessing files."""
from __future__ import annotations

import json, os, signal, time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from rich.console import Console

from .config import Settings, WatchInbox, WatchPlex
from .photos import PHOTO_EXTS
from .pipeline import VIDEO_EXTS

console = Console()
ALL_MEDIA_EXTS = VIDEO_EXTS | PHOTO_EXTS

# ── state persistence ────────────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}}

def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ── file stabilization ──────────────────────────────────────────────────

def _is_stable(path: Path, delay: float = 5.0) -> bool:
    """Check if file has stopped changing (same size after *delay* seconds)."""
    try:
        s1 = path.stat().st_size
        time.sleep(delay)
        s2 = path.stat().st_size
        return s1 == s2 and s1 > 0
    except OSError:
        return False

# ── JSONL logging ────────────────────────────────────────────────────────

def _log(log_f: TextIO | None, event: str, detail: str = "") -> None:
    if log_f:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "event": event, "detail": detail}
        log_f.write(json.dumps(entry) + "\n")
        log_f.flush()

# ── Plex refresh ─────────────────────────────────────────────────────────

def _refresh_plex(plex_config: WatchPlex, log_f: TextIO | None = None) -> None:
    if not plex_config.enabled:
        return
    token = plex_config.token or os.environ.get("PLEX_TOKEN", "")
    if not token:
        _log(log_f, "plex-skip", "No Plex token configured")
        return
    import httpx
    base = plex_config.url.rstrip("/")
    headers = {"X-Plex-Token": token}
    try:
        if plex_config.library_ids:
            ids = plex_config.library_ids
        else:
            import xml.etree.ElementTree as ET
            resp = httpx.get(f"{base}/library/sections", headers=headers, timeout=30)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            ids = [int(d.get("key")) for d in root.findall(".//Directory") if d.get("key")]
        for lib_id in ids:
            httpx.get(f"{base}/library/sections/{lib_id}/refresh",
                      headers=headers, timeout=30).raise_for_status()
            _log(log_f, "plex-refresh", f"Refreshed library {lib_id}")
            console.log(f"[green]Plex library {lib_id} refreshed[/]")
    except Exception as exc:
        _log(log_f, "plex-error", str(exc))
        console.log(f"[red]Plex refresh failed: {exc}[/]")

# ── per-file processing ─────────────────────────────────────────────────

def _determine_mode(file_path: Path, inbox: WatchInbox) -> str | None:
    """Return 'video' or 'photo' based on inbox mode and file extension."""
    ext = file_path.suffix.lower()
    mode = inbox.mode
    if mode == "photos":
        return "photo" if ext in PHOTO_EXTS else None
    if mode == "home-videos":
        return "video" if ext in VIDEO_EXTS else None
    # auto
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PHOTO_EXTS:
        return "photo"
    return None

def _process_file(
    file_path: Path, inbox: WatchInbox, settings: Settings,
    state: dict, state_path: Path, log_f: TextIO | None, dry_run: bool,
) -> None:
    """Process a single new file through the appropriate pipeline."""
    key = str(file_path)
    existing = state["files"].get(key)
    if existing and existing.get("status") == "done":
        return
    media_type = _determine_mode(file_path, inbox)
    if media_type is None:
        _log(log_f, "skip", f"Unsupported extension: {file_path.name}")
        return
    if not file_path.exists():
        _log(log_f, "skip", f"File vanished: {file_path}")
        return

    console.log(f"[bold cyan]watch[/] processing [dim]{file_path.name}[/] as {media_type}")
    _log(log_f, "process-start", f"{file_path} mode={media_type}")

    if dry_run:
        console.log(f"[yellow]dry-run[/] would process {file_path.name}")
        _log(log_f, "dry-run", str(file_path))
        state["files"][key] = {"mtime": file_path.stat().st_mtime, "status": "done"}
        _save_state(state_path, state)
        return

    dest = inbox.dest.resolve()
    quarantine = dest / "_quarantine"
    src_dir = file_path.parent

    try:
        if media_type == "video":
            if inbox.mode == "home-videos":
                from .home_videos import run_home_videos
                run_home_videos(settings, src_dir, dest, quarantine, dry_run=False, use_llm=True)
            else:
                from .organize import run_auto
                run_auto(settings, src_dir, dest, quarantine, dry_run=False)
            from .enrich import run_enrich
            run_enrich(settings, dest, force=False, use_whisper=True, no_rename=False)
        else:
            from .photos import run_photos
            run_photos(settings, src_dir, dest, quarantine, dry_run=False)
            from .enrich_photos import run_enrich_photos
            run_enrich_photos(settings, dest, force=False, do_faces=True)

        mtime = file_path.stat().st_mtime if file_path.exists() else 0
        state["files"][key] = {"mtime": mtime, "status": "done"}
        _log(log_f, "process-done", str(file_path))
        console.log(f"[green]done[/] {file_path.name}")
    except Exception as exc:
        mtime = file_path.stat().st_mtime if file_path.exists() else 0
        state["files"][key] = {"mtime": mtime, "status": "failed", "error": str(exc)}
        _log(log_f, "process-error", f"{file_path}: {exc}")
        console.log(f"[red]error processing {file_path.name}: {exc}[/]")
    _save_state(state_path, state)

# ── watchdog event handler ───────────────────────────────────────────────

def _make_handler(inbox: WatchInbox, queue: list):
    from watchdog.events import FileSystemEventHandler

    class _InboxHandler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                p = Path(event.src_path)
                if p.suffix.lower() in ALL_MEDIA_EXTS:
                    queue.append(p)
        def on_modified(self, event):
            if not event.is_directory:
                p = Path(event.src_path)
                if p.suffix.lower() in ALL_MEDIA_EXTS:
                    queue.append(p)
    return _InboxHandler()

# ── main daemon loop ─────────────────────────────────────────────────────

def run_watch(settings: Settings, dry_run: bool = False) -> None:
    """Start the watch daemon.  Blocks until Ctrl+C."""
    watch_cfg = settings.watch
    inboxes = watch_cfg.inboxes

    console.print(f"[bold cyan]watch[/] monitoring {len(inboxes)} inbox(es), "
                  f"poll_interval={watch_cfg.poll_interval}s")
    if dry_run:
        console.print("[yellow]DRY RUN -- no files will be processed.[/]")

    queue: list[Path] = []
    observers = []

    # Prefer native observer; import PollingObserver as fallback
    from watchdog.observers import Observer as _NativeObserver
    from watchdog.observers.polling import PollingObserver

    for inbox in inboxes:
        inbox_path = inbox.path.resolve()
        if not inbox_path.exists():
            console.print(f"[yellow]Creating inbox directory: {inbox_path}[/]")
            inbox_path.mkdir(parents=True, exist_ok=True)
        handler = _make_handler(inbox, queue)
        try:
            obs = _NativeObserver()
            obs.schedule(handler, str(inbox_path), recursive=True)
            obs.start()
            observers.append(obs)
            console.log(f"[green]watching[/] {inbox_path} (mode={inbox.mode})")
        except Exception:
            console.log(f"[yellow]native watch failed, polling {inbox_path}[/]")
            obs = PollingObserver(timeout=watch_cfg.poll_interval)
            obs.schedule(handler, str(inbox_path), recursive=True)
            obs.start()
            observers.append(obs)

    # Scan for files that arrived while the daemon was offline
    for inbox in inboxes:
        state_path = inbox.dest.resolve() / "watch-state.json"
        state = _load_state(state_path)
        for f in inbox.path.resolve().rglob("*"):
            if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTS:
                entry = state["files"].get(str(f))
                if not entry or entry.get("status") == "failed":
                    queue.append(f)

    # Graceful shutdown via signal
    shutdown = False
    def _on_signal(signum, frame):
        nonlocal shutdown
        shutdown = True
        console.print("\n[yellow]Shutting down (finishing current file)...[/]")
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    stamp = datetime.now().strftime("%Y-%m-%d")

    try:
        while not shutdown:
            # De-duplicate
            seen: set[str] = set()
            unique: list[Path] = []
            for p in queue:
                k = str(p)
                if k not in seen:
                    seen.add(k)
                    unique.append(p)
            queue.clear()

            for file_path in unique:
                if shutdown:
                    break
                # Match file to its inbox
                matched = None
                for inbox in inboxes:
                    try:
                        file_path.resolve().relative_to(inbox.path.resolve())
                        matched = inbox
                        break
                    except ValueError:
                        continue
                if not matched:
                    continue

                dest = matched.dest.resolve()
                dest.mkdir(parents=True, exist_ok=True)
                state_path = dest / "watch-state.json"
                state = _load_state(state_path)
                log_path = dest / f"watch-{stamp}.log"

                with open(log_path, "a", encoding="utf-8") as log_f:
                    if not file_path.exists():
                        continue
                    console.log(f"[dim]stabilizing {file_path.name}...[/]")
                    if not _is_stable(file_path):
                        queue.append(file_path)
                        _log(log_f, "unstable", str(file_path))
                        continue
                    _process_file(file_path, matched, settings, state,
                                  state_path, log_f, dry_run)

            if unique and not dry_run:
                _refresh_plex(watch_cfg.plex)

            if not shutdown:
                time.sleep(watch_cfg.poll_interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
    finally:
        for obs in observers:
            obs.stop()
        for obs in observers:
            obs.join(timeout=5)
        console.print("[bold cyan]watch[/] stopped.")
