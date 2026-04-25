from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings
from .db import QuorumDB


def detect_events(
    db: QuorumDB,
    gap_hours: float = 2.0,
    ollama_url: str | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Auto-detect events by clustering media on timestamps.

    Algorithm:
    1. Load all media with created_at timestamps, sorted chronologically
    2. Split into clusters at gaps > gap_hours
    3. Refine: merge adjacent clusters sharing 2+ face tags
    4. Name each event from metadata or fallback to date + top scene tag
    5. Store as events in the DB
    """
    # Load media sorted by timestamp
    rows = db.conn.execute(
        "SELECT id, path, type, created_at FROM media "
        "WHERE created_at IS NOT NULL AND created_at != '' "
        "ORDER BY created_at"
    ).fetchall()

    if not rows:
        return {"events_created": 0, "media_assigned": 0}

    # Step 1: Split into temporal clusters
    gap = timedelta(hours=gap_hours)
    clusters: list[list[tuple]] = []
    current_cluster: list[tuple] = [rows[0]]

    for row in rows[1:]:
        prev_time = _parse_dt(current_cluster[-1][3])
        curr_time = _parse_dt(row[3])
        if prev_time and curr_time and (curr_time - prev_time) > gap:
            clusters.append(current_cluster)
            current_cluster = [row]
        else:
            current_cluster.append(row)
    clusters.append(current_cluster)

    # Step 2: Merge adjacent clusters sharing 2+ face tags
    merged = _merge_by_faces(db, clusters)

    # Step 3: Create events
    events_created = 0
    media_assigned = 0

    for cluster in merged:
        if len(cluster) < 1:
            continue

        media_ids = [r[0] for r in cluster]
        start = cluster[0][3]
        end = cluster[-1][3]

        # Check if media is already assigned to an event
        already_assigned = all(
            (db.get_media(mid) or {}).get("event_id") is not None
            for mid in media_ids
        )
        if already_assigned:
            continue

        # Generate event name
        name = _generate_event_name(db, media_ids, start, ollama_url, model)

        # Create event
        eid = db.insert_event(
            name=name,
            start_time=start,
            end_time=end,
            auto_detected=True,
        )

        for mid in media_ids:
            db.assign_media_to_event(mid, eid)
            media_assigned += 1

        events_created += 1

    return {"events_created": events_created, "media_assigned": media_assigned}


def _parse_dt(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _merge_by_faces(
    db: QuorumDB,
    clusters: list[list[tuple]],
) -> list[list[tuple]]:
    """Merge adjacent clusters that share 2+ face tags."""
    if len(clusters) <= 1:
        return clusters

    def _get_faces(cluster: list[tuple]) -> set[str]:
        faces: set[str] = set()
        for row in cluster:
            for tag in db.get_tags(row[0], category="face"):
                faces.add(tag["value"])
        return faces

    merged: list[list[tuple]] = [clusters[0]]
    for cluster in clusters[1:]:
        prev_faces = _get_faces(merged[-1])
        curr_faces = _get_faces(cluster)
        overlap = prev_faces & curr_faces
        if len(overlap) >= 2:
            merged[-1].extend(cluster)
        else:
            merged.append(cluster)

    return merged


def _generate_event_name(
    db: QuorumDB,
    media_ids: list[int],
    start_time: str,
    ollama_url: str | None = None,
    model: str | None = None,
) -> str:
    """Generate a descriptive name for an event."""
    # Collect context
    scene_tags: dict[str, int] = {}
    face_tags: dict[str, int] = {}
    titles: list[str] = []

    for mid in media_ids:
        for tag in db.get_tags(mid, category="scene"):
            scene_tags[tag["value"]] = scene_tags.get(tag["value"], 0) + 1
        for tag in db.get_tags(mid, category="face"):
            face_tags[tag["value"]] = face_tags.get(tag["value"], 0) + 1
        title = db.get_metadata_value(mid, "title")
        if title:
            titles.append(title)

    # Try LLM naming
    if ollama_url and model:
        try:
            from .ollama_client import OllamaClient
            client = OllamaClient(ollama_url)
            try:
                context_parts = []
                if titles:
                    context_parts.append(f"Titles: {', '.join(titles[:5])}")
                if scene_tags:
                    top_scenes = sorted(scene_tags, key=scene_tags.get, reverse=True)[:5]
                    context_parts.append(f"Scenes: {', '.join(top_scenes)}")
                if face_tags:
                    top_faces = sorted(face_tags, key=face_tags.get, reverse=True)[:5]
                    context_parts.append(f"People: {', '.join(top_faces)}")

                prompt = (
                    "Generate a short, descriptive event name (3-6 words) for a group of "
                    f"media files from {start_time[:10]}.\n\n"
                    f"Context:\n" + "\n".join(context_parts) + "\n\n"
                    "Return ONLY the event name, nothing else."
                )
                name = client.generate(model, prompt).strip().strip('"').strip("'")
                if name and len(name) < 80:
                    return name
            finally:
                client.close()
        except Exception:
            pass

    # Fallback: date + top scene tag
    date_str = start_time[:10] if start_time else "Unknown Date"
    if scene_tags:
        top_scene = max(scene_tags, key=scene_tags.get)
        return f"{date_str} — {top_scene.title()}"
    if face_tags:
        top_face = max(face_tags, key=face_tags.get)
        return f"{date_str} — with {top_face}"
    return date_str
