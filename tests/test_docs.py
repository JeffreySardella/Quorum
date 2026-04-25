from __future__ import annotations

from pathlib import Path

from quorum.plugins.docs import (
    DocumentPlugin, analyze_document, extract_text,
    _extract_year, _classify_document, _generate_name,
)


class TestExtractText:
    def test_txt_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("Hello world, this is a test document.")
        text = extract_text(f)
        assert "Hello world" in text

    def test_csv_file(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("name,value\nalice,100\nbob,200")
        text = extract_text(f)
        assert "alice" in text

    def test_missing_file(self, tmp_path: Path) -> None:
        text = extract_text(tmp_path / "nope.txt")
        assert text == ""

    def test_unknown_format(self, tmp_path: Path) -> None:
        f = tmp_path / "test.xyz"
        f.write_bytes(b"\x00")
        assert extract_text(f) == ""


class TestExtractYear:
    def test_year_in_filename(self) -> None:
        assert _extract_year(Path("report_2023.pdf"), None) == "2023"

    def test_year_in_content(self) -> None:
        assert _extract_year(Path("report.pdf"), "Created in 2024 for review") == "2024"

    def test_no_year_uses_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("no year here")
        year = _extract_year(f, "no year here either")
        assert len(year) == 4


class TestClassifyDocument:
    def test_invoice(self) -> None:
        assert _classify_document(Path("invoice.pdf"), "Invoice #1234 Amount Due: $500") == "Invoice"

    def test_receipt(self) -> None:
        assert _classify_document(Path("receipt.pdf"), "Purchase receipt transaction complete") == "Receipt"

    def test_medical(self) -> None:
        assert _classify_document(Path("report.pdf"), "Patient diagnosis and prescription") == "Medical"

    def test_legal(self) -> None:
        assert _classify_document(Path("doc.pdf"), "This agreement and contract hereby") == "Legal"

    def test_unknown(self) -> None:
        assert _classify_document(Path("random.pdf"), "some random content") == "Other"


class TestGenerateName:
    def test_cleans_underscores(self) -> None:
        assert _generate_name(Path("my_great_doc.pdf"), None) == "my great doc"

    def test_cleans_dashes(self) -> None:
        assert _generate_name(Path("my-great-doc.pdf"), None) == "my great doc"

    def test_truncates_long_names(self) -> None:
        name = _generate_name(Path("a" * 100 + ".pdf"), None)
        assert len(name) <= 60


class TestDocumentPlugin:
    def test_plugin_name(self) -> None:
        p = DocumentPlugin()
        assert p.name == "documents"

    def test_file_types(self) -> None:
        p = DocumentPlugin()
        assert ".pdf" in p.file_types
        assert ".docx" in p.file_types
        assert ".txt" in p.file_types

    def test_scan_txt(self, tmp_path: Path) -> None:
        f = tmp_path / "invoice_2024.txt"
        f.write_text("Invoice #1234\nAmount Due: $500")
        p = DocumentPlugin()
        p.on_register({})
        proposals = p.on_scan([f])
        assert len(proposals) == 1
        assert "Documents" in proposals[0].dest_path
        assert "2024" in proposals[0].dest_path

    def test_scan_empty(self) -> None:
        p = DocumentPlugin()
        p.on_register({})
        assert p.on_scan([]) == []

    def test_apply_moves_file(self, tmp_path: Path) -> None:
        src = tmp_path / "doc.txt"
        src.write_text("content")
        dest_root = tmp_path / "library"

        p = DocumentPlugin()
        p.on_register({"dest_root": dest_root})

        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path=str(src),
            dest_path="Documents/Other/2024/doc.txt",
            confidence=0.5,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "moved"
        assert not src.exists()

    def test_apply_missing_file(self) -> None:
        p = DocumentPlugin()
        p.on_register({})
        from quorum.engine.plugin import Proposal
        proposals = [Proposal(
            media_id=0, source_path="/nope.pdf",
            dest_path="out.pdf", confidence=0.5,
        )]
        results = p.on_apply(proposals)
        assert results[0]["status"] == "skipped"


class TestAnalyzeDocument:
    def test_txt_analysis(self, tmp_path: Path) -> None:
        f = tmp_path / "invoice_2024.txt"
        f.write_text("Invoice #1234 for services rendered")
        info = analyze_document(f)
        assert info["category"] == "Invoice"
        assert info["year"] == "2024"
        assert info["confidence"] >= 0.6
