"""Erzeugt Testplan und lesbare Spezifikation aus denselben Daten wie die Testdateien."""

from __future__ import annotations

from typing import Any

from .spec import ExportSpec

STATUS = "Exportadapter implementiert – Zielsystemvalidierung ausstehend"
BLANK = "&nbsp;" * 40


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_test_plan(overview: list[dict[str, Any]], spec: ExportSpec) -> str:
    """Testplan für die echte Windows-/Lexware-Testinstallation mit Protokollfeldern."""
    lines = [
        "# Lexware-Integrationstest",
        "",
        f"**Status: {STATUS}.** Bis dieser Test bestanden und protokolliert ist, darf das Produkt "
        "nicht als „Lexware-kompatibel“ bezeichnet werden. Auch danach gilt die Aussage nur für "
        "das getestete Lexware-Produkt in der getesteten Version.",
        "",
        "Diese Datei wird erzeugt (`python tools/lexware_testpaket.py`); die erwarteten Dateien "
        "liegen in `integration/lexware/cases/<Test>/`.",
        "",
        "## Voraussetzungen",
        "",
        "1. Windows-Rechner mit der Lexware-Version des Kunden; Datensicherung vor dem Test.",
        "2. Eigener **Testmandant**, niemals der Produktivmandant.",
        "3. Stammartikel anlegen: TEST-ART-1 (19 %, 10,00), TEST-ART-2 (19 %, 5,50), "
        "TEST-ART-3 (7 %, 24,90). TEST-ART-3 als Lagerartikel mit Bestand 5 (Test 10).",
        "4. Firmeneinstellung „Preise netto/brutto“ notieren (OQ-18).",
        "5. Import: eBusiness → Standard-Shopschnittstelle → Bestellungen importieren aus Datei "
        "(Menüpfad je Version prüfen und im Protokoll notieren).",
        "6. Je Test nur die genannten Dateien importieren; Ergebnis sofort protokollieren, "
        "Screenshots der Seiten „Kunde“, „Positionen“ und „Summe“ ablegen.",
        "",
        "## Protokollkopf",
        "",
        "| Angabe | Wert |",
        "|---|---|",
        *(
            f"| {item} | {BLANK} |"
            for item in (
                "Datum",
                "Tester",
                "Lexware-Produkt",
                "Version / Build",
                "Testmandant",
                "Firmeneinstellung Preise (netto/brutto)",
                "Windows-Version",
            )
        ),
        "| Exportadapter | lexware_opentrans 1.0.0 |",
        f"| Spezifikation | {spec.spec_id} {spec.version} (sha256 {spec.sha256[:12]}…) |",
        "",
        "## Testfälle",
        "",
    ]
    for case in overview:
        files = ", ".join(f"`{name}`" for name in case["files"]) or "–"
        product = (
            "Export blockiert: " + "; ".join(case["export_blocked_by_product"])
            if case["export_blocked_by_product"]
            else "Export zulässig"
        )
        notes = "; ".join(case["product_notes"]) or "–"
        data = case["input"]
        summary = (
            f"Beleg {data['document_number']}, Bestellnr. {data['customer_reference']}, "
            f"{data['lines']} Position(en), Zahlung {data['payment']}, "
            f"Rechnung: {data['invoice_address']}, "
            f"Lieferung {'wie Rechnung' if data['delivery_same_as_invoice'] else 'abweichend'}, "
            f"{case['encoding']}, Notation {case['notation']}"
        )
        lines += [
            f"### Test {case['test']}: {case['title']}",
            "",
            "| Feld | Inhalt |",
            "|---|---|",
            f"| Zweck | {_cell(case['purpose'])} |",
            f"| Input | {_cell(summary)} (Details: `input.json`) |",
            f"| Expected XML | {files} |",
            f"| Produktprüfung | {_cell(product)}; Hinweise: {_cell(notes)} |",
            f"| Vorgehen | {_cell(case['procedure'])} |",
            f"| Lexware result (erwartet) | {_cell(case['expectation'])} |",
            f"| Observed result | {BLANK} |",
            "| Pass/Fail | ☐ Pass ☐ Fail |",
            f"| Offene Fragen | {', '.join(case['open_questions']) or '–'} |",
            "",
        ]
    lines += [
        "## Zusammenfassung",
        "",
        "| Test | Pass/Fail | Bemerkung |",
        "|---|---|---|",
        *(f"| {case['test']} | | |" for case in overview),
        "",
        "## Freigabe",
        "",
        "Erst wenn alle Tests bestanden oder Abweichungen geklärt und im Adapter umgesetzt sind, "
        "trägt ein Administrator im Importprofil `target_system` (Produkt und Version) und "
        "`target_validated_on` (Datum) ein. Ohne diese Angaben verweigert die Software den "
        "Produktivexport; Vorschau, Probelauf und Testexport bleiben möglich.",
        "",
    ]
    return "\n".join(lines)


