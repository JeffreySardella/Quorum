from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import QuorumDB
from .plugin import PluginRegistry, Proposal


class PluginRunner:
    """Orchestrates scan → plugin → proposals → apply workflow."""

    def __init__(self, db: QuorumDB, registry: PluginRegistry) -> None:
        self.db = db
        self.registry = registry

    def scan_directory(self, root: Path, plugin_name: str | None = None) -> list[Proposal]:
        """Scan a directory and route files to appropriate plugins."""
        if not root.exists():
            return []

        # Collect files grouped by plugin
        plugin_files: dict[str, list[Path]] = {}

        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if ".quorum-cache" in f.parts:
                continue

            if plugin_name:
                plugin = self.registry.get(plugin_name)
                if plugin and f.suffix.lower() in [e.lower() for e in plugin.file_types]:
                    plugin_files.setdefault(plugin_name, []).append(f)
            else:
                plugin = self.registry.get_for_file(f)
                if plugin:
                    plugin_files.setdefault(plugin.name, []).append(f)

        # Run each plugin's scan
        all_proposals: list[Proposal] = []
        for pname, files in plugin_files.items():
            plugin = self.registry.get(pname)
            if plugin:
                try:
                    proposals = plugin.on_scan(files)
                    all_proposals.extend(proposals)
                except Exception:
                    pass

        return all_proposals

    def apply_proposals(self, proposals: list[Proposal]) -> list[dict]:
        """Apply proposals by routing to the appropriate plugin."""
        results: list[dict] = []

        # Group by source plugin (infer from file type)
        for proposal in proposals:
            path = Path(proposal.source_path)
            plugin = self.registry.get_for_file(path)
            if plugin:
                try:
                    actions = plugin.on_apply([proposal])
                    results.extend(actions)
                except Exception:
                    results.append({
                        "source": proposal.source_path,
                        "status": "failed",
                        "error": "plugin apply failed",
                    })
        return results
