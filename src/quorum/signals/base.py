from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Candidate:
    """One identification guess from one signal."""
    title: str
    year: int | None = None
    kind: str | None = None         # "movie" | "tv" | None
    season: int | None = None
    episode: int | None = None
    confidence: float = 0.0          # 0..1 within this signal
    source: str = ""                 # producing signal name
    notes: str = ""


@dataclass
class SignalContext:
    video: Path
    keyframes: list[Path] = field(default_factory=list)
    audio_clip: Path | None = None


class Signal(Protocol):
    name: str

    def run(self, ctx: SignalContext) -> list[Candidate]:
        ...
