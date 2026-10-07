"""Minimaler, gehärteter XLSX-Leser und -Schreiber.

Bewusst ohne openpyxl (im Security-Pass entfernt): gelesen wird nur, was ein Katalog braucht
(Blattnamen, gemeinsame und eingebettete Texte, Zahlen, Wahrheitswerte), jeder ZIP-Eintrag mit
Größengrenze und jedes XML über den gehärteten Parser ohne DTD und Entitäten.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape, quoteattr

from ..ingest.attachments import read_zip_member
from ..security.limits import FILES
from ..security.safe_xml import UnsafeXmlError, parse_xml

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_MEMBERS = 2_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_COLUMNS = 200
MAX_EXCEL_COLUMN = 16_384
_CELL_REF = re.compile(r"([A-Z]+)(\d+)")
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


class XlsxError(ValueError):
    """Die Arbeitsmappe ist beschädigt, zu groß oder enthält Unerlaubtes."""


def _member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        archive.getinfo(name)
    except KeyError:
        return None
    try:
        return read_zip_member(archive, name, MAX_MEMBER_BYTES)
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        raise XlsxError(f"Eintrag {name} ist beschädigt oder zu groß") from exc


def _xml(archive: zipfile.ZipFile, name: str, *, required: bool = True) -> Element | None:
    data = _member(archive, name)
    if data is None:
        if required:
            raise XlsxError(f"Pflichtteil {name} fehlt; keine Excel-Arbeitsmappe")
        return None
    try:
        return parse_xml(data, max_bytes=MAX_MEMBER_BYTES)
    except (UnsafeXmlError, ValueError) as exc:
        raise XlsxError(f"{name}: unzulässiges oder fehlerhaftes XML") from exc


def _require(element: Element | None) -> Element:
    if element is None:
        raise XlsxError("Pflichtteil der Arbeitsmappe fehlt")
    return element


def _text(element: Element) -> str:
    """Text eines ``<si>``- oder ``<is>``-Elements inklusive Rich-Text-Runs."""
    parts = [node.text or "" for node in element.iter(f"{MAIN}t")]
    return "".join(parts)


def _column_index(letters: str) -> int:
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    if value > MAX_EXCEL_COLUMN:
        raise XlsxError(f"Spalte {letters} liegt außerhalb von Excel")
    return value - 1


def _number(raw: str) -> str:
    """Zahl in deutscher Schreibweise, ohne Gleitkomma-Artefakte (``12.499999999`` → ``12,5``)."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if not value.is_finite():
        return raw
    if value == value.to_integral_value():
        return str(int(value))
    rounded = round(value, 9).normalize()
    return format(rounded, "f").replace(".", ",")


