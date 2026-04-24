"""Content-based enrichment — watches each organized video and generates
Plex-compatible metadata sidecars (.nfo files).

This runs AFTER home-videos mode has organized files into a Year/Event layout.
For each video it:
  1. Extracts keyframes + audio clip (via existing extract.py)
  2. Asks the vision LLM to describe what's in the frames
  3. Asks Whisper to transcribe the audio
  4. Asks the text LLM to synthesize a title + description from all three
     signals plus the parent-folder name hint
  5. Writes <video>.nfo next to the video for Plex to pick up
  6. Flags videos whose content strongly disagrees with the folder name
     into a separate "likely mislabeled" review log

Designed to be safe to run repeatedly: skips videos that already have a .nfo
unless --force is passed. Cache frames + audio in .quorum-cache so a re-run
doesn't re-extract.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .extract import extract_audio_clip, extract_keyframes
from .ollama_client import OllamaClient
from .pipeline import VIDEO_EXTS
from .signals.transcript import TranscriptBackend, build_backend


console = Console()


# ── prompts ───────────────────────────────────────────────────────────────
VISION_PROMPT = """You are describing a short home-video clip from its keyframes.

Look at all the keyframes together and return ONLY a JSON object:

{
  "people_visible": ["short neutral descriptions of each distinct person you see, e.g. 'young girl in red dress', 'older man with glasses'"],
  "setting": "where this appears to be (house interior, backyard, beach, restaurant, theme park, etc.)",
  "activity": "what's happening (opening presents, blowing out candles, playing sports, eating dinner, etc.)",
  "visual_notes": "specific objects / decorations / costumes / pets / text visible on screen. Focus on things that uniquely identify the event.",
  "approximate_era": "rough visual era based on video quality, clothing, tech visible (e.g. 'early 2000s camcorder', 'modern smartphone', '1990s VHS')"
}

Be concrete and observational. DO NOT invent names of people — just describe what you see.
If keyframes are dark or uninformative, say so in visual_notes and be brief.
Return ONLY the JSON object. No prose, no code fences."""


SYNTHESIS_PROMPT = """You are generating Plex library metadata for one home-video clip.

You have THREE sources of information. Synthesize them into a clean title + description.

1) Parent folder name (the librarian's rough label):
{folder_name}

2) Filename:
{filename}

3) Visual description from keyframes:
{vision}

4) Audio transcript (may be partial, noisy, or empty):
\"\"\"{transcript}\"\"\"

Return ONLY a JSON object with this schema:

{
  "title": "concise, specific title for this clip, under 60 chars",
  "description": "1-2 sentences describing what actually happens in THIS clip, under 250 chars",
  "matches_folder_hint": true,
  "confidence": 0.9,
  "reasoning": "one short sentence explaining the title/description choice"
}

