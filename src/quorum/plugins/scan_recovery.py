from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_SCAN_EXTS = [".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"]


class ScanRecoveryPlugin:
    """Detects and processes scanned photo prints."""

    name = "scan_recovery"
    file_types = _SCAN_EXTS

    def __init__(self) -> None:
        self._dest_root: Path | None = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            if not f.exists():
                continue
            info = analyze_scan(f)
            if not info.get("is_scan"):
                continue
            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(Path("Recovered Photos") / f.name),
                confidence=info.get("confidence", 0.5),
                metadata=info,
            ))
        return proposals

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        results: list[dict] = []
        for p in proposals:
            src = Path(p.source_path)
            if self._dest_root:
                dst = self._dest_root / p.dest_path
            else:
                dst = Path(p.dest_path)

            if not src.exists():
                results.append({"source": str(src), "status": "skipped"})
                continue

            try:
                processed = _process_scan(src, dst)
                results.append({"source": str(src), "dest": str(processed), "status": "processed"})
            except Exception as e:
                results.append({"source": str(src), "status": "failed", "error": str(e)})
        return results


def analyze_scan(path: Path) -> dict[str, Any]:
    """Analyze whether an image is a scanned photo print."""
    info: dict[str, Any] = {"is_scan": False, "confidence": 0.0}

    try:
        from PIL import Image
        img = Image.open(str(path))
        width, height = img.size
    except Exception:
        return info

    # Heuristic 1: Scanner-typical resolutions (300/600/1200 DPI at common print sizes)
    # Standard print sizes at 300 DPI: 4x6=1200x1800, 5x7=1500x2100, 8x10=2400x3000
    min_dim = min(width, height)
    max_dim = max(width, height)
    aspect = max_dim / min_dim if min_dim > 0 else 0

    # Very high resolution (>2000px on short side) suggests scanner
    if min_dim >= 2000:
        info["confidence"] += 0.3

    # Common print aspect ratios: 3:2 (1.5), 4:3 (1.33), 5:4 (1.25), 7:5 (1.4)
    print_ratios = [1.25, 1.33, 1.4, 1.5]
    for ratio in print_ratios:
        if abs(aspect - ratio) < 0.05:
            info["confidence"] += 0.2
            break

    # Heuristic 2: Check for border regions (uniform color edges = scanner bed)
    try:
        import numpy as np
        arr = np.array(img)
        if len(arr.shape) == 3:
            # Check top/bottom 2% for uniformity
            h = arr.shape[0]
            top = arr[:max(1, h // 50)]
            bottom = arr[-max(1, h // 50):]
            top_std = np.std(top)
            bottom_std = np.std(bottom)
            if top_std < 15 and bottom_std < 15:
                info["confidence"] += 0.3
                info["has_border"] = True
    except Exception:
        pass

    info["width"] = width
    info["height"] = height
    info["aspect_ratio"] = round(aspect, 2)
    info["is_scan"] = info["confidence"] >= 0.4
    return info


def _process_scan(src: Path, dst: Path) -> Path:
    """Process a scanned photo: copy to destination (auto-crop/deskew in future)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))
    return dst
