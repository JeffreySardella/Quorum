"""Photo enrichment — scene tagging and face clustering.

Walks ``Photos/YYYY/YYYY-MM-DD/`` directories created by the ``photos``
command and, for each image:

  1. Runs the vision LLM to tag the scene (setting, activity, objects, mood).
  2. Writes a ``.quorum.json`` sidecar with the structured tags.
  3. Writes a Plex-compatible ``.nfo`` sidecar with ``<genre>`` / ``<tag>``
     elements derived from the scene tags.

Optionally (enabled by default), it also:

  4. Extracts face embeddings via InsightFace (ONNX Runtime).
  5. Clusters faces with agglomerative clustering (cosine distance).
  6. Asks the vision LLM to name each anonymous cluster.
  7. Applies seed corrections from a ``faces/`` directory at the library root.
  8. Writes face labels into the ``.nfo`` as ``<actor>`` elements.

Resume-friendly: skips photos that already have a ``.quorum.json`` sidecar
unless ``--force`` is passed.
"""

from __future__ import annotations

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from .config import Settings
from .ollama_client import OllamaClient
from .photos import PHOTO_EXTS

if TYPE_CHECKING:
    import numpy as np


console = Console()


# ── prompts ──────────────────────────────────────────────────────────────

SCENE_PROMPT = """You are describing a single photo for a family photo library.

Look at the image and return ONLY a JSON object:

{
  "setting": "where this photo was taken (beach, kitchen, park, restaurant, backyard, etc.)",
  "activity": "what is happening (opening presents, blowing out candles, swimming, eating dinner, posing for photo, etc.)",
  "objects": ["notable items visible — cake, balloons, dog, Christmas tree, etc."],
  "mood": "overall mood (festive, casual, formal, playful, serene, etc.)"
}

Be concrete and observational. Return ONLY the JSON object. No prose, no code fences."""


NAMING_PROMPT = """You are identifying a person who appears in a family photo library.

You are given a set of representative face crops of the SAME person, plus
contextual information about which folders and events they appear in.

Context:
{context}

Based on the face crops and context, guess who this person is.  Return ONLY
a JSON object:

{{
  "name": "your best guess for this person's first name (or 'Unknown' if truly impossible)",
  "confidence": 0.7,
  "reasoning": "one sentence explaining your guess"
}}

Return ONLY the JSON object. No prose, no code fences."""


# ── result types ─────────────────────────────────────────────────────────

@dataclass
class PhotoEnrichResult:
    setting: str
    activity: str
    objects: list[str]
    mood: str


@dataclass
class PhotoEnrichSummary:
    total: int = 0
    scene_tagged: int = 0
    faces_detected: int = 0
    clusters_formed: int = 0
    clusters_named: int = 0
    skipped_existing: int = 0
    failed: int = 0


# ── helpers ──────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ── scene tagging ────────────────────────────────────────────────────────

def scene_tag_one(
    photo: Path,
    ollama: OllamaClient,
    model: str,
) -> PhotoEnrichResult | None:
    """Run the vision LLM on a single photo and return structured scene tags."""
    try:
        raw = ollama.generate(model, SCENE_PROMPT, images=[photo])
    except Exception as e:
        console.log(f"[yellow]vision LLM failed for {photo.name}: {e}[/]")
        return None
    data = _parse_json(raw)
    if not data:
        console.log(f"[yellow]could not parse LLM response for {photo.name}[/]")
        return None
    objects_raw = data.get("objects", [])
    if isinstance(objects_raw, str):
        objects_raw = [s.strip() for s in objects_raw.split(",") if s.strip()]
    return PhotoEnrichResult(
        setting=str(data.get("setting", "unknown")),
        activity=str(data.get("activity", "unknown")),
        objects=[str(o) for o in objects_raw] if isinstance(objects_raw, list) else [],
        mood=str(data.get("mood", "unknown")),
    )


def _write_photo_sidecar(photo: Path, result: PhotoEnrichResult) -> Path:
    """Write a ``.quorum.json`` sidecar next to the photo."""
    sidecar = photo.with_suffix(photo.suffix + ".quorum.json")
    data = {
        "setting": result.setting,
        "activity": result.activity,
        "objects": result.objects,
        "mood": result.mood,
        "tagged_at": datetime.now().isoformat(timespec="seconds"),
    }
    sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return sidecar


