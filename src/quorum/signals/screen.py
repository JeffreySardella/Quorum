from __future__ import annotations

import json
import re

from .base import Candidate, SignalContext


SCREEN_DETECT_PROMPT = """Classify this video frame into exactly ONE category:

1. "camera" - Real-world footage from a camera (people, places, events, nature)
2. "screen_recording" - Computer/phone screen capture (UI elements, apps, browsers, desktop)
3. "gaming" - Video game footage (3D renders, HUD elements, game UI)
4. "presentation" - Slides, tutorials, code editors, educational content

Look for these clues:
- Taskbars, window borders, mouse cursors → screen_recording
- HUD overlays, health bars, rendered 3D → gaming
- Slide layouts, bullet points, code → presentation
- Natural lighting, real faces, physical spaces → camera

Return ONLY a JSON object:
{"category": "camera|screen_recording|gaming|presentation", "confidence": 0.0-1.0, "reasoning": "brief explanation"}"""


class ScreenDetectSignal:
    """Signal that classifies video content as camera/screen/gaming/presentation."""

    name = "screen_detect"

    def __init__(self, ollama_client=None, model: str = "mistral-small3.2:latest") -> None:
        self.ollama = ollama_client
        self.model = model

    def run(self, ctx: SignalContext) -> list[Candidate]:
        if not self.ollama or not ctx.keyframes:
            return []

        # Analyze first 2 keyframes for classification
        categories: dict[str, list[float]] = {}
        for frame in ctx.keyframes[:2]:
            try:
                raw = self.ollama.generate(self.model, SCREEN_DETECT_PROMPT, images=[frame])
                data = _parse_json(raw)
                if data and "category" in data:
                    cat = data["category"]
                    conf = float(data.get("confidence", 0.5))
                    categories.setdefault(cat, []).append(conf)
            except Exception:
                continue

        if not categories:
            return []

        # Pick dominant category
        best_cat = max(categories, key=lambda c: sum(categories[c]) / len(categories[c]))
        avg_conf = sum(categories[best_cat]) / len(categories[best_cat])

        return [
            Candidate(
                title=best_cat,
                confidence=avg_conf,
                source=self.name,
                notes=f"Content classified as {best_cat}",
            )
        ]


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
