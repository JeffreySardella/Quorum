"""Home-video organizer — the family-archive mode.

Assumes the folder structure you already have for home videos: one folder per
event/disc, with a descriptive folder name that usually contains a year and a
rough description (e.g. "2005 sophia 4th bd, fishing derby, sea world").

Strategy: DON'T try to identify content against any database. Trust the folder
name. Use the text LLM to parse it into (year, month, description), fall back
to regex, cross-check against the filesystem mtime of the videos inside. Then
move everything under `<dest>/Home Videos/YYYY/YYYY-MM - <description>/`.

This mode is cheap: one LLM call per folder (not per file), no Whisper, no
vision. Runs in minutes for hundreds of folders.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .ollama_client import OllamaClient
from .pipeline import VIDEO_EXTS


console = Console()


# ── folder-name parsing ───────────────────────────────────────────────────
PROMPT = """You are parsing the name of a folder containing home-video files.

The folder name often contains a year (sometimes just "05" for 2005, sometimes
a full "2005"), sometimes a month (full name or short form), and a list of
events separated by commas.

Return ONLY a JSON object with this exact schema:

{
  "year": 2005,
  "month": 4,
  "day": null,
  "description": "a short clean title for the folder",
  "confidence": 0.0
}

Rules:
- `year` MUST be 4 digits. A 2-digit prefix like "09" means 2009 (2000s era).
- `month` is 1–12 or null if not present. Month names: jan=1, feb=2, mar=3,
  apr=4, may=5, jun=6, jul=7, aug=8, sep=9, oct=10, nov=11, dec=12.
- `day` is 1–31 or null.
- `description` should be a readable title. Fix common typos gently (valtines
  -> valentines, caluse -> clause, febuary -> february). Capitalize proper
  names (sophia, jeffrey, nana). Keep it under 80 chars. Strip leading numbers
  that are just dates.
- If you can't parse a year with reasonable confidence, set year to null and
  confidence <= 0.3.
- Return ONLY the JSON object, no other text.

