from __future__ import annotations

import mailbox
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from quorum.plugins.email_import import EmailPlugin, email_stats


def _create_test_mbox(path: Path, attachments: list[tuple[str, bytes]]) -> None:
    """Create a test .mbox file with attachments."""
    mbox = mailbox.mbox(str(path))
    msg = MIMEMultipart()
    msg["From"] = "sender@test.com"
    msg["To"] = "recipient@test.com"
    msg["Subject"] = "Test Email"
    msg["Date"] = "Thu, 15 Jun 2024 10:30:00 +0000"
    msg.attach(MIMEText("Body text"))

    for filename, content in attachments:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(content)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={filename}")
        msg.attach(part)

    mbox.add(msg)
    mbox.close()


class TestEmailPlugin:
    def test_plugin_name(self) -> None:
        assert EmailPlugin().name == "email"

    def test_scan_empty_mbox(self, tmp_path: Path) -> None:
        mbox_path = tmp_path / "empty.mbox"
        mbox = mailbox.mbox(str(mbox_path))
        mbox.close()
        p = EmailPlugin()
        p.on_register({})
        proposals = p.on_scan([mbox_path])
        assert len(proposals) == 0

    def test_scan_mbox_with_attachment(self, tmp_path: Path) -> None:
        mbox_path = tmp_path / "test.mbox"
        _create_test_mbox(mbox_path, [("document.pdf", b"PDF content here")])
        p = EmailPlugin()
        p.on_register({})
        proposals = p.on_scan([mbox_path])
        assert len(proposals) == 1
        assert "document.pdf" in proposals[0].dest_path
        assert proposals[0].metadata["sender"] == "sender@test.com"

    def test_scan_multiple_attachments(self, tmp_path: Path) -> None:
        mbox_path = tmp_path / "test.mbox"
        _create_test_mbox(mbox_path, [
            ("file1.pdf", b"content1"),
            ("file2.jpg", b"content2"),
        ])
        p = EmailPlugin()
        p.on_register({})
        proposals = p.on_scan([mbox_path])
        assert len(proposals) == 2

    def test_apply_extracts_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "output"
        p = EmailPlugin()
        p.on_register({"dest_root": dest})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/archive.mbox",
            dest_path="Email Attachments/2024/doc.pdf",
            confidence=0.8,
            metadata={"content": b"PDF data", "filename": "doc.pdf"},
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "extracted"
        assert (dest / "Email Attachments" / "2024" / "doc.pdf").exists()

    def test_apply_no_content(self) -> None:
        p = EmailPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/x.mbox",
            dest_path="out.pdf", confidence=0.8,
            metadata={},
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "skipped"

    def test_scan_empty_list(self) -> None:
        p = EmailPlugin()
        p.on_register({})
        assert p.on_scan([]) == []


class TestEmailStats:
    def test_stats_with_attachments(self, tmp_path: Path) -> None:
        mbox_path = tmp_path / "test.mbox"
        _create_test_mbox(mbox_path, [("a.pdf", b"x" * 100)])
        stats = email_stats(mbox_path)
        assert stats["total_attachments"] == 1
        assert stats["total_size"] > 0

    def test_stats_empty(self, tmp_path: Path) -> None:
        mbox_path = tmp_path / "empty.mbox"
        mbox = mailbox.mbox(str(mbox_path))
        mbox.close()
        stats = email_stats(mbox_path)
        assert stats["total_attachments"] == 0
