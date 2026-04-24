"""Photos mode — organize image files by capture date.

Walks every image under `src`, reads the EXIF `DateTimeOriginal` (falling back
to filename date patterns, then filesystem mtime), and moves each photo into
`<dest>/Photos/YYYY/YYYY-MM-DD/<filename>`.

Hard skip for Aperture library internals: any file whose path includes an
`.aplibrary` / `.apdata` package, or whose extension is Aperture-managed
(`.apversion`, `.apmaster`, etc.). Touching those would corrupt the library.

Designed to run against large photo collections (100k+). EXIF reading is
cheap (~2–5 ms per file) so a full pass typically takes minutes, not hours.
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


console = Console()


# ── file filters ──────────────────────────────────────────────────────────
PHOTO_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif",
    ".png", ".heic", ".heif",
    ".webp", ".tif", ".tiff",
    ".gif", ".bmp", ".dng",
}

# Files inside a managed Aperture / iPhoto library. Never move these.
APERTURE_EXTS = {
    ".apversion", ".apmaster", ".apdetected", ".apalbum", ".apfolder",
    ".apvault", ".apaccount", ".aplibraryinfo", ".bam",
}

# Package directory suffixes — the macOS bundle formats Aperture uses. Any
# file whose path contains one of these directory components is inside a
# managed library and must be skipped entirely.
APERTURE_PKG_SUFFIXES = (".aplibrary", ".apdata", ".photoslibrary")


def _in_managed_library(p: Path) -> bool:
    for part in p.parts:
        lp = part.lower()
        if lp.endswith(APERTURE_PKG_SUFFIXES):
            return True
    return False


# ── date resolution ───────────────────────────────────────────────────────
# Pillow is imported lazily so a broken install doesn't block other modes.
_PIL_REGISTERED = False


def _ensure_pil() -> None:
    global _PIL_REGISTERED
    if _PIL_REGISTERED:
        return
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        # HEIC support is best-effort; JPEG/PNG work without it.
        pass
    _PIL_REGISTERED = True


def _read_exif_date(path: Path) -> tuple[datetime, str] | None:
    """Return (datetime, source_tag) from EXIF, or None if unavailable."""
    _ensure_pil()
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return None

    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # Resolve the sub-IFD where DateTimeOriginal actually lives.
            ifd = exif.get_ifd(ExifTags.IFD.Exif) if hasattr(ExifTags, "IFD") else exif
            # Try in order of reliability.
            candidates = [
                ("DateTimeOriginal", ifd),
                ("DateTimeDigitized", ifd),
                ("DateTime", exif),
            ]
            reverse_tags = {v: k for k, v in ExifTags.TAGS.items()}
            for name, source in candidates:
                tag_id = reverse_tags.get(name)
                if tag_id is None:
                    continue
                raw = source.get(tag_id) if hasattr(source, "get") else None
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("ascii", errors="ignore")
                raw = str(raw).strip().rstrip("\x00")
                # EXIF format: "YYYY:MM:DD HH:MM:SS"
                try:
                    dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    try:
                        dt = datetime.strptime(raw[:10], "%Y:%m:%d")
                    except ValueError:
                        continue
                if 1990 <= dt.year <= datetime.now().year + 1:
                    return dt, f"exif_{name.lower()}"
    except Exception:
        return None
    return None


# Phone / camera filename patterns that embed the date.
_FILENAME_DATE_PATTERNS = [
    # IMG_20190215_143021.jpg / VID_YYYYMMDD_HHMMSS etc.
    re.compile(r"(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})[_\-T]?\d{4,6}"),
    # 20190215_143021 at the start
    re.compile(r"^(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})"),
    # YYYY-MM-DD anywhere
    re.compile(r"\b(?P<y>(?:19|20)\d{2})[-_.](?P<m>\d{1,2})[-_.](?P<d>\d{1,2})\b"),
    # MM-DD-YY near the start (some phone cameras)
    re.compile(r"^(?P<m>\d{1,2})[-_](?P<d>\d{1,2})[-_](?P<y2>\d{2})"),
]


def _read_filename_date(stem: str) -> tuple[datetime, str] | None:
    for pat in _FILENAME_DATE_PATTERNS:
        m = pat.search(stem)
        if not m:
            continue
        gd = m.groupdict()
        try:
            if "y" in gd:
                year = int(gd["y"])
            else:
                y2 = int(gd["y2"])
                year = 2000 + y2 if y2 < 50 else 1900 + y2
            month = int(gd["m"])
            day = int(gd["d"])
        except (ValueError, TypeError, KeyError):
            continue
        if not (1990 <= year <= datetime.now().year + 1):
            continue
        if not (1 <= month <= 12):
            continue
        if not (1 <= day <= 31):
            continue
        try:
            return datetime(year, month, day), "filename"
        except ValueError:
            continue
    return None


def _read_folder_date(parent: Path) -> tuple[datetime, str] | None:
    """Last resort: does the parent folder have a year in its name?"""
    m = re.search(r"\b(19|20)\d{2}\b", parent.name)
    if not m:
        return None
    year = int(m.group(0))
    if not (1990 <= year <= datetime.now().year + 1):
        return None
    return datetime(year, 1, 1), "folder_year"


def _mtime_date(path: Path) -> tuple[datetime, str] | None:
    try:
        dt = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None
    if 1990 <= dt.year <= datetime.now().year + 1:
        return dt, "mtime"
    return None


def _ocr_date_stamp(path: Path) -> tuple[datetime, str] | None:
    """Try OCR date-overlay detection on a photo (e.g. film-camera date imprint)."""
    try:
        from .signals.ocr import parse_date_stamps
        ocr_dt = parse_date_stamps([path])
        if ocr_dt:
            return ocr_dt, "ocr_date_stamp"
    except ImportError:
        pass
    return None


def resolve_date(path: Path) -> tuple[datetime, str] | None:
    """Best-effort date resolution. Returns (datetime, source) or None."""
    return (
        _read_exif_date(path)
        or _read_filename_date(path.stem)
        or _ocr_date_stamp(path)
        or _read_folder_date(path.parent)
        or _mtime_date(path)
    )


# ── destination ──────────────────────────────────────────────────────────
_INVALID = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    s = "".join(ch if ch not in _INVALID else " " for ch in s)
    return " ".join(s.split()).strip(".")[:80]


def photo_destination(dest_root: Path, dt: datetime, filename: str) -> Path:
    return (
        dest_root
        / "Photos"
        / f"{dt.year:04d}"
        / f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
        / filename
    )


# ── run ──────────────────────────────────────────────────────────────────
@dataclass
class PhotoSummary:
    total_files: int = 0
    moved: int = 0
    skipped_aperture: int = 0
    skipped_collision: int = 0
    quarantined_undated: int = 0
    failed: int = 0
    per_year: dict = field(default_factory=dict)
    per_source: dict = field(default_factory=dict)


def _iter_photos(src: Path) -> list[Path]:
    """Every image file under src, excluding managed-library internals."""
    out: list[Path] = []
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix in APERTURE_EXTS:
            continue  # Aperture-internal metadata — never move
        if suffix not in PHOTO_EXTS:
            continue
        if _in_managed_library(p):
            continue
        out.append(p)
    return out


def run_photos(
    settings,
    src: Path,
    dest: Path,
    quarantine: Path,
    dry_run: bool = False,
) -> tuple[PhotoSummary, Path]:
    # refuse accidental overlap
    src_resolved = src.resolve()
    dest_resolved = dest.resolve()
    if dest_resolved == src_resolved or str(dest_resolved).startswith(str(src_resolved) + os.sep):
        raise ValueError("dest is inside src — refusing to run (would recursively re-process)")

    dest.mkdir(parents=True, exist_ok=True)
    quarantine.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = dest / f"photos-{stamp}.log"

    # Pre-walk — fast, so we can give a decent progress bar.
    photos = _iter_photos(src)
    # Count the Aperture skips separately so the user sees them.
    aperture_count = 0
    for p in src.rglob("*"):
        if p.is_file():
            suffix = p.suffix.lower()
            if suffix in APERTURE_EXTS or _in_managed_library(p):
                aperture_count += 1

    summary = PhotoSummary(total_files=len(photos), skipped_aperture=aperture_count)
    if not photos:
        console.print(f"[yellow]No photos found under[/] {src}")
        log_path.touch()
        return summary, log_path

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    with log_path.open("w", encoding="utf-8") as log_f, Progress(*columns, console=console) as progress:
        task = progress.add_task("photos", total=len(photos))
        for p in photos:
            progress.update(task, description=p.name[:60])
            _process_photo(p, dest, quarantine, dry_run, summary, log_f)
            progress.advance(task)

    return summary, log_path


def _process_photo(
    photo: Path,
    dest: Path,
    quarantine: Path,
    dry_run: bool,
    summary: PhotoSummary,
    log_f,
) -> None:
    resolved = resolve_date(photo)

    entry: dict = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "src": str(photo),
    }

    if resolved is None:
        # Quarantine with sidecar
        q_dst = quarantine / photo.parent.name / photo.name
        if not dry_run:
            try:
                q_dst.parent.mkdir(parents=True, exist_ok=True)
                if q_dst.exists():
                    q_dst = q_dst.with_stem(q_dst.stem + "." + datetime.now().strftime("%H%M%S"))
                shutil.move(str(photo), str(q_dst))
                sidecar = q_dst.with_suffix(q_dst.suffix + ".quorum.json")
                sidecar.write_text(
                    json.dumps({"reason": "no_date", "parent": photo.parent.name}, indent=2),
                    encoding="utf-8",
                )
            except OSError as e:
                summary.failed += 1
                entry.update({"dst": str(q_dst), "action": "fail", "error": str(e)})
                log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log_f.flush()
                return
        summary.quarantined_undated += 1
        entry.update({"dst": str(q_dst), "action": "quarantine_no_date"})
        log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log_f.flush()
        return

    dt, source = resolved
    summary.per_year[dt.year] = summary.per_year.get(dt.year, 0) + 1
    summary.per_source[source] = summary.per_source.get(source, 0) + 1

    dst = photo_destination(dest, dt, photo.name)
    if dst.exists():
        # Use content hash, NOT size, to decide whether these are truly the
        # same file. Different photos can coincidentally share a name (iPhone
        # reuses IMG_1234.JPG across generations; cameras reuse DSC_####.JPG)
        # and approximately share size. The old size-check silently dropped
        # thousands of unique photos during testing — never again.
        import hashlib
        def _hash(p, block=1 << 16):
            h = hashlib.sha1()
            try:
                with p.open("rb") as f:
                    while chunk := f.read(block):
                        h.update(chunk)
                return h.hexdigest()
            except OSError:
                return ""
        same_content = _hash(photo) == _hash(dst)
        if same_content:
            summary.skipped_collision += 1
            entry.update({
                "dst": str(dst),
                "action": "skip_collision_same_content",
                "source": source,
            })
            log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log_f.flush()
            return
        # Different content — find a free `(N)` suffix instead of overwriting.
        stem, suffix, parent = dst.stem, dst.suffix, dst.parent
        for i in range(1, 100):
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                dst = candidate
                break
            if _hash(candidate) == _hash(photo):
                summary.skipped_collision += 1
                entry.update({
                    "dst": str(candidate),
                    "action": "skip_collision_same_content",
                    "source": source,
                })
                log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log_f.flush()
                return

    if not dry_run:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(photo), str(dst))
        except OSError as e:
            summary.failed += 1
            entry.update({"dst": str(dst), "action": "fail", "error": str(e)})
            log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            log_f.flush()
            return

    summary.moved += 1
    entry.update({
        "dst": str(dst),
        "action": "move",
        "source": source,
        "date": dt.isoformat(timespec="seconds"),
    })
    log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log_f.flush()


def print_summary(summary: PhotoSummary, log_path: Path, dry_run: bool) -> None:
    title = "Photos run"
    if dry_run:
        title += " (DRY RUN — nothing moved)"
    t = Table(title=title)
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("photos seen", str(summary.total_files))
    t.add_row("[green]moved into Year/Date layout[/]", str(summary.moved))
    t.add_row("[yellow]quarantined (no date resolvable)[/]", str(summary.quarantined_undated))
    t.add_row("[dim]skipped (already at destination)[/]", str(summary.skipped_collision))
    t.add_row("[cyan]skipped (Aperture / managed library)[/]", str(summary.skipped_aperture))
    t.add_row("[red]failed[/]", str(summary.failed))
    console.print(t)

    if summary.per_source:
        st = Table(title="Date source")
        st.add_column("source")
        st.add_column("count", justify="right")
        for s, n in sorted(summary.per_source.items(), key=lambda x: -x[1]):
            st.add_row(s, str(n))
        console.print(st)

    if summary.per_year:
        yt = Table(title="Photos per year")
        yt.add_column("year")
        yt.add_column("count", justify="right")
        for y in sorted(summary.per_year):
            yt.add_row(str(y), str(summary.per_year[y]))
        console.print(yt)

    console.print(f"Log: [bold]{log_path}[/]")
    console.print(f"Undo: [dim]quorum undo {log_path}[/]")
