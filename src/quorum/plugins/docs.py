from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_DOC_EXTS = [".pdf", ".docx", ".doc", ".txt", ".rtf", ".odt", ".xlsx", ".csv", ".pptx"]

_CATEGORIES = [
    "receipt", "invoice", "manual", "legal", "correspondence",
    "medical", "tax", "personal", "reference", "other",
]

_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")


class DocumentPlugin:
    """Organizes documents by category and date."""

    name = "documents"
    file_types = _DOC_EXTS

    def __init__(self) -> None:
        self._dest_root: Path | None = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            if not f.exists():
                continue

            info = analyze_document(f)
            category = _safe(info.get("category", "Other"))
            year = info.get("year", datetime.now().strftime("%Y"))
            name = _safe(info.get("name", f.stem))
            ext = f.suffix.lower()

            dest = Path("Documents") / category / year / f"{name}{ext}"

            proposals.append(Proposal(
                media_id=0,
                source_path=str(f),
                dest_path=str(dest),
                confidence=info.get("confidence", 0.4),
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
                results.append({"source": str(src), "status": "skipped", "reason": "not found"})
                continue

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                results.append({"source": str(src), "dest": str(dst), "status": "moved"})
            except OSError as e:
                results.append({"source": str(src), "status": "failed", "error": str(e)})
        return results


def analyze_document(path: Path) -> dict[str, Any]:
    """Analyze a document to determine category, date, and descriptive name."""
    info: dict[str, Any] = {"confidence": 0.3}

    # Extract text content
    text = extract_text(path)
    if text:
        info["text_preview"] = text[:500]

    # Extract date from filename or content
    info["year"] = _extract_year(path, text)

    # Classify by content keywords
    info["category"] = _classify_document(path, text)

    # Generate descriptive name
    info["name"] = _generate_name(path, text)

    # Confidence based on how much info we extracted
    if text and len(text) > 50:
        info["confidence"] = 0.6
    if info["category"] != "Other":
        info["confidence"] = max(info["confidence"], 0.7)

    return info


def extract_text(path: Path) -> str:
    """Extract text from various document formats."""
    ext = path.suffix.lower()

    if ext == ".txt":
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:5000]
        except OSError:
            return ""

    if ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                pages = []
                for page in pdf.pages[:5]:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n".join(pages)[:5000]
        except Exception:
            return ""

    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs[:100])[:5000]
        except Exception:
            return ""

    if ext == ".csv":
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            return ""

    return ""


def _extract_year(path: Path, text: str | None) -> str:
    """Extract year from filename or document content."""
    # Check filename first
    m = re.search(r"(20\d{2}|19\d{2})", path.stem)
    if m:
        return m.group(1)

    # Check content
    if text:
        dates = re.findall(r"(20\d{2}|19\d{2})", text[:1000])
        if dates:
            return dates[0]

    # Fall back to file modification time
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%Y")
    except OSError:
        return datetime.now().strftime("%Y")


def _classify_document(path: Path, text: str | None) -> str:
    """Classify document by keyword matching."""
    combined = (path.stem + " " + (text or "")).lower()

    patterns = {
        "Receipt": ["receipt", "purchase", "order confirmation", "payment received", "transaction"],
        "Invoice": ["invoice", "bill", "amount due", "payment terms", "remittance"],
        "Tax": ["tax", "w-2", "1099", "irs", "tax return", "deduction"],
        "Medical": ["medical", "patient", "diagnosis", "prescription", "health", "doctor"],
        "Legal": ["agreement", "contract", "terms and conditions", "hereby", "legal", "court"],
        "Correspondence": ["dear", "sincerely", "regards", "letter", "memo"],
        "Manual": ["manual", "instructions", "guide", "how to", "user guide", "documentation"],
        "Reference": ["reference", "specification", "datasheet", "whitepaper", "report"],
    }

    best_cat = "Other"
    best_score = 0
    for category, keywords in patterns.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > best_score:
            best_score = score
            best_cat = category

    return best_cat


def _generate_name(path: Path, text: str | None) -> str:
    """Generate a descriptive name for the document."""
    stem = path.stem
    # Clean up common filename patterns
    name = re.sub(r"[_\-]+", " ", stem)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 60:
        name = name[:57] + "..."
    return name or "Untitled"
