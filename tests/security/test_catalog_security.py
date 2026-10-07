"""Katalogimport gegen manipulierte Dateien: XML-Angriffe, ZIP-Bomben, Pfadausbruch, Grenzen."""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal

import pytest

from icware_auftragsimport.catalog import xlsx
from icware_auftragsimport.catalog.table import CatalogSourceError, read_table
from icware_auftragsimport.domain.models import Article


def _workbook(**replace: str) -> bytes:
    original = xlsx.write_xlsx("Artikel", ("Nr", "Name"), [["A1", "Eins"]])
    source = zipfile.ZipFile(io.BytesIO(original))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            target.writestr(name, replace.get(name, source.read(name).decode()))
    return buffer.getvalue()


def test_external_entity_in_sheet_is_rejected() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>&e;</t></is></c></row></sheetData></worksheet>'
    )
    with pytest.raises(CatalogSourceError, match="XML"):
        read_table(_workbook(**{"xl/worksheets/sheet1.xml": evil}), "evil.xlsx")


def test_billion_laughs_is_rejected() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>'
    )
    with pytest.raises(CatalogSourceError):
        read_table(_workbook(**{"xl/workbook.xml": bomb}), "bomb.xlsx")


def test_relationship_cannot_escape_the_archive_folder() -> None:
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="x" Target="../../etc/passwd"/></Relationships>'
    )
    with pytest.raises(CatalogSourceError, match="Verweis"):
        read_table(_workbook(**{"xl/_rels/workbook.xml.rels": rels}), "escape.xlsx")


def test_oversized_member_is_stopped_while_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [[f"A{i}", "x" * 50] for i in range(2000)]
    data = xlsx.write_xlsx("Artikel", ("Nr", "Name"), rows)
    monkeypatch.setattr(xlsx, "MAX_MEMBER_BYTES", 10_000)
    with pytest.raises(CatalogSourceError, match="zu groß"):
        read_table(data, "gross.xlsx")


def test_cells_beyond_excel_columns_are_rejected() -> None:
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="ZZZZ1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>'
    )
    with pytest.raises(CatalogSourceError, match="außerhalb"):
        read_table(_workbook(**{"xl/worksheets/sheet1.xml": sheet}), "spalte.xlsx")


def test_csv_with_too_many_columns_is_rejected() -> None:
    with pytest.raises(CatalogSourceError, match="Spalten"):
        read_table((";".join(["x"] * 300) + "\n").encode(), "breit.csv")


def test_nested_or_deep_json_is_rejected() -> None:
    with pytest.raises(CatalogSourceError, match="Verschachtelte"):
        read_table(json.dumps([{"number": "1", "name": {"x": 1}}]).encode(), "n.json")
    with pytest.raises(CatalogSourceError):
        read_table(("[" * 100_000 + "]" * 100_000).encode(), "tief.json")


def test_xlsx_export_writes_text_never_formulas() -> None:
    data = xlsx.write_xlsx("Artikel", ("Nr",), [['=HYPERLINK("http://x")'], [Decimal("1.5")]])
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet
    assert 't="inlineStr"' in sheet
    _, _, rows = xlsx.read_xlsx(data)
    assert rows[1][1][0].startswith("=HYPERLINK")


def test_control_characters_do_not_break_the_workbook() -> None:
    data = xlsx.write_xlsx("Artikel", ("Nr", "Name"), [["A\x00B", "Text\x0bmit Steuerzeichen"]])
    _, _, rows = xlsx.read_xlsx(data)
    assert rows[1][1] == ["AB", "Textmit Steuerzeichen"]
    assert Article
