from __future__ import annotations

from pathlib import Path

from quorum.rules import Rule, load_rules, match_file, _matches, _expand_template


class TestLoadRules:
    def test_empty_config(self) -> None:
        rules = load_rules({})
        assert rules == []

    def test_load_rules(self) -> None:
        config = {"rules": [
            {"name": "invoices", "match": {"extension": [".pdf"]}, "action": {"move_to": "Invoices/"}, "priority": 10},
            {"name": "photos", "match": {"extension": [".jpg"]}, "action": {"move_to": "Photos/"}, "priority": 5},
        ]}
        rules = load_rules(config)
        assert len(rules) == 2
        assert rules[0].name == "invoices"  # higher priority first

    def test_disabled_rules(self) -> None:
        config = {"rules": [
            {"name": "test", "match": {}, "action": {}, "enabled": False},
        ]}
        rules = load_rules(config)
        assert rules[0].enabled is False


class TestMatches:
    def test_extension_match(self) -> None:
        assert _matches(Path("doc.pdf"), {"extension": [".pdf"]}, {}) is True
        assert _matches(Path("doc.txt"), {"extension": [".pdf"]}, {}) is False

    def test_extension_case_insensitive(self) -> None:
        assert _matches(Path("doc.PDF"), {"extension": [".pdf"]}, {}) is True

    def test_type_match(self) -> None:
        assert _matches(Path("a.mkv"), {"type": "video"}, {"type": "video"}) is True
        assert _matches(Path("a.mkv"), {"type": "photo"}, {"type": "video"}) is False

    def test_filename_regex(self) -> None:
        assert _matches(Path("invoice_2024.pdf"), {"filename_matches": r"invoice"}, {}) is True
        assert _matches(Path("report.pdf"), {"filename_matches": r"invoice"}, {}) is False

    def test_size_filters(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 100)
        assert _matches(f, {"size_gt": 50}, {}) is True
        assert _matches(f, {"size_gt": 200}, {}) is False
        assert _matches(f, {"size_lt": 200}, {}) is True
        assert _matches(f, {"size_lt": 50}, {}) is False

    def test_faces_match(self) -> None:
        ctx = {"faces": ["Sophia", "Max"]}
        assert _matches(Path("a.jpg"), {"faces": ["Sophia"]}, ctx) is True
        assert _matches(Path("a.jpg"), {"faces": ["Sophia", "Max"]}, ctx) is True
        assert _matches(Path("a.jpg"), {"faces": ["Unknown"]}, ctx) is False

    def test_scene_contains(self) -> None:
        ctx = {"scenes": ["beach", "sunset"]}
        assert _matches(Path("a.jpg"), {"scene_contains": "beach"}, ctx) is True
        assert _matches(Path("a.jpg"), {"scene_contains": "mountain"}, ctx) is False

    def test_multiple_conditions_all_must_match(self) -> None:
        ctx = {"type": "photo", "faces": ["Sophia"]}
        conditions = {"type": "photo", "faces": ["Sophia"], "extension": [".jpg"]}
        assert _matches(Path("a.jpg"), conditions, ctx) is True
        assert _matches(Path("a.png"), conditions, ctx) is False  # wrong extension


class TestExpandTemplate:
    def test_year_month(self) -> None:
        result = _expand_template("Archive/{year}/{month}/", Path("a.pdf"), {"year": "2024", "month": "06"})
        assert result == "Archive/2024/06/"

    def test_extension(self) -> None:
        result = _expand_template("files/{ext}", Path("doc.pdf"), {})
        assert result == "files/.pdf"

    def test_face_and_scene(self) -> None:
        ctx = {"faces": ["Sophia"], "scenes": ["beach"]}
        result = _expand_template("{face}/{scene}/", Path("a.jpg"), ctx)
        assert result == "Sophia/beach/"


class TestMatchFile:
    def test_matches_highest_priority(self) -> None:
        rules = [
            Rule(name="high", match={"extension": [".pdf"]}, action={"move_to": "High/"}, priority=10),
            Rule(name="low", match={"extension": [".pdf"]}, action={"move_to": "Low/"}, priority=1),
        ]
        result = match_file(Path("doc.pdf"), rules)
        assert result is not None
        assert result.rule.name == "high"
        assert result.dest_path == "High/"

    def test_no_match(self) -> None:
        rules = [Rule(name="pdfs", match={"extension": [".pdf"]}, action={"move_to": "PDFs/"}, priority=1)]
        assert match_file(Path("photo.jpg"), rules) is None

    def test_disabled_rule_skipped(self) -> None:
        rules = [Rule(name="test", match={"extension": [".pdf"]}, action={"move_to": "X/"}, enabled=False)]
        assert match_file(Path("doc.pdf"), rules) is None
