from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from .db import QuorumDB


def create_manifest(db: QuorumDB, output_path: Path, since: str | None = None) -> dict[str, int]:
    """Generate a portable backup manifest as a SQLite database."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = sqlite3.connect(str(output_path))
    manifest.execute("""
        CREATE TABLE IF NOT EXISTS manifest_info (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    manifest.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            type TEXT NOT NULL,
            size INTEGER NOT NULL,
            checksum TEXT,
            created_at TEXT,
            event_name TEXT
        )
    """)
    manifest.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            file_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT
        )
    """)
    manifest.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            file_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            value TEXT NOT NULL
        )
    """)

    manifest.execute(
        "INSERT OR REPLACE INTO manifest_info VALUES (?, ?)",
        ("created_at", datetime.now().isoformat(timespec="seconds")),
    )

    media_list = db.list_media()
    if since:
        media_list = [m for m in media_list if (m.get("created_at") or "") >= since]

    file_count = 0
    for m in media_list:
        event_name = None
        if m.get("event_id"):
            event = db.get_event(m["event_id"])
            if event:
                event_name = event["name"]

        manifest.execute(
            "INSERT INTO files (id, path, type, size, checksum, created_at, event_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (m["id"], m["path"], m["type"], m["size"], m.get("checksum"), m.get("created_at"), event_name),
        )

        for meta in db.get_metadata(m["id"]):
            manifest.execute(
                "INSERT INTO metadata (file_id, key, value) VALUES (?, ?, ?)",
                (m["id"], meta["key"], meta["value"]),
            )

        for tag in db.get_tags(m["id"]):
            manifest.execute(
                "INSERT INTO tags (file_id, category, value) VALUES (?, ?, ?)",
                (m["id"], tag["category"], tag["value"]),
            )

        file_count += 1

    manifest.commit()
    manifest.close()

    return {"files": file_count, "output": str(output_path)}


def verify_manifest(manifest_path: Path) -> dict[str, int]:
    """Verify backup integrity against a manifest."""
    conn = sqlite3.connect(str(manifest_path))
    rows = conn.execute("SELECT path, size, checksum FROM files").fetchall()
    conn.close()

    verified = 0
    missing = 0
    corrupted = 0

    for path, expected_size, expected_checksum in rows:
        p = Path(path)
        if not p.exists():
            missing += 1
            continue
        actual_size = p.stat().st_size
        if actual_size != expected_size:
            corrupted += 1
            continue
        if expected_checksum:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            if h.hexdigest() != expected_checksum:
                corrupted += 1
                continue
        verified += 1

    return {"verified": verified, "missing": missing, "corrupted": corrupted, "total": len(rows)}


def diff_manifests(m1_path: Path, m2_path: Path) -> dict[str, list[str]]:
    """Compare two manifests and show differences."""
    c1 = sqlite3.connect(str(m1_path))
    c2 = sqlite3.connect(str(m2_path))

    paths1 = {r[0] for r in c1.execute("SELECT path FROM files").fetchall()}
    paths2 = {r[0] for r in c2.execute("SELECT path FROM files").fetchall()}

    c1.close()
    c2.close()

    return {
        "added": sorted(paths2 - paths1),
        "removed": sorted(paths1 - paths2),
        "unchanged": sorted(paths1 & paths2),
    }
