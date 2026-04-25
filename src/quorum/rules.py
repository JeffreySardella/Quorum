from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Rule:
    name: str
    match: dict[str, Any]
    action: dict[str, str]
    priority: int = 0
    enabled: bool = True


@dataclass
class RuleMatch:
    rule: Rule
    file_path: str
    dest_path: str


def load_rules(config_data: dict) -> list[Rule]:
    """Load rules from config dict (parsed from config.toml)."""
    rules_data = config_data.get("rules", [])
    rules: list[Rule] = []
    for rd in rules_data:
        rules.append(Rule(
            name=rd.get("name", "unnamed"),
            match=rd.get("match", {}),
            action=rd.get("action", {}),
            priority=rd.get("priority", 0),
            enabled=rd.get("enabled", True),
        ))
    return sorted(rules, key=lambda r: -r.priority)


def match_file(file_path: Path, rules: list[Rule], context: dict[str, Any] | None = None) -> RuleMatch | None:
    """Test a file against all rules, return the first (highest priority) match."""
    ctx = context or {}
    for rule in rules:
        if not rule.enabled:
            continue
        if _matches(file_path, rule.match, ctx):
            dest = _expand_template(rule.action.get("move_to", ""), file_path, ctx)
            return RuleMatch(rule=rule, file_path=str(file_path), dest_path=dest)
    return None


def _matches(file_path: Path, conditions: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Check if a file matches all conditions in a rule."""
    for key, value in conditions.items():
        if key == "extension":
            exts = value if isinstance(value, list) else [value]
            if file_path.suffix.lower() not in [e.lower() for e in exts]:
                return False

        elif key == "type":
            if ctx.get("type") != value:
                return False

        elif key == "filename_matches":
            if not re.search(value, file_path.name, re.IGNORECASE):
                return False

        elif key == "size_gt":
            try:
                if file_path.exists() and file_path.stat().st_size <= value:
                    return False
            except OSError:
                return False

        elif key == "size_lt":
            try:
                if file_path.exists() and file_path.stat().st_size >= value:
                    return False
            except OSError:
                return False

        elif key == "faces":
            file_faces = set(ctx.get("faces", []))
            required = set(value) if isinstance(value, list) else {value}
            if not required.issubset(file_faces):
                return False

        elif key == "scene_contains":
            scenes = " ".join(ctx.get("scenes", []))
            if value.lower() not in scenes.lower():
                return False

        elif key == "ocr_contains":
            ocr = ctx.get("ocr_text", "")
            if value.lower() not in ocr.lower():
                return False

        elif key == "transcript_contains":
            transcript = ctx.get("transcript", "")
            if value.lower() not in transcript.lower():
                return False

    return True


def _expand_template(template: str, file_path: Path, ctx: dict[str, Any]) -> str:
    """Expand template variables in a destination path."""
    now = datetime.now()
    replacements = {
        "{year}": ctx.get("year", now.strftime("%Y")),
        "{month}": ctx.get("month", now.strftime("%m")),
        "{day}": ctx.get("day", now.strftime("%d")),
        "{date}": ctx.get("date", now.strftime("%Y-%m-%d")),
        "{ext}": file_path.suffix,
        "{face}": ctx.get("faces", [""])[0] if ctx.get("faces") else "",
        "{scene}": ctx.get("scenes", [""])[0] if ctx.get("scenes") else "",
        "{category}": ctx.get("category", ""),
    }
    result = template
    for key, value in replacements.items():
        result = result.replace(key, str(value))
    return result
