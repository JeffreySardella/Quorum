from __future__ import annotations

import json
import re

from ..ollama_client import OllamaClient
from .base import Candidate, SignalContext


PROMPT = """You are identifying an unknown video file from a small set of keyframes.

Look at the keyframes. Based ONLY on what you can actually see (title cards,
channel logos, credit text, costumes, sets, recognizable faces, animation
style, etc.), answer in strict JSON with this exact schema:

{
  "likely_title": "best guess at the movie or TV show title, or null",
  "is_tv_show": true,
  "year_guess": 1999,
  "confidence": 0.0,
  "evidence": "one short sentence citing what in the frames led to this guess"
}

Rules:
- If you cannot identify it with reasonable certainty, return null for
  likely_title and confidence <= 0.2.
- year_guess may be null if unknown.
- Return ONLY the JSON object. No prose, no code fences."""


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class VisionSignal:
    name = "vision"

    def __init__(self, client: OllamaClient, model: str) -> None:
        self.client = client
        self.model = model

    def run(self, ctx: SignalContext) -> list[Candidate]:
        if not ctx.keyframes:
            return []
        raw = self.client.generate(self.model, PROMPT, images=ctx.keyframes)
        data = _parse(raw)
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
        year: int | None = None
        if isinstance(year_raw, int) and 1900 <= year_raw <= 2100:
            year = year_raw
        kind = "tv" if data.get("is_tv_show") else "movie"
        return [Candidate(
            title=str(title).strip(),
            year=year,
            kind=kind,
            confidence=max(0.0, min(1.0, conf)),
            source=self.name,
            notes=str(data.get("evidence", ""))[:200],
        )]