Folder name:
\"\"\"{name}\"\"\"
"""


_YEAR_4 = re.compile(r"\b(19|20)\d{2}\b")
# 2-digit year fallback: must be at the start of the string or preceded by
# whitespace, and followed by whitespace / end / letter. This rejects "#26",
# "v12", "ep03", and other non-year 2-digit numbers embedded in text.
_YEAR_2 = re.compile(r"(?:^|\s)(\d{2})(?=\s|$|[a-z])")

# Filename-level date patterns. These catch phone-timestamp files
# (20160820_115414.mp4), slash/dash dates (2010-04-15), and space-separated
# dates (2010 4 15 10). Tried in order; first hit wins.
_FILENAME_DATE = [
    # YYYYMMDD_HHMMSS or YYYYMMDDTHHMMSS (Samsung / most Android)
    re.compile(r"(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})[_T]\d{6}"),
    # YYYY-MM-DD, YYYY_MM_DD, YYYY.MM.DD
    re.compile(r"\b(?P<y>(?:19|20)\d{2})[-_.](?P<m>\d{1,2})[-_.](?P<d>\d{1,2})\b"),
    # YYYY MM DD (space-separated, 1-2 digit month/day)
    re.compile(r"\b(?P<y>(?:19|20)\d{2})\s+(?P<m>\d{1,2})\s+(?P<d>\d{1,2})(?:\s|$)"),
    # Standalone YYYYMMDD
    re.compile(r"\b(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})\b"),
    # YYYY only
    re.compile(r"\b(?P<y>(?:19|20)\d{2})\b"),
]
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "febuary": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_INVALID = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    s = "".join(ch if ch not in _INVALID else " " for ch in s)
    return " ".join(s.split()).strip().strip(".")[:80]


def _regex_parse(name: str) -> dict:
    lower = name.lower()
    year: int | None = None
    m = _YEAR_4.search(lower)
    if m:
        year = int(m.group(0))
    else:
        # Two-digit fallback: "03 easter" -> 2003, "97" -> 1997
        m2 = _YEAR_2.search(lower)
        if m2:
            y2 = int(m2.group(1))
            year = 2000 + y2 if y2 < 50 else 1900 + y2
    month: int | None = None
    for name_key, idx in _MONTHS.items():
        if re.search(rf"\b{name_key}\b", lower):
            month = idx
            break
    return {"year": year, "month": month, "day": None, "description": name, "confidence": 0.45 if year else 0.1}


def _parse_filename_date(stem: str) -> dict | None:
    """Extract year/month/day from a video filename. Returns None if no year."""
    for pat in _FILENAME_DATE:
        m = pat.search(stem)
        if not m:
            continue
        gd = m.groupdict()
        try:
            year = int(gd["y"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (1990 <= year <= datetime.now().year):
            continue
        month: int | None = None
        if gd.get("m"):
            try:
                mv = int(gd["m"])
                if 1 <= mv <= 12:
                    month = mv
            except ValueError:
                pass
        day: int | None = None
        if gd.get("d"):
            try:
                dv = int(gd["d"])
                if 1 <= dv <= 31:
                    day = dv
            except ValueError:
                pass
        if month and day:
            conf = 0.7
        elif month:
            conf = 0.55
        else:
            conf = 0.4
        return {
            "year": year,
            "month": month,
            "day": day,
            "description": stem,
            "confidence": conf,
            "source": "filename",
        }
    return None


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def parse_folder_name(name: str, client: OllamaClient | None, text_model: str) -> dict:
    """Return {year, month, day, description, confidence, source}."""
    base = _regex_parse(name)
    if client is None:
        return {**base, "source": "regex"}
    # NOTE: we use .replace, NOT .format — the PROMPT contains literal `{` and
    # `}` from the JSON schema example which would break .format().
    try:
        raw = client.generate(text_model, PROMPT.replace("{name}", name))
    except Exception as e:
        console.log(f"[yellow]LLM call failed for {name!r}: {type(e).__name__}: {e}[/]")
        return {**base, "source": "regex"}
    data = _parse_json(raw)
    if not data:
        return {**base, "source": "regex"}
    # Sanitize LLM output
    year = data.get("year")
    if not isinstance(year, int) or year < 1900 or year > 2100:
        year = base["year"]
    month = data.get("month")
    if not isinstance(month, int) or not 1 <= month <= 12:
        month = base["month"]
    day = data.get("day")
    if not isinstance(day, int) or not 1 <= day <= 31:
        day = None
    desc = data.get("description") or name
    if not isinstance(desc, str):
        desc = name
    try:
        conf = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "year": year,
        "month": month,
        "day": day,
        "description": _safe(desc),
        "confidence": max(0.0, min(1.0, conf)),
        "source": "llm",
    }


# ── destination ───────────────────────────────────────────────────────────
def home_video_path(dest_root: Path, parsed: dict, filename: str) -> Path | None:
    year = parsed.get("year")
    if not isinstance(year, int):
        return None
    desc = _safe(parsed.get("description") or "")
    month = parsed.get("month")
    day = parsed.get("day")
    if isinstance(month, int) and isinstance(day, int):
        prefix = f"{year:04d}-{month:02d}-{day:02d}"
    elif isinstance(month, int):
        prefix = f"{year:04d}-{month:02d}"
    else:
        prefix = f"{year:04d}"
    subdir = f"{prefix} - {desc}" if desc else prefix
    return dest_root / "Home Videos" / f"{year:04d}" / subdir / filename


def _file_year(p: Path) -> int | None:
    try:
        mt = datetime.fromtimestamp(p.stat().st_mtime)
        return mt.year if 1990 <= mt.year <= datetime.now().year else None
    except OSError:
        return None


# ── run ───────────────────────────────────────────────────────────────────
@dataclass
class HomeSummary:
    total_folders: int = 0
    total_files: int = 0
    moved: int = 0
    quarantined_unparsed: int = 0
    skipped_collision: int = 0
    failed: int = 0
    folders_per_year: dict = field(default_factory=dict)


def _iter_event_folders(src: Path) -> list[Path]:
    """Every subdirectory that directly contains video files. Also yield src
    itself if it contains loose video files."""
    roots: list[Path] = []
    # loose videos at the root — treat src itself as an event folder if it has any
    if any(
        p.is_file() and p.suffix.lower() in VIDEO_EXTS
        for p in src.iterdir()
    ):
        roots.append(src)
    for d in sorted(p for p in src.rglob("*") if p.is_dir()):
        try:
            has_video = any(
                p.is_file() and p.suffix.lower() in VIDEO_EXTS
                for p in d.iterdir()
            )
        except OSError:
            continue
        if has_video:
            roots.append(d)
    return roots


def run_home_videos(
    settings: Settings,
    src: Path,
    dest: Path,
    quarantine: Path,
    dry_run: bool = False,
    use_llm: bool = True,
) -> tuple[HomeSummary, Path]:
    """Organize home-video folders under `src` into `dest/Home Videos/YYYY/...`.

    If `use_llm` is True, calls the configured text model (e.g. gemma4:31b) to
    parse each folder name. Set it False to run purely on regex — much faster,
    less accurate on edge cases.
    """
    # Refuse accidental overlap
    src_resolved = src.resolve()
    dest_resolved = dest.resolve()
    if dest_resolved == src_resolved or str(dest_resolved).startswith(str(src_resolved) + os.sep):
        raise ValueError("dest is inside src — refusing to run (would recursively re-process)")

    dest.mkdir(parents=True, exist_ok=True)
    quarantine.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = dest / f"home-videos-{stamp}.log"

    client = OllamaClient(settings.ollama_url) if use_llm else None
    summary = HomeSummary()

    folders = _iter_event_folders(src)
    summary.total_folders = len(folders)
    if not folders:
        console.print(f"[yellow]No folders with video files under[/] {src}")
        log_path.touch()
        return summary, log_path

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    try:
        with log_path.open("w", encoding="utf-8") as log_f, Progress(*columns, console=console) as progress:
            task = progress.add_task("home-videos", total=len(folders))
            for folder in folders:
                progress.update(task, description=folder.name[:60])
                _process_folder(
                    folder, src, dest, quarantine, settings,
                    client, dry_run, summary, log_f,
                )
                progress.advance(task)
    finally:
        if client is not None:
            client.close()

    return summary, log_path


def _resolve_parse(folder_parsed: dict | None, file_parsed: dict | None, stem: str) -> dict:
    """Combine folder-level + filename-level parses into one result per file.

    Priority: folder year wins (usually right); filename month/day fills in
    when the folder has only a year; filename-only is used when the folder
    has no year at all (e.g. loose files at the src root).
    """
    if folder_parsed and isinstance(folder_parsed.get("year"), int):
        out = dict(folder_parsed)
        if file_parsed:
            if out.get("month") is None and file_parsed.get("month"):
                out["month"] = file_parsed["month"]
            if out.get("day") is None and file_parsed.get("day"):
                out["day"] = file_parsed["day"]
        return out
    if file_parsed:
        return file_parsed
    return folder_parsed or {
        "year": None, "month": None, "day": None,
        "description": stem, "confidence": 0.0, "source": "none",
    }


def _process_folder(
    folder: Path,
    src_root: Path,
    dest: Path,
    quarantine: Path,
    settings: Settings,
    client: OllamaClient | None,
    dry_run: bool,
    summary: HomeSummary,
    log_f,
) -> None:
    videos = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    summary.total_files += len(videos)

    is_root = (folder == src_root)
    folder_parsed: dict | None = None
    if not is_root:
        folder_parsed = parse_folder_name(folder.name, client, settings.models.text)

    for v in videos:
        file_parsed = _parse_filename_date(v.stem)
        parsed = _resolve_parse(folder_parsed, file_parsed, v.stem)

        # mtime cross-check per-file
        year = parsed.get("year")
        file_mtime_year = _file_year(v)
        if isinstance(year, int) and file_mtime_year and year != file_mtime_year:
            parsed = dict(parsed)
            parsed["confidence"] = min(parsed.get("confidence", 0.0), 0.5)
            parsed["mtime_warning"] = file_mtime_year

        summary.folders_per_year[year] = summary.folders_per_year.get(year, 0) + 1

        if not isinstance(year, int):
            _quarantine_file(v, parsed, quarantine, dry_run, summary, log_f, reason="no_year")
            continue

        dst = home_video_path(dest, parsed, v.name)
        if dst is None:
            _quarantine_file(v, parsed, quarantine, dry_run, summary, log_f, reason="no_year")
            continue
        if dst.exists():
            summary.skipped_collision += 1
            log_f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "src": str(v), "dst": str(dst), "action": "skip_collision",
            }, ensure_ascii=False) + "\n")
            log_f.flush()
            continue
        if not dry_run:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(v), str(dst))
            except OSError as e:
                summary.failed += 1
                log_f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "src": str(v), "dst": str(dst), "action": "fail", "error": str(e),
                }, ensure_ascii=False) + "\n")
                log_f.flush()
                continue
        summary.moved += 1
        log_f.write(json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "src": str(v), "dst": str(dst), "action": "move",
            "parsed": parsed,
        }, ensure_ascii=False) + "\n")
        log_f.flush()


def _quarantine_file(
    video: Path,
    parsed: dict,
    quarantine: Path,
    dry_run: bool,
    summary: HomeSummary,
    log_f,
    reason: str,
) -> None:
    q_dst = quarantine / video.parent.name / video.name
    if not dry_run:
        try:
            q_dst.parent.mkdir(parents=True, exist_ok=True)
            if q_dst.exists():
                q_dst = q_dst.with_stem(q_dst.stem + "." + datetime.now().strftime("%H%M%S"))
            shutil.move(str(video), str(q_dst))
            sidecar = q_dst.with_suffix(q_dst.suffix + ".quorum.json")
            sidecar.write_text(
                json.dumps({"reason": reason, "parsed": parsed}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            summary.failed += 1
            log_f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "src": str(video), "dst": str(q_dst), "action": "fail", "error": str(e),
            }) + "\n")
            log_f.flush()
            return
    summary.quarantined_unparsed += 1
    log_f.write(json.dumps({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "src": str(video), "dst": str(q_dst), "action": f"quarantine_{reason}",
        "parsed": parsed,
    }) + "\n")
    log_f.flush()


def print_summary(summary: HomeSummary, log_path: Path, dry_run: bool) -> None:
    title = "Home videos run"
    if dry_run:
        title += " (DRY RUN — nothing moved)"
    t = Table(title=title)
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("folders seen", str(summary.total_folders))
    t.add_row("video files seen", str(summary.total_files))
    t.add_row("[green]moved into Year/Event layout[/]", str(summary.moved))
    t.add_row("[yellow]quarantined (no year parsed)[/]", str(summary.quarantined_unparsed))
    t.add_row("[dim]skipped (destination already exists)[/]", str(summary.skipped_collision))
    t.add_row("[red]failed[/]", str(summary.failed))
    console.print(t)

    if summary.folders_per_year:
        yt = Table(title="Folders per year")
        yt.add_column("year")
        yt.add_column("folders", justify="right")
        for y in sorted(summary.folders_per_year, key=lambda x: (x is None, x)):
            yt.add_row(str(y) if y is not None else "[red]unknown[/]", str(summary.folders_per_year[y]))
        console.print(yt)

    console.print(f"Log: [bold]{log_path}[/]")
    console.print(f"Undo: [dim]quorum undo {log_path}[/]")
