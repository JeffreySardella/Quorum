from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .db import QuorumDB


_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif", ".bmp", ".webp"}
_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg", ".mpeg"}


@dataclass
class DupFile:
    media_id: int
    path: str
    size: int
    media_type: str
    checksum: str | None = None
    phash: str | None = None
    created_at: str | None = None
    keep: bool = False


@dataclass
class DupCluster:
    id: int
    strategy: str
    files: list[DupFile] = field(default_factory=list)
    recommended_keep: int | None = None


@dataclass
class DedupReport:
    clusters: list[DupCluster] = field(default_factory=list)
    scanned_at: str = ""
    total_files_scanned: int = 0
    total_duplicates: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DedupReport:
        report = cls(
            scanned_at=data.get("scanned_at", ""),
            total_files_scanned=data.get("total_files_scanned", 0),
            total_duplicates=data.get("total_duplicates", 0),
        )
        for c in data.get("clusters", []):
            cluster = DupCluster(
                id=c["id"],
                strategy=c["strategy"],
                recommended_keep=c.get("recommended_keep"),
                files=[DupFile(**f) for f in c.get("files", [])],
            )
            report.clusters.append(cluster)
        return report


def _compute_checksum(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _compute_phash(path: Path) -> str | None:
    try:
        from PIL import Image
        import imagehash
        img = Image.open(str(path))
        return str(imagehash.phash(img))
    except Exception:
        return None


def _pick_best(files: list[DupFile]) -> int:
    """Pick the best file to keep: largest size wins (proxy for quality)."""
    best = max(files, key=lambda f: f.size)
    return best.media_id


def scan_duplicates(
    db: QuorumDB,
    aggressive: bool = False,
) -> DedupReport:
    """Scan for duplicate files in the database.

    Standard mode: exact checksum matches only.
    Aggressive mode: also checks perceptual hashes for photos and
    cross-media timestamp matching.
    """
    report = DedupReport(scanned_at=datetime.now().isoformat(timespec="seconds"))
    cluster_id = 0
    media_list = db.list_media()
    report.total_files_scanned = len(media_list)

    # Strategy 1: Exact checksum duplicates
    checksum_map: dict[str, list[DupFile]] = {}
    for m in media_list:
        path = Path(m["path"])
        if not path.exists():
            # Use stored checksum if file doesn't exist locally
            cs = m.get("checksum")
        else:
            cs = _compute_checksum(path)
            if cs and cs != m.get("checksum"):
                db.conn.execute("UPDATE media SET checksum = ? WHERE id = ?", (cs, m["id"]))
                db.conn.commit()
        if cs:
            df = DupFile(
                media_id=m["id"], path=m["path"], size=m["size"],
                media_type=m["type"], checksum=cs, created_at=m.get("created_at"),
            )
            checksum_map.setdefault(cs, []).append(df)

    for cs, files in checksum_map.items():
        if len(files) < 2:
            continue
        cluster_id += 1
        cluster = DupCluster(id=cluster_id, strategy="exact_checksum", files=files)
        cluster.recommended_keep = _pick_best(files)
        report.clusters.append(cluster)
        report.total_duplicates += len(files) - 1

    if not aggressive:
        return report

    # Strategy 2: Perceptual hash for photos
    seen_checksums = {cs for cs, files in checksum_map.items() if len(files) >= 2}
    phash_map: dict[str, list[DupFile]] = {}
    for m in media_list:
        path = Path(m["path"])
        if path.suffix.lower() not in _PHOTO_EXTS:
            continue
        if not path.exists():
            continue
        # Skip if already in an exact-match cluster
        cs = m.get("checksum") or _compute_checksum(path)
        if cs in seen_checksums:
            continue
        ph = _compute_phash(path)
        if ph:
            df = DupFile(
                media_id=m["id"], path=m["path"], size=m["size"],
                media_type=m["type"], phash=ph, created_at=m.get("created_at"),
            )
            phash_map.setdefault(ph, []).append(df)

    for ph, files in phash_map.items():
        if len(files) < 2:
            continue
        cluster_id += 1
        cluster = DupCluster(id=cluster_id, strategy="perceptual_hash", files=files)
        cluster.recommended_keep = _pick_best(files)
        report.clusters.append(cluster)
        report.total_duplicates += len(files) - 1

    # Strategy 3: Cross-media timestamp matching (photo taken during video)
    photos_with_time = []
    videos_with_time = []
    for m in media_list:
        if not m.get("created_at"):
            continue
        if m["type"] == "photo":
            photos_with_time.append(m)
        elif m["type"] == "video":
            videos_with_time.append(m)

    for photo in photos_with_time:
        for video in videos_with_time:
            from .events import _parse_dt
            pt = _parse_dt(photo["created_at"])
            vt = _parse_dt(video["created_at"])
            if pt and vt:
                diff = abs((pt - vt).total_seconds())
                dur = video.get("duration") or 0
                if diff <= max(dur, 5):
                    # Check face overlap
                    photo_faces = {t["value"] for t in db.get_tags(photo["id"], category="face")}
                    video_faces = {t["value"] for t in db.get_tags(video["id"], category="face")}
                    if photo_faces & video_faces:
                        cluster_id += 1
                        cluster = DupCluster(
                            id=cluster_id,
                            strategy="cross_media_moment",
                            files=[
                                DupFile(media_id=video["id"], path=video["path"], size=video["size"],
                                        media_type="video", created_at=video.get("created_at")),
                                DupFile(media_id=photo["id"], path=photo["path"], size=photo["size"],
                                        media_type="photo", created_at=photo.get("created_at")),
                            ],
                        )
                        cluster.recommended_keep = video["id"]
                        report.clusters.append(cluster)
                        report.total_duplicates += 1

    return report


def save_report(report: DedupReport, path: Path) -> None:
    path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_report(path: Path) -> DedupReport:
    data = json.loads(path.read_text(encoding="utf-8"))
    return DedupReport.from_dict(data)


def apply_dedup(
    db: QuorumDB,
    report: DedupReport,
    holding_dir: Path,
    cluster_id: int | None = None,
) -> dict[str, int]:
    """Move duplicate files to holding directory. Returns counts."""
    import shutil
    holding_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    skipped = 0
    failed = 0

    clusters = report.clusters
    if cluster_id is not None:
        clusters = [c for c in clusters if c.id == cluster_id]

    for cluster in clusters:
        keep_id = cluster.recommended_keep
        for f in cluster.files:
            if f.media_id == keep_id:
                continue
            src = Path(f.path)
            if not src.exists():
                skipped += 1
                continue
            dst = holding_dir / src.name
            if dst.exists():
                dst = holding_dir / f"{src.stem}.{f.media_id}{src.suffix}"
            try:
                shutil.move(str(src), str(dst))
                db.insert_action(
                    operation="dedup_move",
                    source_path=str(src),
                    dest_path=str(dst),
                    metadata=json.dumps({"cluster_id": cluster.id, "strategy": cluster.strategy}),
                    created_at=datetime.now().isoformat(timespec="seconds"),
                )
                moved += 1
            except OSError:
                failed += 1

    return {"moved": moved, "skipped": skipped, "failed": failed}
