from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from .config import Settings
from .extract import extract_audio_clip, extract_keyframes
from .ollama_client import OllamaClient
from .signals.base import Candidate, Signal, SignalContext
from .signals.filename import FilenameSignal
from .signals.transcript import TranscriptSignal, build_backend
from .signals.vision import VisionSignal
from .tmdb import TMDB, TMDBMatch


VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv",
    ".ts", ".mpg", ".mpeg", ".webm", ".flv", ".vob",
}

console = Console()


@dataclass
class Proposal:
    path: str
    current_name: str
    proposed_name: str
    confidence: float
    kind: str | None
    tmdb_id: int | None
    picked: dict | None
    candidates: list[dict]


def iter_videos(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def _vote(all_candidates: list[Candidate]) -> tuple[Candidate | None, float]:
    """Bucket candidates by normalized title, score by agreement + confidence."""
    if not all_candidates:
        return None, 0.0
    buckets: dict[str, list[Candidate]] = {}
    for c in all_candidates:
        key = c.title.lower().strip()
        if not key:
            continue
        buckets.setdefault(key, []).append(c)
    if not buckets:
        return None, 0.0

    def bucket_score(k: str) -> tuple[int, float]:
        group = buckets[k]
        return (len({c.source for c in group}), sum(c.confidence for c in group))

    best_key = max(buckets, key=bucket_score)
    group = buckets[best_key]
    distinct_sources = {c.source for c in group}
    mean_conf = sum(c.confidence for c in group) / len(group)
    score = min(1.0, mean_conf + 0.15 * (len(distinct_sources) - 1))
    pick = max(group, key=lambda c: (c.year is not None, c.season is not None, c.confidence))
    return pick, score


def _plex_name(c: Candidate, match: TMDBMatch | None, original: Path) -> str:
    title = (match.title if match else c.title).strip()
    year = match.year if match else c.year
    ext = original.suffix.lower()
    # strip characters Plex / Windows can't store in filenames
    safe_title = "".join(ch for ch in title if ch not in '<>:"/\\|?*').strip()
    if c.kind == "tv" and c.season is not None and c.episode is not None:
        return f"{safe_title} - s{c.season:02d}e{c.episode:02d}{ext}"
    if year:
        return f"{safe_title} ({year}){ext}"
    return f"{safe_title}{ext}"


class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ollama = OllamaClient(settings.ollama_url)
        self.tmdb = TMDB(settings.tmdb_api_key)
        self.signals: list[Signal] = []
        if settings.signals.filename:
            self.signals.append(FilenameSignal())
        if settings.signals.vision:
            self.signals.append(VisionSignal(self.ollama, settings.models.vision))
        if settings.signals.transcript:
            backend = build_backend(settings.whisper)
            if backend and backend.available():
                self.signals.append(TranscriptSignal(
                    self.ollama, settings.models.text, backend
                ))
            else:
                console.log(
                    f"[yellow]transcript signal enabled but backend "
                    f"'{settings.whisper.backend}' is unavailable — skipping[/]"
                )

    def close(self) -> None:
        self.ollama.close()
        self.tmdb.close()

    def identify_one(self, video: Path) -> Proposal:
        cache = self.settings.extract.cache_dir / video.stem
        frames: list[Path] = []
        audio: Path | None = None

        need_frames = any(s.name == "vision" for s in self.signals)
        need_audio = any(s.name in ("transcript", "fingerprint") for s in self.signals)

        if need_frames:
            try:
                frames = extract_keyframes(
                    video, cache / "frames", self.settings.extract.keyframe_count
                )
            except Exception as e:
                console.log(f"[yellow]keyframe extract failed for {video.name}: {e}[/]")

        if need_audio:
            try:
                audio = extract_audio_clip(
                    video, cache / "audio.wav", self.settings.extract.audio_seconds
                )
            except Exception as e:
                console.log(f"[yellow]audio extract failed for {video.name}: {e}[/]")

        ctx = SignalContext(video=video, keyframes=frames, audio_clip=audio)

        all_candidates: list[Candidate] = []
        for sig in self.signals:
            try:
                all_candidates.extend(sig.run(ctx))
            except Exception as e:
                console.log(f"[yellow]{sig.name} failed on {video.name}: {e}[/]")

        pick, score = _vote(all_candidates)

        match: TMDBMatch | None = None
        if pick and self.settings.tmdb_api_key:
            try:
                results = self.tmdb.search_multi(pick.title, year=pick.year)
                if results:
                    match = max(results, key=lambda m: m.popularity)
                    if match.title.lower() == pick.title.lower():
                        score = min(1.0, score + 0.05)
            except Exception as e:
                console.log(f"[yellow]tmdb lookup failed for '{pick.title}': {e}[/]")

        proposed = _plex_name(pick, match, video) if pick else video.name
        return Proposal(
            path=str(video),
            current_name=video.name,
            proposed_name=proposed,
            confidence=round(score, 3),
            kind=(pick.kind if pick else None),
            tmdb_id=(match.id if match else None),
            picked=asdict(pick) if pick else None,
            candidates=[asdict(c) for c in all_candidates],
        )

    def scan(self, root: Path) -> list[Proposal]:
        videos = iter_videos(root)
        proposals: list[Proposal] = []
        if not videos:
            console.print(f"[yellow]No video files found under[/] {root}")
            return proposals
        columns = [
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        ]
        with Progress(*columns, console=console) as progress:
            task = progress.add_task("identifying", total=len(videos))
            for v in videos:
                progress.update(task, description=v.name[:60])
                proposals.append(self.identify_one(v))
                progress.advance(task)
        return proposals


def write_queue(proposals: list[Proposal], queue_path: Path, review_floor: float) -> int:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with queue_path.open("w", encoding="utf-8") as f:
        for p in proposals:
            if p.confidence < review_floor:
                continue
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
            n += 1
    return n


def apply_queue(queue_path: Path, auto_apply: float, dry_run: bool = False) -> tuple[int, int, int]:
    applied = skipped = failed = 0
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        if p["confidence"] < auto_apply:
            skipped += 1
            continue
        src = Path(p["path"])
        if not src.exists():
            console.print(f"[yellow]GONE[/] {src.name}")
            skipped += 1
            continue
        dst = src.with_name(p["proposed_name"])
        if dst == src:
            skipped += 1
            continue
        if dst.exists():
            console.print(f"[yellow]EXISTS[/] {dst.name} — refusing to overwrite")
            skipped += 1
            continue
        if dry_run:
            console.print(f"[cyan]DRY[/]  {src.name}  ->  {dst.name}  ({p['confidence']:.2f})")
            applied += 1
            continue
        try:
            src.rename(dst)
            console.print(f"[green]OK[/]   {src.name}  ->  {dst.name}  ({p['confidence']:.2f})")
            applied += 1
        except OSError as e:
            console.print(f"[red]FAIL[/] {src.name}: {e}")
            failed += 1
    return applied, skipped, failed