def _write_photo_nfo(
    photo: Path,
    result: PhotoEnrichResult,
    faces: list[str] | None = None,
) -> Path:
    """Write a Plex-compatible ``.nfo`` sidecar next to the photo."""
    nfo_path = photo.with_suffix(".nfo")
    root = ET.Element("movie")

    # Title from the parent folder name (the date folder)
    ET.SubElement(root, "title").text = photo.stem

    # Description from scene tags
    description_parts = []
    if result.setting and result.setting != "unknown":
        description_parts.append(f"Setting: {result.setting}.")
    if result.activity and result.activity != "unknown":
        description_parts.append(f"Activity: {result.activity}.")
    if result.mood and result.mood != "unknown":
        description_parts.append(f"Mood: {result.mood}.")
    ET.SubElement(root, "plot").text = " ".join(description_parts) or "Photo"

    # Year from the folder structure (Photos/YYYY/YYYY-MM-DD/)
    year_match = re.search(r"\b(19|20)\d{2}\b", photo.parent.name)
    if year_match:
        ET.SubElement(root, "year").text = year_match.group(0)

    # Genre from setting
    if result.setting and result.setting != "unknown":
        ET.SubElement(root, "genre").text = result.setting

    # Tags from objects
    for obj in result.objects:
        ET.SubElement(root, "tag").text = obj

    # Mood as a tag
    if result.mood and result.mood != "unknown":
        ET.SubElement(root, "tag").text = result.mood

    # Quorum marker
    ET.SubElement(root, "tag").text = "quorum-enriched"

    # Actor elements for identified faces
    if faces:
        for name in faces:
            actor = ET.SubElement(root, "actor")
            ET.SubElement(actor, "name").text = name

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
    return nfo_path