def _open(data: bytes) -> zipfile.ZipFile:
    if len(data) > FILES.catalog_max_bytes:
        raise XlsxError(f"Datei größer als {FILES.catalog_max_bytes // (1024 * 1024)} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XlsxError("Keine gültige Excel-Datei (.xlsx)") from exc
    if len(archive.infolist()) > MAX_MEMBERS:
        raise XlsxError("Zu viele Einträge in der Arbeitsmappe")
    for info in archive.infolist():
        if info.flag_bits & 0x1:
            raise XlsxError("Verschlüsselte Arbeitsmappen werden nicht unterstützt")
    return archive


def _sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Blattnamen mit Pfad im Archiv, in Arbeitsmappen-Reihenfolge."""
    workbook = _require(_xml(archive, "xl/workbook.xml"))
    rels = _require(_xml(archive, "xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target", "") for rel in rels.iter(f"{PKG_REL}Relationship")}
    sheets = []
    for sheet in workbook.iter(f"{MAIN}sheet"):
        target = targets.get(sheet.get(f"{REL}id"), "")
        path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        if path.startswith("..") or not path.startswith("xl/"):
            raise XlsxError("Unzulässiger Verweis auf ein Tabellenblatt")
        sheets.append((str(sheet.get("name", "")), path))
    if not sheets:
        raise XlsxError("Die Arbeitsmappe enthält kein Tabellenblatt")
    return sheets


def sheet_names(data: bytes) -> list[str]:
    """Namen aller Tabellenblätter."""
    with _open(data) as archive:
        return [name for name, _ in _sheets(archive)]


def read_xlsx(
    data: bytes, sheet: str | None = None
) -> tuple[list[str], str, list[tuple[int, list[str]]]]:
    """Liest ein Blatt: (alle Blattnamen, gewähltes Blatt, [(Zeilennummer, Zellen)])."""
    with _open(data) as archive:
        sheets = _sheets(archive)
        names = [name for name, _ in sheets]
        chosen = next(((n, p) for n, p in sheets if n == sheet), sheets[0]) if sheet else sheets[0]
        shared_root = _xml(archive, "xl/sharedStrings.xml", required=False)
        shared = (
            [_text(si) for si in shared_root.iter(f"{MAIN}si")] if shared_root is not None else []
        )
        root = _require(_xml(archive, chosen[1]))
        rows: list[tuple[int, list[str]]] = []
        for row in root.iter(f"{MAIN}row"):
            if len(rows) >= FILES.catalog_max_rows:
                raise XlsxError(f"Mehr als {FILES.catalog_max_rows} Zeilen")
            number = int(row.get("r", len(rows) + 1))
            cells: dict[int, str] = {}
            for position, cell in enumerate(row.iter(f"{MAIN}c")):
                match = _CELL_REF.fullmatch(cell.get("r", ""))
                column = _column_index(match.group(1)) if match else position
                if column >= MAX_COLUMNS:
                    continue
                cells[column] = _cell_value(cell, shared)[: FILES.catalog_max_field_chars]
            width = max(cells, default=-1) + 1
            rows.append((number, [cells.get(i, "") for i in range(width)]))
        return names, chosen[0], rows


def _cell_value(cell: Element, shared: Sequence[str]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find(f"{MAIN}is")
        return _text(inline) if inline is not None else ""
    value = cell.find(f"{MAIN}v")
    raw = (value.text or "") if value is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError) as exc:
            raise XlsxError("Verweis auf einen fehlenden Text in der Arbeitsmappe") from exc
    if kind == "n":
        return _number(raw) if raw else ""
    return raw


def _clean(text: str) -> str:
    return _ILLEGAL_XML.sub("", text)


def _column_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
SHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml."
REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
Value = str | Decimal | None


def _cell(ref: str, value: Value, style: int) -> str:
    if value is None or value == "":
        return ""
    attr = f' s="{style}"' if style else ""
    if isinstance(value, Decimal):
        return f'<c r="{ref}"{attr}><v>{value}</v></c>'
    text = escape(_clean(value))
    return f'<c r="{ref}" t="inlineStr"{attr}><is><t xml:space="preserve">{text}</t></is></c>'


def _widths(headers: Sequence[str], rows: Sequence[Sequence[Value]]) -> str:
    cols = []
    for index, header in enumerate(headers):
        longest = max((len(str(r[index] or "")) for r in rows[:500] if index < len(r)), default=0)
        width = min(60, max(10, len(header) + 4, longest + 2))
        cols.append(f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>')
    return "".join(cols)


def _relationships(*items: tuple[str, str, str]) -> str:
    rels = "".join(
        f'<Relationship Id="{i}" Type="{REL_TYPE}{t}" Target="{target}"/>' for i, t, target in items
    )
    return f'{XML_HEAD}<Relationships xmlns="{PKG_REL[1:-1]}">{rels}</Relationships>'


def _content_types() -> str:
    overrides = (
        ("/xl/workbook.xml", "sheet.main+xml"),
        ("/xl/worksheets/sheet1.xml", "worksheet+xml"),
        ("/xl/styles.xml", "styles+xml"),
    )
    parts = "".join(f'<Override PartName="{n}" ContentType="{SHEET_CT}{t}"/>' for n, t in overrides)
    return (
        f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>{parts}</Types>'
    )


STYLES = (
    f'{XML_HEAD}<styleSheet xmlns="{MAIN[1:-1]}">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
    '<cellXfs count="2"><xf fontId="0"/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>'
)


def write_xlsx(sheet: str, headers: Sequence[str], rows: Sequence[Sequence[Value]]) -> bytes:
    """Arbeitsmappe mit einem Blatt; Texte als Text (keine Formeln möglich), Zahlen als Zahl.

    Kopfzeile fett, fixiert und mit Filter.
    """
    lines = []
    table: list[list[Value]] = [list(headers), *[list(r) for r in rows]]
    for row_index, values in enumerate(table, start=1):
        style = 1 if row_index == 1 else 0
        cells = "".join(
            _cell(f"{_column_letters(c)}{row_index}", v, style) for c, v in enumerate(values)
        )
        lines.append(f'<row r="{row_index}">{cells}</row>')
    last = f"{_column_letters(max(len(headers) - 1, 0))}{len(rows) + 1}"
    sheet_xml = (
        f'{XML_HEAD}<worksheet xmlns="{MAIN[1:-1]}"><sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        f"</sheetView></sheetViews><cols>{_widths(headers, rows)}</cols>"
        f'<sheetData>{"".join(lines)}</sheetData><autoFilter ref="A1:{last}"/></worksheet>'
    )
    name = quoteattr(_clean(sheet)[:31] or "Artikel")
    workbook = (
        f'{XML_HEAD}<workbook xmlns="{MAIN[1:-1]}" xmlns:r="{REL[1:-1]}">'
        f'<sheets><sheet name={name} sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    parts = {
        "[Content_Types].xml": _content_types(),
        "_rels/.rels": _relationships(("rId1", "officeDocument", "xl/workbook.xml")),
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": _relationships(
            ("rId1", "worksheet", "worksheets/sheet1.xml"), ("rId2", "styles", "styles.xml")
        ),
        "xl/styles.xml": STYLES,
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for part, content in parts.items():
            archive.writestr(part, content.encode("utf-8"))
    return buffer.getvalue()
