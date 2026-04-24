from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .base import Candidate, SignalContext

console = Console()


class FingerprintSignal:
    """Audio-fingerprint signal using pyacoustid / Chromaprint.

    Identifies background music in videos, detects duplicate audio across
    files, and returns Candidate results for known recordings.
    """

    name = "fingerprint"

    def __init__(self, api_key: str, cache_dir: Path) -> None:
        self.api_key = api_key
        self.cache_dir = cache_dir
        self._fingerprints: dict[str, dict] = {}
        self._load_fingerprints()

    # ── persistence ──────────────────────────────────────────────────────

    def _fp_path(self) -> Path:
        return self.cache_dir / "fingerprints.json"

    def _load_fingerprints(self) -> None:
        fp = self._fp_path()
        if fp.exists():
            try:
                self._fingerprints = json.loads(fp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._fingerprints = {}

    def save_fingerprints(self) -> None:
        """Write collected fingerprints to cache_dir/fingerprints.json."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fp_path().write_text(
            json.dumps(self._fingerprints, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── main signal ──────────────────────────────────────────────────────

    def run(self, ctx: SignalContext) -> list[Candidate]:
        if not ctx.audio_clip or not ctx.audio_clip.exists():
            return []

        try:
            import acoustid
        except ImportError:
            console.log("[yellow]pyacoustid not installed -- fingerprint signal disabled[/]")
            return []

        # compute fingerprint
        try:
            duration, fingerprint = acoustid.fingerprint_file(str(ctx.audio_clip))
        except Exception as e:
            console.log(f"[yellow]fingerprint failed on {ctx.video.name}: {e}[/]")
            return []

        # store for later dedup
        self._fingerprints[str(ctx.video)] = {
            "duration": duration,
            "fingerprint": fingerprint,
        }

        # query AcoustID
        candidates: list[Candidate] = []
        if not self.api_key:
            return candidates

        try:
            results = acoustid.lookup(self.api_key, fingerprint, duration)
        except Exception as e:
            console.log(f"[yellow]AcoustID lookup failed for {ctx.video.name}: {e}[/]")
            return candidates

        try:
            for score, recording_id, title, artist in acoustid.parse_lookup_result(results):
                if score < 0.8:
                    continue
                if not title:
                    continue

                label = title
                if artist:
                    label = f"{title} - {artist}"

                candidates.append(Candidate(
                    title=label,
                    confidence=max(0.0, min(1.0, score)),
                    source=self.name,
                    notes=f"music:{label}; acoustid={recording_id}",
                ))
        except Exception as e:
            console.log(f"[yellow]AcoustID parse failed for {ctx.video.name}: {e}[/]")

        return candidates

    # ── duplicate detection ──────────────────────────────────────────────

    def find_duplicates(self, threshold: float = 0.9) -> list[tuple[str, str, float]]:
        """Compare all fingerprints pairwise and return near-duplicate pairs.

        Returns a list of (path_a, path_b, similarity) where similarity
        exceeds *threshold*.
        """
        try:
            import chromaprint  # noqa: F401 -- needed for decode_fingerprint
        except ImportError:
            # Fall back: compare raw fingerprint strings with simple ratio
            pass

        keys = list(self._fingerprints.keys())
        duplicates: list[tuple[str, str, float]] = []

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                fp_a = self._fingerprints[keys[i]]["fingerprint"]
                fp_b = self._fingerprints[keys[j]]["fingerprint"]
                sim = _fingerprint_similarity(fp_a, fp_b)
                if sim >= threshold:
                    duplicates.append((keys[i], keys[j], round(sim, 4)))

        return duplicates


def _fingerprint_similarity(fp_a: str, fp_b: str) -> float:
    """Compute similarity between two Chromaprint fingerprint strings.

    Uses hamming distance on the raw bytes for a quick comparison.
    Returns a float in [0.0, 1.0] where 1.0 means identical.
    """
    if fp_a == fp_b:
        return 1.0
    if not fp_a or not fp_b:
        return 0.0

    # Chromaprint fingerprints are base64-encoded; compare the shorter
    # overlap so different-length clips are still comparable.
    min_len = min(len(fp_a), len(fp_b))
    if min_len == 0:
        return 0.0

    matches = sum(a == b for a, b in zip(fp_a[:min_len], fp_b[:min_len]))
    return matches / min_len


def write_dedup_log(
    duplicates: list[tuple[str, str, float]],
    dest: Path,
) -> Path:
    """Write duplicate pairs to a JSONL log file.

    Returns the path of the written log.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = dest / f"dedup-{stamp}.log"
    dest.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as f:
        for path_a, path_b, score in duplicates:
            obj = {"file_a": path_a, "file_b": path_b, "similarity": score}
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    return log_path
