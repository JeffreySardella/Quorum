"""File-level classifier — splits a folder of mixed video content into
home-video files vs commercial movie/TV titles.

Designed for the VhsTapes/ case where you have personal camcorder dumps
(`04 easter, jeff 4th, tina college.mp4`) shelved right next to ripped
movies (`101 Dalmatians.mp4`). Running either organizer blindly over that
folder would mis-handle one side.

Output: two plain text manifests (one path per line) plus a JSONL log with
reasoning. Nothing moves. You then run:

    quorum home-videos <dir containing only home-list files>
    quorum auto <dir containing only commercial-list files>

or use the manifests in any other workflow.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .ollama_client import OllamaClient
from .pipeline import VIDEO_EXTS


console = Console()


# ── prompt ────────────────────────────────────────────────────────────────
PROMPT = """You are classifying a single video filename as HOME or COMMERCIAL.

HOME = personal / family content. Signs: descriptive lists of events,
  family names (Sophia, Jeffrey, Nana, etc.), year-prefixed event dumps
  ("05 xmas", "2007 soccer"), trip names without a studio, camcorder dump
  names, phone timestamps like "20160820_115414".

COMMERCIAL = a movie, TV episode, documentary, or other released title.
  Signs: a proper movie/show title you recognize, release-group suffixes
  (x264, YIFY, BluRay), "S01E05" / "1x05" patterns, release years in
  parentheses "Film Name (2015)".

Filename: {filename}

Return ONLY a JSON object with this schema:

{
  "kind": "home",
  "confidence": 0.85,
  "reasoning": "one short sentence"
}

Rules:
- kind MUST be exactly "home" or "commercial".
- If truly ambiguous, pick the side you lean toward and lower confidence.
- Return ONLY the JSON object. No prose."""


# ── data ──────────────────────────────────────────────────────────────────
Kind = Literal["home", "commercial", "unknown"]


@dataclass
class TriageResult:
    path: str
    kind: Kind
    confidence: float
    reasoning: str


@dataclass
class TriageSummary:
    total: int = 0
    home: int = 0
    commercial: int = 0
    unknown: int = 0
    low_confidence: int = 0  # cases with confidence < 0.5


# ── helpers ───────────────────────────────────────────────────────────────
def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def classify_filename(client: OllamaClient, model: str, filename: str) -> tuple[Kind, float, str]:
    try:
        raw = client.generate(model, PROMPT.replace("{filename}", filename))
    except Exception as e:
        return "unknown", 0.0, f"LLM call failed: {type(e).__name__}"

    data = _parse_json(raw)
    if not data:
        return "unknown", 0.0, "no JSON in response"

    kind_raw = str(data.get("kind") or "").strip().lower()
    if kind_raw not in ("home", "commercial"):
        return "unknown", 0.0, f"unexpected kind: {kind_raw!r}"

    try:
        conf = max(0.0, min(1.0, float(data.get("confidence") or 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    reasoning = str(data.get("reasoning") or "").strip()[:200]
    return kind_raw, conf, reasoning  # type: ignore[return-value]


# ── run ──────────────────────────────────────────────────────────────────
def run_triage(settings: Settings, src: Path) -> tuple[TriageSummary, Path, Path, Path, Path]:
    """Walk `src` for video files, classify each filename.

    Writes three files in the parent of `src`:
      triage-<stamp>.log               JSONL with full reasoning per file
      triage-home-<stamp>.txt          one absolute path per line
      triage-commercial-<stamp>.txt    one absolute path per line

    Returns (summary, log_path, home_list, commercial_list, unknown_list).
    """
    videos = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    summary = TriageSummary(total=len(videos))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = src.parent
    log_path = out_dir / f"triage-{stamp}.log"
    home_list = out_dir / f"triage-home-{stamp}.txt"
    commercial_list = out_dir / f"triage-commercial-{stamp}.txt"
    unknown_list = out_dir / f"triage-unknown-{stamp}.txt"

    if not videos:
        console.print(f"[yellow]No videos under[/] {src}")
        log_path.touch()
        return summary, log_path, home_list, commercial_list, unknown_list

    client = OllamaClient(settings.ollama_url)
    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    try:
        with (
            log_path.open("w", encoding="utf-8") as log_f,
            home_list.open("w", encoding="utf-8") as h_f,
            commercial_list.open("w", encoding="utf-8") as c_f,
            unknown_list.open("w", encoding="utf-8") as u_f,
            Progress(*columns, console=console) as progress,
        ):
            task = progress.add_task("triage", total=len(videos))
            for v in videos:
                progress.update(task, description=v.name[:60])
                kind, conf, reasoning = classify_filename(
                    client, settings.models.text, v.name
                )
                result = TriageResult(
                    path=str(v), kind=kind, confidence=conf, reasoning=reasoning
                )
                log_f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    **asdict(result),
                }, ensure_ascii=False) + "\n")
                log_f.flush()

                if kind == "home":
                    summary.home += 1
                    h_f.write(f"{v}\n"); h_f.flush()
                elif kind == "commercial":
                    summary.commercial += 1
                    c_f.write(f"{v}\n"); c_f.flush()
                else:
                    summary.unknown += 1
                    u_f.write(f"{v}\n"); u_f.flush()

                if conf < 0.5 and kind != "unknown":
                    summary.low_confidence += 1

                progress.advance(task)
    finally:
        client.close()

    return summary, log_path, home_list, commercial_list, unknown_list


def print_summary(
    summary: TriageSummary,
    log_path: Path,
    home_list: Path,
    commercial_list: Path,
    unknown_list: Path,
) -> None:
    t = Table(title="Quorum triage")
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("files seen", str(summary.total))
    t.add_row("[green]home (family / personal)[/]", str(summary.home))
    t.add_row("[cyan]commercial (movies / TV)[/]", str(summary.commercial))
    t.add_row("[yellow]unknown / ambiguous[/]", str(summary.unknown))
    t.add_row("[dim]low-confidence (< 0.5)[/]", str(summary.low_confidence))
    console.print(t)
    console.print(f"Log (full reasoning): [bold]{log_path}[/]")
    console.print(f"Home manifest:        [bold]{home_list}[/]")
    console.print(f"Commercial manifest:  [bold]{commercial_list}[/]")
    if summary.unknown:
        console.print(f"Unknown manifest:     [bold yellow]{unknown_list}[/]")
