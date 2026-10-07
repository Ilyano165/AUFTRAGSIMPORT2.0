"""Anhänge: PDF, XLSX, CSV, ZIP-basierte Formate, Programme und Tarnungen."""

from __future__ import annotations

import io
import warnings
import zipfile

import pytest

from icware_auftragsimport.ingest.attachments import (
    AttachmentKind,
    Verdict,
    inspect_attachment,
    read_zip_member,
)
from icware_auftragsimport.security.limits import AttachmentLimits

PDF = (
    b"%PDF-1.7\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
)


def xlsx(extra: dict[str, bytes] | None = None, encrypted: str = "") -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore")
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
        if encrypted:
            archive.writestr(encrypted, b"geheim")
    data = bytearray(buffer.getvalue())
    if encrypted:
        name = data.rindex(encrypted.encode())
        header = name - 46
        assert data[header : header + 4] == b"PK\x01\x02"
        data[header + 8] |= 0x1
    return bytes(data)


def test_regular_pdf_and_xlsx_are_accepted() -> None:
    assert inspect_attachment("bestellung.pdf", PDF).verdict is Verdict.ACCEPTED
    assert inspect_attachment("bestellung.xlsx", xlsx()).verdict is Verdict.ACCEPTED


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("bestellung.pdf", b"MZ\x90\x00programm", AttachmentKind.EXECUTABLE),
        ("bestellung.pdf.exe", PDF, AttachmentKind.EXECUTABLE),
        ("skript.js", b"var x", AttachmentKind.EXECUTABLE),
        ("liste.xlsm", xlsx(), AttachmentKind.OFFICE_MACRO),
        ("archiv.zip", xlsx(), AttachmentKind.ARCHIVE),
        ("tarnung.pdf", xlsx(), AttachmentKind.ARCHIVE),
        ("tarnung.xlsx", PDF, AttachmentKind.UNKNOWN),
        (
            "doc.pdf",
            PDF.replace(
                b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (x) >>"
            ),
            AttachmentKind.PDF,
        ),
        (
            "doc.pdf",
            PDF.replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /J#61vaScript >>"),
            AttachmentKind.PDF,
        ),
        (
            "doc.pdf",
            PDF.replace(b"/Type /Catalog", b"/Type /Catalog /Names << /EmbeddedFiles 4 0 R >>"),
            AttachmentKind.PDF,
        ),
        (
            "bombe.xlsx",
            xlsx({"xl/worksheets/sheet1.xml": b"0" * 3_000_000}),
            AttachmentKind.SPREADSHEET,
        ),
        ("pfad.xlsx", xlsx({"../../evil.txt": b"x"}), AttachmentKind.SPREADSHEET),
        ("makro.xlsx", xlsx({"xl/vbaProject.bin": b"x"}), AttachmentKind.SPREADSHEET),
        ("innen.xlsx", xlsx({"xl/embeddings/innen.zip": b"x"}), AttachmentKind.SPREADSHEET),
        ("doppelt.xlsx", xlsx({"xl/workbook.xml": b"<x/>"}), AttachmentKind.SPREADSHEET),
        ("krypto.xlsx", xlsx(encrypted="xl/secret.xml"), AttachmentKind.SPREADSHEET),
        ("kaputt.xlsx", b"PK\x03\x04kaputt", AttachmentKind.SPREADSHEET),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_dangerous_attachments_are_blocked(name: str, data: bytes, kind: AttachmentKind) -> None:
    report = inspect_attachment(name, data)
    assert (report.kind, report.verdict) == (kind, Verdict.BLOCKED), report.reasons


def test_limits_for_pdf_pages_and_zip_entries() -> None:
    pages = PDF + b"4 0 obj << /Type /Page >> endobj\n5 0 obj << /Type /Page >> endobj\n%%EOF"
    assert (
        inspect_attachment("a.pdf", pages, AttachmentLimits(pdf_max_pages=2)).verdict
        is Verdict.BLOCKED
    )
    many = xlsx({f"xl/p{i}.xml": b"x" for i in range(30)})
    assert (
        inspect_attachment("a.xlsx", many, AttachmentLimits(zip_max_entries=10)).verdict
        is Verdict.BLOCKED
    )


def test_streaming_read_stops_at_limit_even_with_forged_sizes() -> None:
    with zipfile.ZipFile(io.BytesIO(xlsx({"xl/big.xml": b"1" * 100_000}))) as archive:
        with pytest.raises(ValueError, match="überschreitet"):
            read_zip_member(archive, "xl/big.xml", 10_000)
        assert read_zip_member(archive, "xl/workbook.xml", 10_000) == b"<workbook/>"


@pytest.mark.parametrize(
    ("data", "limits", "verdict"),
    [
        (b"Artikel;Menge\nYT11;5\n", AttachmentLimits(), Verdict.ACCEPTED),
        (b"a;b\x00c\n", AttachmentLimits(), Verdict.BLOCKED),
        (b"a;" + b"x" * 200 + b"\n", AttachmentLimits(csv_max_field_chars=100), Verdict.BLOCKED),
        (b"a;b\n" * 50, AttachmentLimits(csv_max_rows=10), Verdict.BLOCKED),
        (b"x" * 2000, AttachmentLimits(csv_max_bytes=1000), Verdict.BLOCKED),
    ],
)
def test_csv_limits(data: bytes, limits: AttachmentLimits, verdict: Verdict) -> None:
    assert inspect_attachment("liste.csv", data, limits).verdict is verdict


def test_csv_formula_cells_are_reported() -> None:
    report = inspect_attachment("liste.csv", b'Artikel;Menge\n=HYPERLINK("http://x");5\n')
    assert report.verdict is Verdict.ACCEPTED and "Formelzeichen" in report.reasons[0]
