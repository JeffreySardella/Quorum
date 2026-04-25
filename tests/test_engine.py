from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from quorum.engine.plugin import PluginRegistry, Proposal
from quorum.engine.runner import PluginRunner
from quorum.db import QuorumDB


# ── Test plugin implementation ──────────────────────────────────────────

class MockPlugin:
    name = "mock"
    file_types = [".mock", ".tst"]

    def __init__(self) -> None:
        self.registered = False
        self.context: dict = {}

    def on_register(self, context: dict[str, Any]) -> None:
        self.registered = True
        self.context = context

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        return [
            Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=f"/organized/{f.name}",
                confidence=0.9,
                metadata={"plugin": "mock"},
            )
            for f in files
        ]

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        return [
            {"source": p.source_path, "dest": p.dest_path, "status": "applied"}
            for p in proposals
        ]


class FailingPlugin:
    name = "failing"
    file_types = [".fail"]

    def on_register(self, context: dict[str, Any]) -> None:
        pass

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        raise RuntimeError("scan failed")

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        raise RuntimeError("apply failed")


# ── Registry tests ──────────────────────────────────────────────────────

class TestPluginRegistry:
    def test_register_and_get(self) -> None:
        registry = PluginRegistry()
        plugin = MockPlugin()
        registry.register(plugin)
        assert registry.get("mock") is plugin

    def test_get_missing(self) -> None:
        registry = PluginRegistry()
        assert registry.get("nonexistent") is None

    def test_register_calls_on_register(self) -> None:
        registry = PluginRegistry()
        plugin = MockPlugin()
        registry.register(plugin, context={"key": "value"})
        assert plugin.registered is True
        assert plugin.context == {"key": "value"}

    def test_get_for_file(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        plugin = registry.get_for_file(Path("/test/file.mock"))
        assert plugin is not None
        assert plugin.name == "mock"

    def test_get_for_file_case_insensitive(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        plugin = registry.get_for_file(Path("/test/file.MOCK"))
        assert plugin is not None

    def test_get_for_unknown_file(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        assert registry.get_for_file(Path("/test/file.xyz")) is None

    def test_list_plugins(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        plugins = registry.list_plugins()
        assert len(plugins) == 1
        assert plugins[0]["name"] == "mock"
        assert ".mock" in plugins[0]["file_types"]

    def test_list_names(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        assert registry.list_names() == ["mock"]

    def test_multiple_plugins(self) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())
        registry.register(FailingPlugin())
        assert len(registry.list_names()) == 2

    def test_discover_empty(self) -> None:
        registry = PluginRegistry.discover()
        # No plugins installed via entry points in test env
        assert isinstance(registry, PluginRegistry)


# ── Runner tests ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "quorum.db"


class TestPluginRunner:
    def test_scan_directory(self, tmp_path: Path, tmp_db_path: Path) -> None:
        # Create test files
        (tmp_path / "a.mock").write_text("content a")
        (tmp_path / "b.tst").write_text("content b")
        (tmp_path / "c.txt").write_text("not a mock file")

        registry = PluginRegistry()
        registry.register(MockPlugin())

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            proposals = runner.scan_directory(tmp_path)

        assert len(proposals) == 2  # .mock and .tst, not .txt

    def test_scan_specific_plugin(self, tmp_path: Path, tmp_db_path: Path) -> None:
        (tmp_path / "a.mock").write_text("content")
        (tmp_path / "b.fail").write_text("content")

        registry = PluginRegistry()
        registry.register(MockPlugin())
        registry.register(FailingPlugin())

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            proposals = runner.scan_directory(tmp_path, plugin_name="mock")

        assert len(proposals) == 1

    def test_scan_empty_directory(self, tmp_path: Path, tmp_db_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()

        registry = PluginRegistry()
        registry.register(MockPlugin())

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            proposals = runner.scan_directory(empty)

        assert proposals == []

    def test_scan_nonexistent_directory(self, tmp_path: Path, tmp_db_path: Path) -> None:
        registry = PluginRegistry()
        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            proposals = runner.scan_directory(tmp_path / "nonexistent")
        assert proposals == []

    def test_failing_plugin_doesnt_crash(self, tmp_path: Path, tmp_db_path: Path) -> None:
        (tmp_path / "a.fail").write_text("content")

        registry = PluginRegistry()
        registry.register(FailingPlugin())

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            proposals = runner.scan_directory(tmp_path)

        assert proposals == []  # failed gracefully

    def test_apply_proposals(self, tmp_path: Path, tmp_db_path: Path) -> None:
        registry = PluginRegistry()
        registry.register(MockPlugin())

        proposals = [
            Proposal(media_id=1, source_path="/test/a.mock", dest_path="/out/a.mock", confidence=0.9),
        ]

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            results = runner.apply_proposals(proposals)

        assert len(results) == 1
        assert results[0]["status"] == "applied"

    def test_apply_failing_plugin(self, tmp_path: Path, tmp_db_path: Path) -> None:
        registry = PluginRegistry()
        registry.register(FailingPlugin())

        proposals = [
            Proposal(media_id=1, source_path="/test/a.fail", dest_path="/out/a.fail", confidence=0.9),
        ]

        with QuorumDB(tmp_db_path) as db:
            runner = PluginRunner(db, registry)
            results = runner.apply_proposals(proposals)

        assert len(results) == 1
        assert results[0]["status"] == "failed"


# ── Proposal tests ──────────────────────────────────────────────────────

class TestProposal:
    def test_proposal_creation(self) -> None:
        p = Proposal(media_id=1, source_path="/a.mkv", dest_path="/b.mkv", confidence=0.95)
        assert p.media_id == 1
        assert p.confidence == 0.95
        assert p.metadata == {}

    def test_proposal_with_metadata(self) -> None:
        p = Proposal(media_id=1, source_path="/a", dest_path="/b", confidence=0.8,
                     metadata={"title": "Test", "year": 2024})
        assert p.metadata["title"] == "Test"
