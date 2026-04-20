from __future__ import annotations

import re
import shutil
import subprocess
from functools import cache
from pathlib import Path


@cache
def ffmpeg_bin() -> str:
    """Resolve ffmpeg. Prefer the bundled imageio-ffmpeg binary because it's a
    known-good modern version; system PATH often includes crusty builds
    shipped with unrelated tools (e.g. Panda3D ships an ancient ffmpeg) that
    can't probe durations on modern MP4 containers.

    Override by setting QUORUM_FFMPEG=<full path to ffmpeg.exe>.
    """
    import os
    override = os.environ.get("QUORUM_FFMPEG")
    if override:
        return override
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    raise RuntimeError("no ffmpeg — install imageio-ffmpeg or put ffmpeg on PATH")


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


def probe_duration(video: Path) -> float:
    """Probe duration by reading ffmpeg's stderr output (avoids a separate ffprobe dependency)."""
    # Force utf-8 + errors=replace so non-cp1252 filenames (Korean, emoji, etc.)
    # in ffmpeg's stderr can't corrupt the decode and crash the pipeline.
    r = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", str(video)],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    m = _DURATION_RE.search(r.stderr)
    if not m:
        return 0.0
    h, mn, s = m.groups()
    try:
        return int(h) * 3600 + int(mn) * 60 + float(s)
    except ValueError:
        return 0.0


def extract_keyframes(video: Path, out_dir: Path, count: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video)
    if duration <= 0 or count <= 0:
        return []
    offsets = [duration * (i + 1) / (count + 1) for i in range(count)]
    frames: list[Path] = []
    for i, t in enumerate(offsets):
        path = out_dir / f"frame_{i:03d}.jpg"
        if path.exists():
            frames.append(path)
            continue
        subprocess.run(
            [
                ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{t:.2f}", "-i", str(video),
                "-frames:v", "1", "-q:v", "3", str(path),
            ],
            capture_output=True, check=True,
        )
        if path.exists():
            frames.append(path)
    return frames


def extract_audio_clip(video: Path, out_path: Path, seconds: int) -> Path | None:
    """Mono 16kHz WAV, sampled ~20% into the video (past credits / intros)."""
    duration = probe_duration(video)
    if duration <= 0:
        return None
    start = min(max(duration * 0.2, 60.0), max(duration - seconds - 10.0, 0.0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.2f}", "-i", str(video),
            "-t", str(seconds), "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(out_path),
        ],
        capture_output=True, check=True,
    )
    return out_path if out_path.exists() else None
