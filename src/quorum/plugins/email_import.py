from __future__ import annotations

import email
import mailbox
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.plugin import Proposal


_BAD_CHARS = set('<>:"/\\|?*')


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch not in _BAD_CHARS).strip().strip(".")[:100] or "unnamed"


class EmailPlugin:
    """Extracts and organizes email attachments from mbox/Maildir archives."""

    name = "email"
    file_types = [".mbox"]

    def __init__(self) -> None:
        self._dest_root: Path | None = None

    def on_register(self, context: dict[str, Any]) -> None:
        self._dest_root = context.get("dest_root")

    def on_scan(self, files: list[Path]) -> list[Proposal]:
        proposals: list[Proposal] = []
        for f in files:
            if f.suffix == ".mbox":
                proposals.extend(self._scan_mbox(f))
            elif f.is_dir() and (f / "cur").exists():
                proposals.extend(self._scan_maildir(f))
        return proposals

    def on_apply(self, proposals: list[Proposal]) -> list[dict]:
        results: list[dict] = []
        for p in proposals:
            content = p.metadata.get("content")
            if not content:
                results.append({"source": p.source_path, "status": "skipped", "reason": "no content"})
                continue

            if self._dest_root:
                dst = self._dest_root / p.dest_path
            else:
                dst = Path(p.dest_path)

            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
                results.append({"source": p.source_path, "dest": str(dst), "status": "extracted"})
            except OSError as e:
                results.append({"source": p.source_path, "status": "failed", "error": str(e)})
        return results

    def _scan_mbox(self, path: Path) -> list[Proposal]:
        proposals: list[Proposal] = []
        try:
            mbox = mailbox.mbox(str(path))
            for msg in mbox:
                proposals.extend(self._extract_attachments(msg, str(path)))
            mbox.close()
        except Exception:
            pass
        return proposals

    def _scan_maildir(self, path: Path) -> list[Proposal]:
        proposals: list[Proposal] = []
        try:
            md = mailbox.Maildir(str(path))
            for msg in md:
                proposals.extend(self._extract_attachments(msg, str(path)))
            md.close()
        except Exception:
            pass
        return proposals

    def _extract_attachments(self, msg: email.message.Message, source: str) -> list[Proposal]:
        proposals: list[Proposal] = []
        sender = msg.get("From", "unknown")
        date_str = msg.get("Date", "")
        subject = msg.get("Subject", "no subject")

        # Parse date
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            year = dt.strftime("%Y")
            date_formatted = dt.strftime("%Y-%m-%d")
        except Exception:
            year = datetime.now().strftime("%Y")
            date_formatted = datetime.now().strftime("%Y-%m-%d")

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            filename = part.get_filename()
            if not filename:
                continue

            content = part.get_payload(decode=True)
            if not content:
                continue

            safe_name = _safe(filename)
            dest = Path("Email Attachments") / year / f"{date_formatted} — {safe_name}"

            proposals.append(Proposal(
                media_id=0,
                source_path=source,
                dest_path=str(dest),
                confidence=0.8,
                metadata={
                    "filename": filename,
                    "sender": sender,
                    "subject": subject,
                    "date": date_formatted,
                    "size": len(content),
                    "content": content,
                },
            ))
        return proposals


def email_stats(path: Path) -> dict[str, Any]:
    """Get attachment statistics from an email archive."""
    plugin = EmailPlugin()
    plugin.on_register({})
    proposals = plugin.on_scan([path])

    total_size = sum(p.metadata.get("size", 0) for p in proposals)
    senders: dict[str, int] = {}
    for p in proposals:
        sender = p.metadata.get("sender", "unknown")
        senders[sender] = senders.get(sender, 0) + 1

    return {
        "total_attachments": len(proposals),
        "total_size": total_size,
        "unique_senders": len(senders),
        "top_senders": sorted(senders.items(), key=lambda x: -x[1])[:5],
    }