# ── face database ────────────────────────────────────────────────────────

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS faces (
    id            INTEGER PRIMARY KEY,
    photo_path    TEXT    NOT NULL,
    bbox_x        REAL    NOT NULL,
    bbox_y        REAL    NOT NULL,
    bbox_w        REAL    NOT NULL,
    bbox_h        REAL    NOT NULL,
    embedding     BLOB    NOT NULL,
    cluster_id    INTEGER,
    label         TEXT,
    label_source  TEXT,
    confidence    REAL
)
"""


def _init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the faces database and ensure the table exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _store_face(
    conn: sqlite3.Connection,
    photo_path: str,
    bbox: tuple[float, float, float, float],
    embedding_bytes: bytes,
    cluster_id: int | None = None,
    label: str | None = None,
    label_source: str | None = None,
    confidence: float | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO faces (photo_path, bbox_x, bbox_y, bbox_w, bbox_h, "
        "embedding, cluster_id, label, label_source, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (photo_path, *bbox, embedding_bytes, cluster_id, label, label_source, confidence),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _get_embeddings(conn: sqlite3.Connection) -> list[tuple[int, str, bytes, int | None]]:
    """Return ``(id, photo_path, embedding_blob, cluster_id)`` for every face."""
    return conn.execute(
        "SELECT id, photo_path, embedding, cluster_id FROM faces"
    ).fetchall()


def _update_cluster(conn: sqlite3.Connection, face_id: int, cluster_id: int) -> None:
    conn.execute("UPDATE faces SET cluster_id = ? WHERE id = ?", (cluster_id, face_id))
    conn.commit()


def _update_label(
    conn: sqlite3.Connection,
    cluster_id: int,
    label: str,
    label_source: str,
    confidence: float,
) -> None:
    conn.execute(
        "UPDATE faces SET label = ?, label_source = ?, confidence = ? "
        "WHERE cluster_id = ?",
        (label, label_source, confidence, cluster_id),
    )
    conn.commit()


def _get_faces_for_photo(conn: sqlite3.Connection, photo_path: str) -> list[str]:
    """Return the list of face labels for a photo (non-null, non-empty)."""
    rows = conn.execute(
        "SELECT DISTINCT label FROM faces WHERE photo_path = ? AND label IS NOT NULL AND label != ''",
        (photo_path,),
    ).fetchall()
    return [r[0] for r in rows]


# ── face extraction ──────────────────────────────────────────────────────

def _extract_faces(
    photo: Path,
    cpu_only: bool,
) -> list[tuple[tuple[float, float, float, float], bytes]]:
    """Extract face bounding boxes and 512-dim embeddings from a photo.

    Returns a list of ``((x, y, w, h), embedding_bytes)`` tuples.
    Lazy-imports InsightFace and cv2 to avoid heavy startup cost.
    """
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np
        from insightface.app import FaceAnalysis  # type: ignore[import-untyped]
    except ImportError as e:
        console.log(f"[yellow]face extraction unavailable: {e}[/]")
        return []

    from .onnx_helpers import onnx_providers

    try:
        app = FaceAnalysis(providers=onnx_providers(cpu_only))
        app.prepare(ctx_id=0, det_size=(640, 640))

        img = cv2.imread(str(photo))
        if img is None:
            return []

        detected = app.get(img)
        results: list[tuple[tuple[float, float, float, float], bytes]] = []
        for face in detected:
            bbox = face.bbox  # [x1, y1, x2, y2]
            x, y = float(bbox[0]), float(bbox[1])
            w, h = float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1])
            emb: np.ndarray = face.normed_embedding
            results.append(((x, y, w, h), emb.tobytes()))
        return results
    except Exception as e:
        console.log(f"[yellow]face extraction failed for {photo.name}: {e}[/]")
        return []


# ── face clustering ──────────────────────────────────────────────────────

def _cosine_distance(a: bytes, b: bytes) -> float:
    """Compute cosine distance between two embedding blobs."""
    import numpy as np

    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    dot = float(np.dot(va, vb))
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _cluster_faces(
    embeddings: list[tuple[int, bytes]],
    threshold: float,
) -> dict[int, int]:
    """Simple agglomerative clustering on embedding blobs.

    Args:
        embeddings: list of ``(face_id, embedding_bytes)``
        threshold: cosine distance threshold for merging

    Returns:
        Mapping of ``face_id -> cluster_id`` (cluster IDs start at 1).
    """
    if not embeddings:
        return {}

    import numpy as np

    n = len(embeddings)
    # Start: each face is its own cluster
    cluster_of: dict[int, int] = {}
    # cluster_id -> list of indices
    clusters: dict[int, list[int]] = {}
    for i, (fid, _) in enumerate(embeddings):
        cluster_of[fid] = i
        clusters[i] = [i]

    # Precompute pairwise cosine distances (upper triangle)
    vecs = [np.frombuffer(emb, dtype=np.float32) for _, emb in embeddings]
    mat = np.array(vecs)
    # Normalise rows
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    mat = mat / norms
    # Cosine similarity matrix
    sim = mat @ mat.T
    dist = 1.0 - sim

    # Greedy single-linkage: repeatedly merge the closest pair below threshold
    merged = True
    while merged:
        merged = False
        best_dist = threshold
        best_pair: tuple[int, int] | None = None
        cluster_ids = list(clusters.keys())
        for ci, c1 in enumerate(cluster_ids):
            for c2 in cluster_ids[ci + 1:]:
                # Average-linkage distance between clusters
                total = 0.0
                count = 0
                for i1 in clusters[c1]:
                    for i2 in clusters[c2]:
                        total += float(dist[i1, i2])
                        count += 1
                avg = total / count if count else 1.0
                if avg < best_dist:
                    best_dist = avg
                    best_pair = (c1, c2)
        if best_pair is not None:
            c_keep, c_merge = best_pair
            for idx in clusters[c_merge]:
                fid = embeddings[idx][0]
                cluster_of[fid] = c_keep
            clusters[c_keep].extend(clusters[c_merge])
            del clusters[c_merge]
            merged = True

    # Re-number clusters starting from 1
    remap: dict[int, int] = {}
    next_id = 1
    result: dict[int, int] = {}
    for fid, cid in cluster_of.items():
        if cid not in remap:
            remap[cid] = next_id
            next_id += 1
        result[fid] = remap[cid]
    return result


def _assign_to_existing(
    embedding_bytes: bytes,
    centroids: dict[int, bytes],
    threshold: float,
) -> int | None:
    """Try to assign a new embedding to the nearest existing cluster.

    Returns the cluster_id if the distance is below threshold, else None.
    """
    if not centroids:
        return None
    best_cid: int | None = None
    best_dist = threshold
    for cid, centroid_bytes in centroids.items():
        d = _cosine_distance(embedding_bytes, centroid_bytes)
        if d < best_dist:
            best_dist = d
            best_cid = cid
    return best_cid


def _compute_centroids(conn: sqlite3.Connection) -> dict[int, bytes]:
    """Compute the average embedding for each cluster."""
    import numpy as np

    rows = conn.execute(
        "SELECT cluster_id, embedding FROM faces WHERE cluster_id IS NOT NULL"
    ).fetchall()
    cluster_embs: dict[int, list[bytes]] = {}
    for cid, emb_blob in rows:
        cluster_embs.setdefault(cid, []).append(emb_blob)

    centroids: dict[int, bytes] = {}
    for cid, blobs in cluster_embs.items():
        vecs = [np.frombuffer(b, dtype=np.float32) for b in blobs]
        avg = np.mean(vecs, axis=0).astype(np.float32)
        # Normalise
        norm = np.linalg.norm(avg)
        if norm > 1e-9:
            avg = avg / norm
        centroids[cid] = avg.tobytes()
    return centroids


# ── LLM naming ───────────────────────────────────────────────────────────

def _name_clusters(
    conn: sqlite3.Connection,
    ollama: OllamaClient,
    model: str,
    root: Path,
) -> int:
    """Name unnamed clusters using context + vision LLM. Returns count named."""
    # Find clusters that have no label yet
    unnamed = conn.execute(
        "SELECT DISTINCT cluster_id FROM faces "
        "WHERE cluster_id IS NOT NULL AND (label IS NULL OR label = '')"
    ).fetchall()
    if not unnamed:
        return 0

    named_count = 0
    for (cluster_id,) in unnamed:
        # Gather context: which photos / folders contain this person?
        rows = conn.execute(
            "SELECT photo_path FROM faces WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchall()
        if not rows:
            continue

        photo_paths = [r[0] for r in rows]
        folders = sorted(set(str(Path(p).parent.name) for p in photo_paths))

        # Gather scene tags from sidecars
        scene_info: list[str] = []
        for pp in photo_paths[:5]:  # limit to 5 for context window
            sidecar = Path(pp).with_suffix(Path(pp).suffix + ".quorum.json")
            if sidecar.exists():
                try:
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    scene_info.append(
                        f"  - {Path(pp).name}: {data.get('setting', '?')}, "
                        f"{data.get('activity', '?')}"
                    )
                except Exception:
                    pass

        context_lines = [
            f"This person appears in {len(photo_paths)} photos.",
            f"Folders: {', '.join(folders[:10])}",
        ]
        if scene_info:
            context_lines.append("Scene context from some of their photos:")
            context_lines.extend(scene_info)
        context = "\n".join(context_lines)

        # Pick up to 3 representative face crops as images
        rep_paths: list[Path] = []
        for pp in photo_paths[:3]:
            p = Path(pp)
            if p.exists():
                rep_paths.append(p)

        prompt = NAMING_PROMPT.replace("{context}", context)

        try:
            if rep_paths:
                raw = ollama.generate(model, prompt, images=rep_paths)
            else:
                raw = ollama.generate(model, prompt)
            data = _parse_json(raw)
            if data and data.get("name") and data["name"].lower() != "unknown":
                name = str(data["name"]).strip()
                conf = 0.5
                try:
                    conf = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
                except (TypeError, ValueError):
                    pass
                _update_label(conn, cluster_id, name, "llm", conf)
                named_count += 1
            else:
                # Assign anonymous label
                _update_label(conn, cluster_id, f"Person {cluster_id}", "llm", 0.0)
        except Exception as e:
            console.log(f"[yellow]naming cluster {cluster_id} failed: {e}[/]")
            _update_label(conn, cluster_id, f"Person {cluster_id}", "llm", 0.0)

    return named_count


# ── seed matching ────────────────────────────────────────────────────────

def _apply_seeds(
    conn: sqlite3.Connection,
    seed_dir: Path,
    cpu_only: bool,
    threshold: float,
) -> int:
    """Embed seed photos and match to existing clusters. Returns count matched."""
    if not seed_dir.exists():
        return 0

    seed_files = [
        f for f in seed_dir.iterdir()
        if f.is_file() and f.suffix.lower() in PHOTO_EXTS
    ]
    if not seed_files:
        return 0

    centroids = _compute_centroids(conn)
    if not centroids:
        return 0

    matched = 0
    for seed in seed_files:
        label = seed.stem  # e.g. "sophia" from sophia.jpg
        faces = _extract_faces(seed, cpu_only)
        if not faces:
            console.log(f"[yellow]no face found in seed {seed.name}[/]")
            continue

        # Use the first (largest) face
        _, emb_bytes = faces[0]
        best_cid = _assign_to_existing(emb_bytes, centroids, threshold)
        if best_cid is not None:
            # Check current label_source — only override if priority allows
            # Priority: manual > seed > llm
            existing = conn.execute(
                "SELECT label_source FROM faces WHERE cluster_id = ? LIMIT 1",
                (best_cid,),
            ).fetchone()
            current_source = existing[0] if existing else None
            if current_source == "manual":
                console.log(f"[dim]seed {label} matches cluster {best_cid} but has manual label, skipping[/]")
                continue
            _update_label(conn, best_cid, label, "seed", 1.0)
            matched += 1
            console.log(f"[green]seed {seed.name} -> cluster {best_cid} as '{label}'[/]")
        else:
            console.log(f"[yellow]seed {seed.name} did not match any cluster (threshold={threshold})[/]")

    return matched


# ── directory walker ─────────────────────────────────────────────────────

def _iter_photos(root: Path) -> list[Path]:
    """Every photo under ``Photos/`` in the library root."""
    photos_dir = root / "Photos"
    if not photos_dir.exists():
        console.print(f"[yellow]No Photos/ directory found under {root}[/]")
        return []
    return sorted(
        p for p in photos_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in PHOTO_EXTS
    )


# ── main entry point ────────────────────────────────────────────────────

def run_enrich_photos(
    settings: Settings,
    root: Path,
    force: bool = False,
    do_faces: bool = True,
) -> tuple[PhotoEnrichSummary, Path]:
    """Scene-tag photos and optionally cluster faces.

    Returns ``(summary, log_path)``.
    """
    photos = _iter_photos(root)
    summary = PhotoEnrichSummary(total=len(photos))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = root / f"enrich-photos-{stamp}.log"

    if not photos:
        console.print(f"[yellow]No photos found under {root}/Photos[/]")
        log_path.touch()
        return summary, log_path

    ollama = OllamaClient(settings.ollama_url)
    model = settings.models.vision

    # Face DB
    conn: sqlite3.Connection | None = None
    if do_faces:
        db_path = root / "faces.db"
        conn = _init_db(db_path)

    # Track which photos already have faces in DB (for incremental runs)
    existing_face_photos: set[str] = set()
    if conn is not None:
        rows = conn.execute("SELECT DISTINCT photo_path FROM faces").fetchall()
        existing_face_photos = {r[0] for r in rows}

    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]

    new_embeddings: list[tuple[int, bytes]] = []

    try:
        with log_path.open("w", encoding="utf-8") as log_f, \
             Progress(*columns, console=console) as progress:
            task = progress.add_task("enrich-photos", total=len(photos))

            for photo in photos:
                progress.update(task, description=photo.name[:60])
                entry: dict = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "photo": str(photo),
                }

                # ── scene tagging ──
                sidecar = photo.with_suffix(photo.suffix + ".quorum.json")
                if sidecar.exists() and not force:
                    summary.skipped_existing += 1
                    entry["action"] = "skip_existing"
                    log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log_f.flush()
                else:
                    try:
                        result = scene_tag_one(photo, ollama, model)
                        if result:
                            _write_photo_sidecar(photo, result)
                            summary.scene_tagged += 1
                            entry.update({
                                "action": "scene_tagged",
                                "setting": result.setting,
                                "activity": result.activity,
                                "objects": result.objects,
                                "mood": result.mood,
                            })
                        else:
                            summary.failed += 1
                            entry["action"] = "scene_tag_failed"
                    except Exception as e:
                        summary.failed += 1
                        entry.update({
                            "action": "error",
                            "error": f"{type(e).__name__}: {e}"[:500],
                        })

                    log_f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log_f.flush()

                # ── face extraction ──
                if do_faces and conn is not None:
                    photo_str = str(photo)
                    if photo_str not in existing_face_photos:
                        faces = _extract_faces(photo, settings.cpu_only)
                        for bbox, emb_bytes in faces:
                            fid = _store_face(
                                conn, photo_str, bbox, emb_bytes,
                            )
                            new_embeddings.append((fid, emb_bytes))
                            summary.faces_detected += 1

                progress.advance(task)

        # ── clustering ──
        if do_faces and conn is not None and new_embeddings:
            console.print("[cyan]Clustering faces...[/]")

            # Get existing centroids for incremental assignment
            centroids = _compute_centroids(conn)
            max_existing_cluster = 0
            if centroids:
                max_existing_cluster = max(centroids.keys())

            # Try to assign new faces to existing clusters first
            unassigned: list[tuple[int, bytes]] = []
            for fid, emb_bytes in new_embeddings:
                cid = _assign_to_existing(
                    emb_bytes, centroids, settings.faces.distance_threshold,
                )
                if cid is not None:
                    _update_cluster(conn, fid, cid)
                else:
                    unassigned.append((fid, emb_bytes))

            # Cluster the unassigned faces among themselves
            if unassigned:
                assignments = _cluster_faces(unassigned, settings.faces.distance_threshold)
                # Offset cluster IDs to avoid collision with existing ones
                for fid, cid in assignments.items():
                    real_cid = cid + max_existing_cluster
                    _update_cluster(conn, fid, real_cid)

            # Count distinct clusters
            row = conn.execute(
                "SELECT COUNT(DISTINCT cluster_id) FROM faces WHERE cluster_id IS NOT NULL"
            ).fetchone()
            summary.clusters_formed = row[0] if row else 0

            # ── LLM naming ──
            console.print("[cyan]Naming face clusters...[/]")
            summary.clusters_named = _name_clusters(conn, ollama, model, root)

            # ── Seed matching ──
            seed_dir = root / "faces"
            if seed_dir.exists():
                console.print("[cyan]Applying seed corrections...[/]")
                _apply_seeds(conn, seed_dir, settings.cpu_only, settings.faces.distance_threshold)

            # ── Write face review log ──
            review_path = root / f"face-review-{stamp}.log"
            _write_face_review(conn, review_path)
            console.print(f"Face review log: [bold]{review_path}[/]")

        # ── Write NFOs (with face labels if available) ──
        console.print("[cyan]Writing .nfo sidecars...[/]")
        for photo in photos:
            sidecar = photo.with_suffix(photo.suffix + ".quorum.json")
            if not sidecar.exists():
                continue
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                result = PhotoEnrichResult(
                    setting=data.get("setting", "unknown"),
                    activity=data.get("activity", "unknown"),
                    objects=data.get("objects", []),
                    mood=data.get("mood", "unknown"),
                )
                face_labels: list[str] = []
                if conn is not None:
                    face_labels = _get_faces_for_photo(conn, str(photo))
                _write_photo_nfo(photo, result, face_labels)
            except Exception as e:
                console.log(f"[yellow]NFO write failed for {photo.name}: {e}[/]")
    finally:
        ollama.close()
        if conn is not None:
            conn.close()

    return summary, log_path


def _write_face_review(conn: sqlite3.Connection, path: Path) -> None:
    """Write a JSONL log listing all provisional face labels."""
    rows = conn.execute(
        "SELECT cluster_id, label, label_source, confidence, COUNT(*) as cnt "
        "FROM faces WHERE cluster_id IS NOT NULL "
        "GROUP BY cluster_id "
        "ORDER BY cluster_id"
    ).fetchall()
    with path.open("w", encoding="utf-8") as f:
        for cid, label, source, conf, cnt in rows:
            entry = {
                "cluster_id": cid,
                "label": label,
                "label_source": source,
                "confidence": conf,
                "face_count": cnt,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── summary printer ─────────────────────────────────────────────────────

def print_summary(summary: PhotoEnrichSummary, log_path: Path) -> None:
    t = Table(title="Quorum enrich-photos")
    t.add_column("outcome")
    t.add_column("count", justify="right")
    t.add_row("photos seen", str(summary.total))
    t.add_row("[green]scene tagged[/]", str(summary.scene_tagged))
    t.add_row("[cyan]faces detected[/]", str(summary.faces_detected))
    t.add_row("[cyan]clusters formed[/]", str(summary.clusters_formed))
    t.add_row("[cyan]clusters named[/]", str(summary.clusters_named))
    t.add_row("[dim]skipped (sidecar exists)[/]", str(summary.skipped_existing))
    t.add_row("[red]failed[/]", str(summary.failed))
    console.print(t)
    console.print(f"Log: [bold]{log_path}[/]")
