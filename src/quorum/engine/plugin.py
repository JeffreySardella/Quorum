from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Proposal:
    """A proposed organization action from a plugin."""
    media_id: int
    source_path: str
    dest_path: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class QuorumPlugin(Protocol):
    """Interface that all Quorum plugins must implement."""

    name: str
    file_types: list[str]

    def on_register(self, context: dict[str, Any]) -> None:
        """Called when the plugin is registered with the engine."""
        ...

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        """Scan files and return organization proposals."""
        ...

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        """Apply accepted proposals. Returns action log entries."""
        ...


class PluginRegistry:
    """Registry for discovering and managing plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, QuorumPlugin] = {}
        self._file_type_map: dict[str, str] = {}

    def register(self, plugin: QuorumPlugin, context: dict[str, Any] | None = None) -> None:
        self._plugins[plugin.name] = plugin
        for ext in plugin.file_types:
            self._file_type_map[ext.lower()] = plugin.name
        plugin.on_register(context or {})

    def get(self, name: str) -> QuorumPlugin | None:
        return self._plugins.get(name)

    def get_for_file(self, path: Path) -> QuorumPlugin | None:
        ext = path.suffix.lower()
        plugin_name = self._file_type_map.get(ext)
        if plugin_name:
            return self._plugins.get(plugin_name)
        return None

    def list_plugins(self) -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "file_types": p.file_types,
            }
            for p in self._plugins.values()
        ]

    def list_names(self) -> list[str]:
        return list(self._plugins.keys())

    @classmethod
    def discover(cls) -> PluginRegistry:
        """Discover plugins via Python entry points."""
        registry = cls()
        try:
            from importlib.metadata import entry_points
            eps = entry_points()
            plugin_eps = eps.select(group="quorum.plugins") if hasattr(eps, "select") else eps.get("quorum.plugins", [])
            for ep in plugin_eps:
                try:
                    plugin_cls = ep.load()
                    plugin = plugin_cls()
                    registry.register(plugin)
                except Exception:
                    pass
        except Exception:
            pass
        return registry
