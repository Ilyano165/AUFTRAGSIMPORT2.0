"""Golden-Test: Erwartete XML-Dateien und Dokumente passen zu Adapter und Spezifikation."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "lexware_testpaket", ROOT / "tools" / "lexware_testpaket.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(base: Path, sub: str) -> dict[str, bytes]:
    folder = base / sub
    return {
        str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()
    }


def test_committed_artifacts_are_up_to_date(tmp_path: Path) -> None:
    reference = tmp_path / "integration" / "lexware" / "reference"
    reference.mkdir(parents=True)
    shutil.copy(ROOT / "integration" / "lexware" / "reference" / "248090.xml", reference)
    _load_tool().generate(tmp_path)
    for sub in ("integration/lexware/cases", "docs/lexware"):
        assert _tree(tmp_path, sub) == _tree(ROOT, sub), (
            f"{sub} veraltet: tools/lexware_testpaket.py ausführen"
        )


def test_plan_covers_required_tests_with_protocol_fields() -> None:
    plan = (ROOT / "docs" / "lexware" / "integrationstest.md").read_text(encoding="utf-8")
    for number in ("01", "02", "03", "04", "05", "06", "07", "08a", "08e", "09", "10"):
        assert f"### Test {number}:" in plan
    fields = (
        "| Input |",
        "| Expected XML |",
        "| Lexware result (erwartet) |",
        "| Observed result |",
    )
    for field in (*fields, "| Pass/Fail | ☐"):
        assert plan.count(field) == 16
    assert "Lexware-kompatibel“ bezeichnet werden" in plan


def test_reference_analysis_records_key_observations() -> None:
    data = json.loads(
        (ROOT / "docs" / "lexware" / "referenzanalyse.json").read_text(encoding="utf-8")
    )
    analysis = data["248090.xml"]
    assert analysis["encoding"] == "ISO-8859-1" and analysis["line_ending"] == "CRLF"
    assert analysis["parties_identical"] is True
    assert len(analysis["article_price_child_orders"]) == 2
    assert any("XXXX" in note for note in analysis["notes"])
    assert any("Kürzung" in note for note in analysis["notes"])