Rules:
- `title`: distill the specific event. Prefer "Sophia's 4th Birthday" over generic "Birthday Party". Use proper names ONLY if they appear in the folder name or transcript (don't invent them).
- `description`: what actually happens in this clip based on visual + audio evidence. Concrete beats generic.
- `matches_folder_hint`: set FALSE only if the content strongly contradicts the folder name (e.g. folder says "Christmas 2005" but frames clearly show a beach in summer). Minor mismatches (folder says "birthday & fishing" but clip shows only birthday) are NOT a contradiction — set TRUE.
- If content is ambiguous, default to trusting the folder name.
- Return ONLY the JSON object."""


# ── result types ──────────────────────────────────────────────────────────
@dataclass
class EnrichResult:
    title: str
    description: str
    matches_folder_hint: bool
    confidence: float
    reasoning: str
    vision: dict
    transcript_snippet: str
    music_tags: list[str] = field(default_factory=list)


@dataclass
class EnrichSummary:
    total: int = 0
    enriched: int = 0
    failed: int = 0
    mislabel_flags: int = 0
    skipped_existing: int = 0


# ── helpers ───────────────────────────────────────────────────────────────
def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


_INVALID = set('<>:"/\\|?*')


def _clean_text(s: str, limit: int) -> str:
    s = "".join(ch for ch in s if ch.isprintable())
    return s.strip()[:limit]


def _extract_year_from_name(name: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", name)
    return int(m.group(0)) if m else None


def _write_nfo(video: Path, result: EnrichResult, year: int | None, music_tags: list[str] | None = None) -> Path:
    """Write a Plex-compatible .nfo sidecar next to the video."""
    nfo_path = video.with_suffix(".nfo")
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = result.title
    ET.SubElement(root, "plot").text = result.description
    if year:
        ET.SubElement(root, "year").text = str(year)
    ET.SubElement(root, "genre").text = "Home Video"
    ET.SubElement(root, "tag").text = "quorum-enriched"
    if music_tags:
        for tag in music_tags:
            ET.SubElement(root, "tag").text = f"music:{tag}"
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
    return nfo_path


# ── per-video enrichment ──────────────────────────────────────────────────
def enrich_one(
    video: Path,
    settings: Settings,
    ollama: OllamaClient,
    transcript_backend: TranscriptBackend | None,
    use_whisper: bool = True,
) -> EnrichResult:
    cache = settings.extract.cache_dir / video.stem

    # 1) keyframes for vision
    frames = []
    try:
        frames = extract_keyframes(video, cache / "frames", settings.extract.keyframe_count)
    except Exception as e:
        console.log(f"[yellow]keyframe extract failed for {video.name}: {e}[/]")

    # 2) audio for transcription
    audio = None
    try:
        audio = extract_audio_clip(video, cache / "audio.wav", settings.extract.audio_seconds)
    except Exception as e:
        console.log(f"[yellow]audio extract failed for {video.name}: {e}[/]")

    # 3) vision description
    vision_data: dict = {}
    if frames:
        try:
            raw = ollama.generate(settings.models.vision, VISION_PROMPT, images=frames)
            vision_data = _parse_json(raw) or {}
        except Exception as e:
            console.log(f"[yellow]vision LLM failed for {video.name}: {e}[/]")

    # 4) transcript (optional — skipping it cuts ~10-15s/video)
    transcript_text = ""
    if use_whisper and audio and transcript_backend and transcript_backend.available():
        try:
            transcript_text = transcript_backend.transcribe(audio)
        except Exception as e:
            console.log(f"[yellow]whisper failed for {video.name}: {e}[/]")

    # 4b) audio fingerprint for music tagging
    music_tags: list[str] = []
    if audio and settings.signals.fingerprint:
        import os
        api_key = os.environ.get("ACOUSTID_API_KEY", "")
        if api_key:
            try:
                import acoustid
                duration, fingerprint = acoustid.fingerprint_file(str(audio))
                results = acoustid.lookup(api_key, fingerprint, duration)
                for score, rec_id, title, artist in acoustid.parse_lookup_result(results):
                    if score >= 0.8 and title:
                        label = f"{title} - {artist}" if artist else title
                        music_tags.append(label)
            except Exception as e:
                console.log(f"[yellow]fingerprint failed for {video.name}: {e}[/]")

    # 5) synthesis
    prompt = (
        SYNTHESIS_PROMPT
        .replace("{folder_name}", video.parent.name)
        .replace("{filename}", video.name)
        .replace("{vision}", json.dumps(vision_data, ensure_ascii=False))
        .replace("{transcript}", transcript_text[:1500])
    )
    # Use the vision model for synthesis too when it's multimodal (e.g. mistral-small3.2).
    # Eliminates the vision<->text model swap, roughly halving per-video latency.
    # If the user has a dedicated text-only model they prefer for synthesis, they can
    # force it by setting models.text to something different AND flipping this flag off
    # (env: QUORUM_SEPARATE_SYNTHESIS=1).
    import os as _os
    synthesis_model = (
        settings.models.text
        if _os.environ.get("QUORUM_SEPARATE_SYNTHESIS") == "1"
        else settings.models.vision
    )
    try:
        raw = ollama.generate(synthesis_model, prompt)
    except Exception as e:
        raise RuntimeError(f"synthesis LLM call failed: {e}") from e
    synth = _parse_json(raw) or {}

    # Sane defaults when synthesis JSON is missing/partial
    title = _clean_text(str(synth.get("title") or video.parent.name), 60) or video.parent.name
    description = _clean_text(str(synth.get("description") or ""), 250)
    matches = synth.get("matches_folder_hint")
    matches_bool = True if matches is None else bool(matches)
    try:
        conf = max(0.0, min(1.0, float(synth.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    reasoning = _clean_text(str(synth.get("reasoning") or ""), 200)

    return EnrichResult(
        title=title,
        description=description,
        matches_folder_hint=matches_bool,
        confidence=conf,
        reasoning=reasoning,
        vision=vision_data,
        transcript_snippet=transcript_text[:400],
        music_tags=music_tags,
    )


# ── walker ────────────────────────────────────────────────────────────────
def _iter_enrichable(root: Path) -> list[Path]:
    target = root / "Home Videos"
    if not target.exists():
        target = root  # allow enriching any dir
    return sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def run_enrich(
    settings: Settings,
    root: Path,
    force: bool = False,
    use_whisper: bool = True,
    no_rename: bool = False,
) -> tuple[EnrichSummary, Path, Path]:
    videos = _iter_enrichable(root)
    summary = EnrichSummary(total=len(videos))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = root / f"enrich-{stamp}.log"
    mislabel_path = root / f"enrich-mislabels-{stamp}.log"

    if not videos:
        console.print(f"[yellow]No videos found under[/] {root}")
        log_path.touch()
        return summary, log_path, mislabel_path

    ollama = OllamaClient(settings.ollama_url)
    backend = build_backend(settings.whisper)

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    try:
        with log_path.open("w", encoding="utf-8") as log_f, \
             mislabel_path.open("w", encoding="utf-8") as ml_f, \
             Progress(*columns, console=console) as progress:
            task = progress.add_task("enrich", total=len(videos))
            for v in videos:
                progress.update(task, description=v.name[:60])

                # Skip if .nfo already exists (resume-friendly)
                nfo = v.with_suffix(".nfo")
                if nfo.exists() and not force:
                    summary.skipped_existing += 1
                    progress.advance(task)
                    continue

                try:
                    result = enrich_one(v, settings, ollama, backend, use_whisper=use_whisper)
                    year = _extract_year_from_name(v.parent.name) or _extract_year_from_name(
                        v.parent.parent.name
                    )
                    _write_nfo(v, result, year, music_tags=result.music_tags)
                    summary.enriched += 1
                    entry = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "video": str(v),
                        "title": result.title,
                        "description": result.description,
                        "matches_folder_hint": result.matches_folder_hint,
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                    }
                    log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log_f.flush()

                    if not result.matches_folder_hint:
                        summary.mislabel_flags += 1
                        ml_f.write(json.dumps({
                            "video": str(v),
                            "folder": v.parent.name,
                            "title": result.title,
                            "description": result.description,
                            "reasoning": result.reasoning,
                            "vision_notes": result.vision.get("visual_notes", ""),
                        }, ensure_ascii=False) + "\n")
                        ml_f.flush()

                except Exception as e:
                    summary.failed += 1
                    log_f.write(json.dumps({
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "video": str(v),
                        "error": f"{type(e).__name__}: {e}"[:500],
                    }, ensure_ascii=False) + "\n")
                    log_f.flush()

                progress.advance(task)
    finally:
        ollama.close()

    # Auto-trigger folder rename on fully enriched folders (unless disabled).
    # Append rename log entries to the enrich log so `quorum undo` reverses both.
    if not no_rename:
        from .rename_folders import run_rename_folders

        console.print("[cyan]Running automatic folder rename pass...[/]")
        with log_path.open("a", encoding="utf-8") as log_f:
            rename_summary, _ = run_rename_folders(
                settings, root, dry_run=False, log_file=log_f,
            )
        if rename_summary.renamed:
            console.print(f"[green]Renamed {rename_summary.renamed} folder(s)[/]")

    return summary, log_path, mislabel_path


def print_summary(summary: EnrichSummary, log_path: Path, mislabel_path: Path) -> None:
    t = Table(title="Quorum enrich")
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("videos seen", str(summary.total))
    t.add_row("[green]enriched with .nfo[/]", str(summary.enriched))
    t.add_row("[yellow]flagged as possibly mislabeled[/]", str(summary.mislabel_flags))
    t.add_row("[dim]skipped (.nfo already existed)[/]", str(summary.skipped_existing))
    t.add_row("[red]failed[/]", str(summary.failed))
    console.print(t)
    console.print(f"Log: [bold]{log_path}[/]")
    if summary.mislabel_flags:
        console.print(f"Mislabel review: [bold yellow]{mislabel_path}[/]")