def render_spec_document(spec: ExportSpec, analysis: dict[str, Any]) -> str:
    """Lesbare Fassung der maschinenlesbaren Spezifikation samt Referenzanalyse."""
    raw = spec.raw
    lines = [
        "# Exportspezifikation Lexware openTRANS",
        "",
        f"**{spec.spec_id} {spec.version} – Status: {STATUS}.**",
        "",
        "Maßgeblich ist die maschinenlesbare Datei "
        "`src/icware_auftragsimport/export/lexware/lexware_opentrans_order_v1.json`. "
        "Diese Seite wird daraus erzeugt.",
        "",
        "## Quellen",
        "",
        *(
            f"- **{s['id']}** ({s['kind']}): {s.get('title', s.get('file', ''))}. {s['note']}"
            for s in raw["sources"]
        ),
        "",
        "## Dateiregeln",
        "",
        f"- Root `{raw['document']['root']}` ohne Namespace; `ORDER` mit Namespace "
        f"`{raw['document']['order_namespace']}` und `xmlns:xsi`.",
        f"- Encoding {' oder '.join(raw['document']['encodings'])}, Standard "
        f"{raw['document']['default_encoding']} ({'; '.join(raw['document']['encoding_basis'])}).",
        f"- Textwerte als {raw['document']['text_notation'].upper()} "
        f"({'; '.join(raw['document']['text_notation_basis'])}).",
        "- Zeilenende CRLF, Einrückung zwei Leerzeichen, ein Auftrag je Datei.",
        "- „€“ wird durch „EUR“ ersetzt (LX-SPEC 3.1.2); jede Ersetzung steht im Exportbericht.",
        "",
        "## Abbildung",
        "",
        "| Element | Quelle im Auftrag |",
        "|---|---|",
        *(f"| {_cell(k)} | {_cell(v)} |" for k, v in raw["mapping"].items()),
        "",
        "## Elementbaum",
        "",
    ]

    def walk(node: Any, depth: int) -> None:
        bounds = f"{node.min}..{'n' if node.max is None else node.max}"
        effect = f" – {node.effect}" if node.effect else ""
        lines.append(f"{'  ' * depth}- `{node.tag}` [{bounds}]{effect} ({', '.join(node.basis)})")
        for child in node.children:
            walk(child, depth + 1)

    walk(spec.tree, 0)
    lines += [
        "",
        "## Dokumentiert, aber bewusst nicht verwendet",
        "",
        *(
            f"- `{e['element']}` ({e['basis']}): {e['note']}"
            for e in raw["documented_but_not_used"]
        ),
        "",
        "## Offene Fragen",
        "",
        "| ID | Thema | Frage | Auswirkung | Klärung |",
        "|---|---|---|---|---|",
        *(
            f"| {q['id']} | {_cell(q['topic'])} | {_cell(q['question'])} | {_cell(q['impact'])} | "
            f"{_cell(q['resolution'])} |"
            for q in spec.open_questions
        ),
        "",
        "## Referenzanalyse 248090.xml",
        "",
        f"- sha256 `{analysis['sha256']}`, {analysis['size']} Byte, "
        f"Encoding {analysis['encoding']}, "
        f"Zeilenende {analysis['line_ending']}, {analysis['cdata_values']} CDATA-Werte.",
        f"- Namespaces: {', '.join(analysis['namespaces'])}.",
        f"- Parteien identisch: {'ja' if analysis['parties_identical'] else 'nein'} "
        "(Käufer = Rechnung = Lieferant; Rollen daher aus der Referenz allein "
        "nicht unterscheidbar).",
        f"- Reihenfolgen in ARTICLE_PRICE: {'; '.join(analysis['article_price_child_orders'])}.",
        *(f"- Auffälligkeit: {note}" for note in analysis["notes"]),
        *(f"- Abweichung von der Spezifikation: {item}" for item in analysis["conformance"]),
        "- Nur eine echte Datei liegt vor: Variabilität zwischen Bestellungen ist unbekannt. "
        "Mit weiteren Dateien `python tools/lexware_testpaket.py` erneut ausführen.",
        "",
    ]
    return "\n".join(lines)
