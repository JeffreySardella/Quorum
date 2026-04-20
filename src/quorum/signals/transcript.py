from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Protocol

from rich.console import Console

from ..ollama_client import OllamaClient
from .base import Candidate, SignalContext


console = Console()


# ── prompt ────────────────────────────────────────────────────────────────
PROMPT = """You are identifying a video file from a short transcript of its dialog.

Given the transcript below, identify what movie or TV show it is most likely from.
Return ONLY a JSON object:

{
  "likely_title": "best guess at the title, or null",
  "is_tv_show": true,
  "year_guess": 1999,
  "confidence": 0.0,
  "evidence": "one short sentence citing specific quotable lines or unique plot / setting cues"
}

Rules:
- If the dialog is too generic (greetings, filler, small talk) to identify, set
  likely_title to null and confidence <= 0.2.
- Do NOT guess from vibes. Require specific quotable lines, named characters,
  references to unique plot points, or distinctive settings.
- year_guess and is_tv_show may be null if unknown.
- Return ONLY the JSON object, no other text.

Transcript:
\"\"\"
{text}
\"\"\"
"""


_TS_PREFIX = re.compile(
    r"^\s*\[\d{2}:\d{2}[:.]\d{2}[.,]\d{1,3}\s*-->\s*\d{2}:\d{2}[:.]\d{2}[.,]\d{1,3}\]\s*",
    re.MULTILINE,
)


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ── backends ──────────────────────────────────────────────────────────────
class TranscriptBackend(Protocol):
    def available(self) -> bool: ...
    def transcribe(self, audio: Path) -> str: ...


class FasterWhisperBackend:
    """Pure-Python backend. pip install pulls everything. CPU-only on AMD Windows
    (CTranslate2 has no ROCm backend), but a 30-second clip at `small` model
    transcribes in a few seconds on a modern CPU — more than fine for ID."""

    def __init__(self, model_size: str, language: str, compute_type: str = "auto") -> None:
        self.model_size = model_size
        self.language = None if language.lower() == "auto" else language
        self.compute_type = compute_type
        self._model = None

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            with console.status(
                f"[cyan]loading whisper model '{self.model_size}' "
                f"(first run downloads ~ hundreds of MB)…[/]"
            ):
                self._model = WhisperModel(
                    self.model_size, device="cpu", compute_type=self.compute_type
                )
        return self._model

    def transcribe(self, audio: Path) -> str:
        model = self._load()
        # beam_size=1 (greedy) + condition_on_previous_text=False collectively
        # disable the hallucination-loop trap Whisper can fall into on noisy or
        # silent audio. Observed 600+ second stalls without these settings.
        segments, _info = model.transcribe(
            str(audio),
            language=self.language,
            vad_filter=True,
            beam_size=1,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()


class WhisperCppBackend:
    """External whisper.cpp CLI (the Vulkan Windows build gives real GPU
    acceleration on AMD). Expects `whisper-cli.exe` and a ggml model file."""

    def __init__(self, binary: Path, model: Path, language: str) -> None:
        self.binary = binary
        self.model = model
        self.language = language

    def available(self) -> bool:
        return bool(self.binary) and bool(self.model) and self.binary.exists() and self.model.exists()

    def transcribe(self, audio: Path) -> str:
        out_prefix = audio.with_suffix("")
        cmd = [
            str(self.binary),
            "-m", str(self.model),
            "-f", str(audio),
            "-otxt",
            "-of", str(out_prefix),
            "-t", "8",
        ]
        if self.language and self.language.lower() != "auto":
            cmd += ["-l", self.language]
        try:
            subprocess.run(cmd, capture_output=True, check=True, text=True, timeout=600)
        except subprocess.CalledProcessError:
            return ""
        txt_path = out_prefix.with_suffix(".txt")
        if not txt_path.exists():
            return ""
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        return _TS_PREFIX.sub("", raw).strip()


# ── signal ────────────────────────────────────────────────────────────────
class TranscriptSignal:
    name = "transcript"

    def __init__(self, client: OllamaClient, text_model: str, backend: TranscriptBackend) -> None:
        self.client = client
        self.text_model = text_model
        self.backend = backend

    def run(self, ctx: SignalContext) -> list[Candidate]:
        if not ctx.audio_clip or not ctx.audio_clip.exists():
            return []
        if not self.backend.available():
            return []

        try:
            text = self.backend.transcribe(ctx.audio_clip)
        except Exception as e:
            console.log(f"[yellow]transcribe failed on {ctx.video.name}: {e}[/]")
            return []
        if not text or len(text) < 40:
            return []

        # .replace, not .format — prompt contains literal {/} from JSON schema example
        raw = self.client.generate(self.text_model, PROMPT.replace("{text}", text[:4000]))
        data = _parse_json(raw)
        if not data:
            return []

        title = data.get("likely_title")
        if not title or not str(title).strip():
            return []

        try:
            conf = float(data.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0

        year_raw = data.get("year_guess")
        year: int | None = (
            year_raw if isinstance(year_raw, int) and 1900 <= year_raw <= 2100 else None
        )
        kind = "tv" if data.get("is_tv_show") else "movie"

        return [Candidate(
            title=str(title).strip(),
            year=year,
            kind=kind,
            confidence=max(0.0, min(1.0, conf)),
            source=self.name,
            notes=str(data.get("evidence", ""))[:200],
        )]


def build_backend(whisper_cfg) -> TranscriptBackend | None:
    """Build a transcript backend from config, or return None if unavailable."""
    if whisper_cfg.backend == "whisper.cpp":
        return WhisperCppBackend(
            whisper_cfg.binary or Path(),
            whisper_cfg.model or Path(),
            whisper_cfg.language,
        )
    # default: faster-whisper
    return FasterWhisperBackend(
        whisper_cfg.model_size,
        whisper_cfg.language,
        whisper_cfg.compute_type,
    )
