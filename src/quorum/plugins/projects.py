from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_PROJECT_MARKERS = {".git", "package.json", "Makefile", "CMakeLists.txt", "Cargo.toml", "pyproject.toml", "pom.xml"}

_RELATED_EXTENSIONS = {
    ".psd": [".png", ".jpg", ".jpeg", ".tiff"],
    ".ai": [".svg", ".pdf", ".eps"],
    ".docx": [".pdf"],
    ".doc": [".pdf"],
    ".sketch": [".png", ".svg"],
    ".fig": [".png", ".svg"],
    ".tex": [".pdf", ".aux", ".log"],
}


class ProjectPlugin:
    """Detects and groups related project files."""

    name = "projects"
    file_types = [".*"]

    def __init__(self) -> None:
        self._dest_root: Path | None = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        """Scan for project clusters — groups of related files."""
        clusters = detect_project_clusters(files)
        proposals: list[Proposal] = []

        for project_name, cluster_files in clusters.items():
            if len(cluster_files) < 2:
                continue
            dest_dir = Path("Projects") / _safe(project_name)
            for f in cluster_files:
                proposals.append(Proposal(
                    media_id=0,
                    source_path=str(f),
                    dest_path=str(dest_dir / f.name),
                    confidence=0.6,
                    metadata={"project": project_name, "action": "gather"},
                ))
        return proposals

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        # Safety: projects plugin only suggests, never auto-applies
        return [
            {"source": p.source_path, "dest": p.dest_path, "status": "suggested"}
            for p in proposals
        ]


def detect_project_clusters(files: list[Path]) -> dict[str, list[Path]]:
    """Group files into project clusters by name similarity and relationships."""
    clusters: dict[str, list[Path]] = defaultdict(list)

    # Strategy 1: Files near project markers
    dirs_seen: set[Path] = set()
    for f in files:
        if f.name in _PROJECT_MARKERS:
            project_name = f.parent.name
            dirs_seen.add(f.parent)
            clusters[project_name].append(f)

    # Strategy 2: Shared name stems
    stem_groups: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        if not f.is_file():
            continue
        # Normalize stem: strip version suffixes, clean separators
        stem = re.sub(r"[-_]?(v\d+|final|draft|copy|backup|\(\d+\))$", "", f.stem, flags=re.IGNORECASE)
        stem = stem.lower().strip("-_ ")
        if stem:
            stem_groups[stem].append(f)

    for stem, group_files in stem_groups.items():
        if len(group_files) >= 2:
            # Check if they have related extensions
            exts = {f.suffix.lower() for f in group_files}
            if len(exts) > 1:  # Same stem, different formats = related
                clusters[stem].extend(group_files)

    # Strategy 3: Source + export pairs
    by_parent: dict[Path, list[Path]] = defaultdict(list)
    for f in files:
        if f.is_file():
            by_parent[f.parent].append(f)

    for parent, dir_files in by_parent.items():
        ext_map = defaultdict(list)
        for f in dir_files:
            ext_map[f.suffix.lower()].append(f)
        for source_ext, export_exts in _RELATED_EXTENSIONS.items():
            if source_ext in ext_map:
                for export_ext in export_exts:
                    if export_ext in ext_map:
                        project_name = parent.name
                        for f in ext_map[source_ext] + ext_map[export_ext]:
                            if f not in clusters.get(project_name, []):
                                clusters[project_name].append(f)

    # Deduplicate files within clusters
    for name in clusters:
        clusters[name] = list(dict.fromkeys(clusters[name]))

    return dict(clusters)


_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")[:80] or "unnamed"
