"""Plex Collections auto-generation from face clusters and scene tags.

Analyzes the faces.db (populated by ``enrich-photos``) and ``.quorum.json``
sidecars to build two kinds of collections:

  * **Person collections** — ``Videos with Sophia``, ``Videos with Jeff``, etc.
    Any video/photo whose event folder contains a named face gets the tag.

  * **Theme collections** — ``Beach``, ``Birthday``, ``Christmas``, etc.
    Derived from the ``setting`` and ``activity`` fields in scene sidecars and
    the ``<plot>`` text in video ``.nfo`` files.

Collection membership is injected as ``<set><name>…</name></set>`` elements
in the existing ``.nfo`` sidecars so Plex groups them automatically.
"""

from __future__ import annotations

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import Settings
from .photos import PHOTO_EXTS
from .pipeline import VIDEO_EXTS

console = Console()


# ── theme normalisation ─────────────────────────────────────────────────

# Map raw setting/activity tokens to a canonical collection name.
# Order matters: first match wins during normalisation.
_THEME_ALIASES: dict[str, list[str]] = {
    "beach": ["beach", "the beach", "seaside", "ocean", "shore", "seashore"],
    "birthday": ["birthday", "birthdays", "birthday party", "bday"],
    "christmas": ["christmas", "xmas", "christmas morning", "christmas eve"],
    "halloween": ["halloween", "trick or treat", "trick-or-treat"],
    "thanksgiving": ["thanksgiving"],
    "easter": ["easter", "easter egg"],
    "park": ["park", "the park", "playground", "the playground"],
    "swimming": ["swimming", "swim", "pool", "swimming pool", "the pool"],
    "camping": ["camping", "campsite", "camp"],
    "hiking": ["hiking", "hike", "trail"],
    "cooking": ["cooking", "baking", "kitchen"],
    "wedding": ["wedding", "reception"],
    "graduation": ["graduation", "commencement"],
    "sports": ["sports", "soccer", "baseball", "basketball", "football", "game"],
    "school": ["school", "classroom", "school play", "recital"],
    "vacation": ["vacation", "holiday", "trip"],
    "snow": ["snow", "skiing", "sledding", "snowman"],
    "garden": ["garden", "gardening", "yard", "backyard"],
    "zoo": ["zoo", "aquarium"],
    "museum": ["museum", "exhibit"],
}

# Invert for O(1) lookup: raw_token -> canonical
_TOKEN_TO_THEME: dict[str, str] = {}
for _canon, _aliases in _THEME_ALIASES.items():
    for _alias in _aliases:
        _TOKEN_TO_THEME[_alias] = _canon


def _normalise_theme(raw: str) -> str | None:
    """Map a raw setting/activity string to a canonical theme name, or None."""
    key = raw.strip().lower()
    if key in _TOKEN_TO_THEME:
        return _TOKEN_TO_THEME[key]
    # Partial match: check if any alias is a substring of the raw value
    for alias, canon in _TOKEN_TO_THEME.items():
        if alias in key:
            return canon
    return None


# ── person collections ──────────────────────────────────────────────────

