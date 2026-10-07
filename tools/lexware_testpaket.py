"""Erzeugt Integrationstest-Paket, Testplan, Spezifikationsdoku und Referenzanalyse.

Aufruf im Projektverzeichnis: ``python tools/lexware_testpaket.py``
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from icware_auftragsimport.config.schema import ImportProfile  # noqa: E402
from icware_auftragsimport.domain.models import Address  # noqa: E402
from icware_auftragsimport.export.lexware.documentation import (  # noqa: E402
    render_spec_document,
    render_test_plan,
)
from icware_auftragsimport.export.lexware.integration_cases import write_package  # noqa: E402
from icware_auftragsimport.export.lexware.reference_analysis import analyze_reference  # noqa: E402
from icware_auftragsimport.export.lexware.spec import load_spec  # noqa: E402

TEST_SUPPLIER = Address(
    company="IC-Ware Testlieferant",
    street="Testweg",
    house_number="1",
    postal_code="51570",
    city="Windeck",
    country="DE",
)


def generate(root: Path) -> None:
    """Schreibt alle erzeugten Artefakte unterhalb von ``root``."""
    spec = load_spec()
    profile = replace(
        ImportProfile(id="integrationstest", name="Integrationstest"), supplier=TEST_SUPPLIER
    )
    cases = root / "integration" / "lexware" / "cases"
    if cases.exists():
        shutil.rmtree(cases)
    overview = write_package(cases, profile)
    docs = root / "docs" / "lexware"
    docs.mkdir(parents=True, exist_ok=True)
    reference = root / "integration" / "lexware" / "reference" / "248090.xml"
    analysis = analyze_reference(reference.read_bytes(), spec)
    (docs / "referenzanalyse.json").write_text(
        json.dumps({"248090.xml": analysis}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (docs / "integrationstest.md").write_text(render_test_plan(overview, spec), encoding="utf-8")
    (docs / "export-spezifikation.md").write_text(
        render_spec_document(spec, analysis), encoding="utf-8"
    )


if __name__ == "__main__":
    generate(ROOT)
    print("Integrationstest-Paket und Dokumentation erzeugt.")
