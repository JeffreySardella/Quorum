from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from rich.console import Console

from ..onnx_helpers import onnx_providers
from .base import Candidate, SignalContext

console = Console()

# Date stamp patterns found on camcorder overlays
_DATE_PATTERNS = [
    # "JAN 15 2003", "Jan 15, 2003"
    (r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2}),?\s+(\d{4})", "%b %d %Y"),
    # "01/15/2003", "1/15/2003"
    (r"(\d{1,2})/(\d{1,2})/(\d{4})", None),   # handled manually (M/D/Y ambiguity)
    # "2003-01-15"
    (r"(\d{4})-(\d{2})-(\d{2})", "%Y-%m-%d"),
    # "15.01.2003"
    (r"(\d{2})\.(\d{2})\.(\d{4})", None),       # handled manually (D.M.Y)
]


class OcrSignal:
    name = "ocr"

    def __init__(self, cpu_only: bool = False) -> None:
        self._cpu_only = cpu_only
        self._reader = None  # lazy init

    def _get_reader(self):
        if self._reader is None:
            try:
                from paddleocr import PaddleOCR
                providers = onnx_providers(self._cpu_only)
                use_gpu = "DmlExecutionProvider" in providers
                self._reader = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=use_gpu)
            except ImportError:
                console.log("[yellow]paddleocr not installed — OCR signal disabled[/]")
                return None
        return self._reader

    def run(self, ctx: SignalContext) -> list[Candidate]:
        if not ctx.keyframes:
            return []
        reader = self._get_reader()
        if reader is None:
            return []

        all_text_parts: list[str] = []
        title_card_texts: list[str] = []
        confidences: list[float] = []

        for frame_path in ctx.keyframes:
            try:
                result = reader.ocr(str(frame_path), cls=True)
                if not result or not result[0]:
                    continue
                for line in result[0]:
                    bbox, (text, conf) = line[0], line[1]
                    all_text_parts.append(text)
                    confidences.append(conf)
                    # Detect title cards: large, centered text with high confidence
                    if conf > 0.8 and len(text.strip()) > 3:
                        # Check if bbox is roughly centered (middle 60% of image)
                        xs = [p[0] for p in bbox]
                        min_x, max_x = min(xs), max(xs)
                        width = max_x - min_x
                        if width > 100:  # reasonably large text
                            title_card_texts.append(text.strip())
            except Exception as e:
                console.log(f"[yellow]OCR failed on {frame_path.name}: {e}[/]")

        if not all_text_parts:
            return []

        all_text = " ".join(all_text_parts)
        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

        # Try to identify from title card text
        candidates: list[Candidate] = []
        if title_card_texts:
            # Use the most prominent title card text as a candidate
            best_title = max(title_card_texts, key=len)
            # Clean up: remove common non-title words
            cleaned = best_title.strip()
            if len(cleaned) >= 3:
                candidates.append(Candidate(
                    title=cleaned,
                    confidence=max(0.0, min(1.0, mean_conf * 0.9)),
                    source=self.name,
                    notes=f"title card text: {cleaned[:100]}; all text: {all_text[:100]}",
                ))

        return candidates


def parse_date_stamps(keyframe_paths: list[Path], cpu_only: bool = False) -> datetime | None:
    """Extract camcorder date overlays from keyframes. Returns parsed datetime or None."""
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return None

    use_gpu = not cpu_only
    reader = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, use_gpu=use_gpu)

    for frame_path in keyframe_paths:
        try:
            result = reader.ocr(str(frame_path), cls=True)
            if not result or not result[0]:
                continue
            for line in result[0]:
                text = line[1][0]
                conf = line[1][1]
                if conf < 0.6:
                    continue
                dt = _try_parse_date(text)
                if dt:
                    return dt
        except Exception:
            continue
    return None


def _try_parse_date(text: str) -> datetime | None:
    """Try to parse a date from OCR text using known camcorder overlay patterns."""
    text = text.upper().strip()

    # Month name patterns: "JAN 15 2003"
    m = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2}),?\s+(\d{4})", text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y")
        except ValueError:
            pass

    # ISO date: "2003-01-15"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # US date: "01/15/2003"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass

    # European date: "15.01.2003"
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    return None