def _person_collections(db_path: Path, min_appearances: int = 3) -> dict[str, list[str]]:
    """Query faces.db for named people and map them to media paths.

    Returns ``{"Sophia": ["/path/to/video1.mp4", ...], ...}`` where each
    person appears in at least *min_appearances* distinct event folders.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT label, photo_path FROM faces "
            "WHERE label IS NOT NULL AND label_source != '' "
            "AND label NOT LIKE 'Person %'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {}

    # Group photo paths by person label
    person_photos: dict[str, list[str]] = {}
    for label, photo_path in rows:
        person_photos.setdefault(label, []).append(photo_path)

    result: dict[str, list[str]] = {}

    for label, photo_paths in person_photos.items():
        # Distinct event folders (parent of each photo)
        event_folders: set[str] = set()
        for pp in photo_paths:
            event_folders.add(str(Path(pp).parent))

        if len(event_folders) < min_appearances:
            continue

        # Collect all media paths that should get this person's collection tag:
        # 1. The photos themselves
        media_paths: set[str] = set(photo_paths)

        # 2. Videos in the same event folders (siblings)
        for folder_str in event_folders:
            folder = Path(folder_str)
            if folder.is_dir():
                for child in folder.iterdir():
                    if child.is_file() and child.suffix.lower() in VIDEO_EXTS:
                        media_paths.add(str(child))

        result[label] = sorted(media_paths)

    return result


# ── theme collections ───────────────────────────────────────────────────

def _theme_collections(root: Path, min_count: int = 3) -> dict[str, list[Path]]:
    """Walk sidecars and NFOs to build theme-based collections.

    Returns ``{"Beach": [path1, path2, ...], ...}`` where each theme appears
    in at least *min_count* distinct event folders.
    """
    # theme -> set of (event_folder, media_path) tuples
    theme_events: dict[str, dict[str, list[Path]]] = {}  # theme -> {folder -> [paths]}

    search_dirs = []
    for subdir in ("Photos", "Home Videos"):
        d = root / subdir
        if d.is_dir():
            search_dirs.append(d)

    for search_dir in search_dirs:
        for path in search_dir.rglob("*"):
            if not path.is_file():
                continue

            themes_found: list[str] = []
            media_path: Path | None = None

            # .quorum.json sidecars — read setting and activity
            if path.name.endswith(".quorum.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for key in ("setting", "activity"):
                    raw = data.get(key, "")
                    if raw and raw != "unknown":
                        theme = _normalise_theme(raw)
                        if theme:
                            themes_found.append(theme)
                # The media file is the sidecar path minus the .quorum.json suffix
                # e.g. photo.jpg.quorum.json -> photo.jpg
                stem = path.name
                if stem.endswith(".quorum.json"):
                    stem = stem[: -len(".quorum.json")]
                media_path = path.parent / stem

            # .nfo files — read <plot> text for theme keywords
            elif path.suffix.lower() == ".nfo":
                try:
                    tree = ET.parse(path)
                    nfo_root = tree.getroot()
                except Exception:
                    continue
                plot_el = nfo_root.findtext("plot", "")
                if plot_el:
                    plot_lower = plot_el.lower()
                    for alias, canon in _TOKEN_TO_THEME.items():
                        # Word-boundary match to avoid false positives
                        if re.search(r"\b" + re.escape(alias) + r"\b", plot_lower):
                            themes_found.append(canon)
                # The media file is the NFO path with a video extension
                for ext in VIDEO_EXTS:
                    candidate = path.with_suffix(ext)
                    if candidate.exists():
                        media_path = candidate
                        break
                if media_path is None:
                    # Also check photo extensions
                    for ext in PHOTO_EXTS:
                        candidate = path.with_suffix(ext)
                        if candidate.exists():
                            media_path = candidate
                            break

            if not themes_found or media_path is None:
                continue

            event_folder = str(media_path.parent)
            for theme in set(themes_found):  # deduplicate within one file
                if theme not in theme_events:
                    theme_events[theme] = {}
                theme_events[theme].setdefault(event_folder, []).append(media_path)

    # Filter to themes with enough distinct event folders
    result: dict[str, list[Path]] = {}
    for theme, folders in theme_events.items():
        if len(folders) >= min_count:
            all_paths: list[Path] = []
            for paths in folders.values():
                all_paths.extend(paths)
            result[theme] = sorted(set(all_paths))

    return result


# ── NFO injection ───────────────────────────────────────────────────────

def _inject_collection_tags(nfo_path: Path, collection_names: list[str]) -> None:
    """Add ``<set><name>…</name></set>`` elements to an existing .nfo file.

    Idempotent: skips collection names that already exist in the file.
    """
    tree = ET.parse(nfo_path)
    root = tree.getroot()

    # Find existing set names
    existing = {s.findtext("name", "") for s in root.findall("set")}

    changed = False
    for name in collection_names:
        if name not in existing:
            set_el = ET.SubElement(root, "set")
            ET.SubElement(set_el, "name").text = name
            changed = True

    if changed:
        ET.indent(tree, space="  ", level=0)
        tree.write(nfo_path, encoding="utf-8", xml_declaration=True)


# ── result type ─────────────────────────────────────────────────────────

@dataclass
class CollectionSummary:
    person_collections: int = 0
    theme_collections: int = 0
    nfos_updated: int = 0


# ── main entry point ────────────────────────────────────────────────────

def run_collections(
    settings: Settings,
    root: Path,
    min_person_appearances: int = 3,
    min_theme_count: int = 3,
) -> tuple[CollectionSummary, Path]:
    """Build person + theme collections and inject tags into .nfo files.

    Returns ``(summary, log_path)``.
    """
    summary = CollectionSummary()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = root / f"collections-{stamp}.log"

    # Build video-to-collections mapping
    video_collections: dict[str, list[str]] = {}  # media_path -> [collection_names]

    # ── Person collections from faces.db ──
    db_path = root / "faces.db"
    if db_path.exists():
        persons = _person_collections(db_path, min_person_appearances)
        for name, paths in persons.items():
            collection_name = f"Videos with {name}"
            summary.person_collections += 1
            for p in paths:
                video_collections.setdefault(p, []).append(collection_name)
        if persons:
            console.print(
                f"[cyan]Found {len(persons)} person collection(s) "
                f"from faces.db[/]"
            )
    else:
        console.print("[yellow]No faces.db found — skipping person collections[/]")

    # ── Theme collections from scene tags ──
    themes = _theme_collections(root, min_theme_count)
    for theme, paths in themes.items():
        collection_name = theme.title()
        summary.theme_collections += 1
        for p in paths:
            video_collections.setdefault(str(p), []).append(collection_name)
    if themes:
        console.print(
            f"[cyan]Found {len(themes)} theme collection(s) "
            f"from scene tags[/]"
        )

    # ── Inject collection tags into NFOs ──
    with log_path.open("w", encoding="utf-8") as log_f:
        for video_path, collections in video_collections.items():
            nfo = Path(video_path).with_suffix(".nfo")
            if not nfo.exists():
                continue
            try:
                _inject_collection_tags(nfo, collections)
                summary.nfos_updated += 1
                log_f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "nfo": str(nfo),
                    "collections": collections,
                }, ensure_ascii=False) + "\n")
            except Exception as e:
                console.log(f"[yellow]failed to update {nfo.name}: {e}[/]")

    return summary, log_path


# ── summary printer ─────────────────────────────────────────────────────

def print_summary(summary: CollectionSummary, log_path: Path) -> None:
    t = Table(title="Quorum collections")
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("[cyan]person collections[/]", str(summary.person_collections))
    t.add_row("[cyan]theme collections[/]", str(summary.theme_collections))
    t.add_row("[green]NFOs updated[/]", str(summary.nfos_updated))
    console.print(t)
    console.print(f"Log: [bold]{log_path}[/]")
