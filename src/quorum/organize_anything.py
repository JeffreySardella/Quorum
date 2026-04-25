from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import QuorumDB
from .engine.plugin import PluginRegistry, Proposal
from .plugins.downloads import classify_file
from .rules import load_rules, match_file


def organize(
    root: Path,
    db: QuorumDB,
    dest: Path | None = None,
    plugin_name: str | None = None,
    rules_config: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """The 'organize anything' entry point.

    1. Scan directory, detect file types
    2. Apply custom rules first (highest priority)
    3. Route remaining to appropriate plugins
    4. Return proposals (dry_run) or apply and return results
    """
    if not root.exists():
        return {"error": f"Directory not found: {root}", "proposals": [], "results": []}

    files = [f for f in root.rglob("*") if f.is_file() and ".quorum-cache" not in f.parts]
    if not files:
        return {"proposals": [], "results": [], "files_scanned": 0}

    # Load custom rules
    rules = load_rules(rules_config or {})

    # Register all built-in plugins
    registry = _build_registry()

    # Phase 1: Apply custom rules
    rule_proposals: list[Proposal] = []
    remaining_files: list[Path] = []

    for f in files:
        # Build context for rule matching
        classification = classify_file(f)
        ctx = {"type": classification["category"].lower()}

        rule_match = match_file(f, rules, ctx)
        if rule_match:
            rule_proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=rule_match.dest_path,
                confidence=1.0,
                metadata={"source": "rule", "rule_name": rule_match.rule.name},
            ))
        else:
            remaining_files.append(f)

    # Phase 2: Route remaining to plugins
    plugin_proposals: list[Proposal] = []
    if plugin_name:
        plugin = registry.get(plugin_name)
        if plugin:
            matched = [f for f in remaining_files if f.suffix.lower() in [e.lower() for e in plugin.file_types]]
            if matched:
                try:
                    plugin_proposals = plugin.on_scan(matched)
                except Exception:
                    pass
    else:
        # Auto-detect: group by plugin
        plugin_files: dict[str, list[Path]] = {}
        unmatched: list[Path] = []
        for f in remaining_files:
            plug = registry.get_for_file(f)
            if plug:
                plugin_files.setdefault(plug.name, []).append(f)
            else:
                unmatched.append(f)

        for pname, pfiles in plugin_files.items():
            plug = registry.get(pname)
            if plug:
                try:
                    plugin_proposals.extend(plug.on_scan(pfiles))
                except Exception:
                    pass

        # Unmatched files → classify by extension
        for f in unmatched:
            info = classify_file(f)
            plugin_proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(Path(info["category"]) / f.name),
                confidence=0.5,
                metadata={"source": "classify", "category": info["category"]},
            ))

    all_proposals = rule_proposals + plugin_proposals

    result: dict[str, Any] = {
        "files_scanned": len(files),
        "rule_matches": len(rule_proposals),
        "plugin_matches": len(plugin_proposals),
        "proposals": all_proposals,
        "results": [],
    }

    if not dry_run and dest:
        import shutil
        from datetime import datetime
        applied = 0
        for p in all_proposals:
            src = Path(p.source_path)
            dst = dest / p.dest_path
            if not src.exists():
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                db.insert_action(
                    operation="organize",
                    source_path=str(src),
                    dest_path=str(dst),
                    metadata=str(p.metadata),
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
                applied += 1
            except OSError:
                pass
        result["applied"] = applied

    return result


def _build_registry() -> PluginRegistry:
    """Build registry with all built-in plugins."""
    registry = PluginRegistry()
    try:
        from .plugins.music import MusicPlugin
        registry.register(MusicPlugin())
    except Exception:
        pass
    try:
        from .plugins.audio import AudioMemoPlugin
        registry.register(AudioMemoPlugin())
    except Exception:
        pass
    try:
        from .plugins.docs import DocumentPlugin
        registry.register(DocumentPlugin())
    except Exception:
        pass
    try:
        from .plugins.downloads import DownloadsPlugin
        registry.register(DownloadsPlugin())
    except Exception:
        pass
    # Also discover via entry points
    try:
        from .engine.plugin import PluginRegistry as PR
        discovered = PR.discover()
        for name in discovered.list_names():
            plug = discovered.get(name)
            if plug and name not in registry.list_names():
                registry.register(plug)
    except Exception:
        pass
    return registry
