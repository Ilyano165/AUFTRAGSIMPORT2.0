"""Strukturelle Prüfung von Anhängen ohne Ausführen, Rendern oder Entpacken auf Platte.

Grundsätze: Typ aus Inhalt (Magic Bytes) und Endung; beide müssen passen. Ausführbare
Dateien, Makro-Dokumente, Archive und Dateien mit versteckten Zeichen im Namen werden nie
geöffnet. Alle Prüfungen arbeiten mit festen Ressourcengrenzen im Speicher.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from ..security.fs import file_extension, safe_filename
from ..security.limits import ATTACHMENTS, AttachmentLimits
from ..security.sanitize import has_hidden_characters


class AttachmentKind(StrEnum):
    """Erkannter Typ."""

    PDF = "pdf"
    SPREADSHEET = "xlsx"
    CSV = "csv"
    TEXT = "text"
    IMAGE = "image"
    LEGACY_OFFICE = "legacy_office"
    OFFICE_MACRO = "office_macro"
    ARCHIVE = "archive"
    EXECUTABLE = "executable"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    """Was mit dem Anhang geschehen darf."""

    ACCEPTED = "accepted"
    STORED = "stored"
    BLOCKED = "blocked"

    @property
    def label(self) -> str:
        """Anzeigename."""
        return {"accepted": "auswertbar", "stored": "nur abgelegt", "blocked": "gesperrt"}[
            self.value
        ]


EXECUTABLE_EXTENSIONS = frozenset(
    (
        "exe",
        "com",
        "bat",
        "cmd",
        "ps1",
        "psm1",
        "psd1",
        "vbs",
        "vbe",
        "js",
        "jse",
        "wsf",
        "wsh",
        "hta",
        "scr",
        "pif",
        "msi",
        "msp",
        "msix",
        "appx",
        "dll",
        "cpl",
        "lnk",
        "url",
        "jar",
        "reg",
        "iso",
        "img",
        "vhd",
        "vhdx",
        "chm",
        "sh",
        "py",
        "pyw",
        "apk",
        "app",
        "scf",
        "inf",
        "sys",
        "drv",
        "ocx",
        "gadget",
        "application",
        "settingcontent-ms",
        "one",
        "xll",
    )
)
MACRO_EXTENSIONS = frozenset(
    (
        "xlsm",
        "xltm",
        "xlam",
        "xlsb",
        "docm",
        "dotm",
        "pptm",
        "potm",
        "ppsm",
        "sldm",
    )
)
LEGACY_OFFICE_EXTENSIONS = frozenset(
    (
        "xls",
        "doc",
        "ppt",
        "xlt",
        "dot",
    )
)
ARCHIVE_EXTENSIONS = frozenset(
    (
        "zip",
        "rar",
        "7z",
        "gz",
        "tgz",
        "tar",
        "bz2",
        "xz",
        "cab",
        "arj",
        "lz",
        "lzh",
        "z",
        "zst",
    )
)
IMAGE_EXTENSIONS = frozenset(
    (
        "png",
        "jpg",
        "jpeg",
        "gif",
        "bmp",
        "tif",
        "tiff",
        "webp",
        "heic",
    )
)
TEXT_EXTENSIONS = frozenset({"txt"})
OOXML_EXTENSIONS = frozenset({"xlsx", "docx", "pptx"})
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "executable"),
    (b"\x7fELF", "executable"),
    (b"#!", "executable"),
    (b"\xca\xfe\xba\xbe", "executable"),
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"Rar!\x1a\x07", "archive"),
    (b"7z\xbc\xaf\x27\x1c", "archive"),
    (b"\x1f\x8b", "archive"),
    (b"MSCF", "archive"),
    (b"\x89PNG", "image"),
    (b"\xff\xd8\xff", "image"),
    (b"GIF8", "image"),
)
_PDF_DANGEROUS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/EmbeddedFiles",
    b"/RichMedia",
    b"/XFA",
    b"/SubmitForm",
    b"/ImportData",
    b"/GoToE",
)
_PDF_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_PAGE = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")
_NESTED_ARCHIVE = re.compile(r"\.(zip|jar|7z|rar|gz|cab|exe|dll|msi)$", re.IGNORECASE)
_FORMULA_START = ("=", "+", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class AttachmentReport:
    """Ergebnis der Prüfung."""

    safe_name: str
    extension: str
    kind: AttachmentKind
    verdict: Verdict
    size: int
    sha256: str
    reasons: tuple[str, ...]


def sniff(data: bytes) -> str | None:
    """Typ aus den ersten Bytes."""
    if b"%PDF-" in data[:1024]:
        return "pdf"
    return next((kind for magic, kind in _MAGIC if data.startswith(magic)), None)


def _report(name: str | None, data: bytes, kind: AttachmentKind, verdict: Verdict,
            *reasons: str) -> AttachmentReport:  # fmt: skip
    return AttachmentReport(
        safe_filename(name),
        file_extension(name),
        kind,
        verdict,
        len(data),
        hashlib.sha256(data).hexdigest(),
        tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class _Rule:
    applies: Callable[[str, str | None, str], bool]
    kind: AttachmentKind
    verdict: Verdict
    reason: str


def _mismatch(ext: str, magic: str | None) -> bool:
    expected = {"pdf": "pdf", "xlsx": "zip", "docx": "zip", "pptx": "zip"}.get(ext)
    unexpected = magic in ("pdf", "zip") and ext not in ("pdf", *OOXML_EXTENSIONS)
    return bool(expected and magic != expected) or unexpected


_RULES = (
    _Rule(
        lambda n, m, e: has_hidden_characters(n),
        AttachmentKind.UNKNOWN,
        Verdict.BLOCKED,
        "Dateiname enthält versteckte Steuer- oder Richtungszeichen",
    ),
    _Rule(
        lambda n, m, e: e in EXECUTABLE_EXTENSIONS or m == "executable",
        AttachmentKind.EXECUTABLE,
        Verdict.BLOCKED,
        "Ausführbare Datei oder Skript",
    ),
    _Rule(
        lambda n, m, e: e in MACRO_EXTENSIONS,
        AttachmentKind.OFFICE_MACRO,
        Verdict.BLOCKED,
        "Office-Dokument mit Makro-Unterstützung",
    ),
    _Rule(
        lambda n, m, e: (
            e in ARCHIVE_EXTENSIONS or m == "archive" or (m == "zip" and e not in OOXML_EXTENSIONS)
        ),
        AttachmentKind.ARCHIVE,
        Verdict.BLOCKED,
        "Archive werden nicht entpackt",
    ),
    _Rule(
        lambda n, m, e: e in LEGACY_OFFICE_EXTENSIONS or m == "ole",
        AttachmentKind.LEGACY_OFFICE,
        Verdict.STORED,
        "Altes Office-Format wird nicht ausgewertet",
    ),
    _Rule(
        lambda n, m, e: _mismatch(e, m),
        AttachmentKind.UNKNOWN,
        Verdict.BLOCKED,
        "Dateiendung passt nicht zum Inhalt",
    ),
    _Rule(
        lambda n, m, e: e in IMAGE_EXTENSIONS or m == "image",
        AttachmentKind.IMAGE,
        Verdict.STORED,
        "Bild wird nicht ausgewertet",
    ),
    _Rule(
        lambda n, m, e: e in TEXT_EXTENSIONS,
        AttachmentKind.TEXT,
        Verdict.STORED,
        "Textanhang wird nicht ausgewertet",
    ),
)


def inspect_attachment(
    name: str | None, data: bytes, limits: AttachmentLimits = ATTACHMENTS
) -> AttachmentReport:
    """Bewertet einen Anhang; öffnet ihn nie mit externen Programmen."""
    ext, magic = file_extension(name), sniff(data)
    for rule in _RULES[:6]:
        if rule.applies(name or "", magic, ext):
            return _report(name, data, rule.kind, rule.verdict, rule.reason)
    if magic == "pdf":
        return _pdf(name, data, limits)
    if ext in ("xlsx", "csv"):
        return (_ooxml if ext == "xlsx" else _csv)(name, data, limits)
    for rule in _RULES[6:]:
        if rule.applies(name or "", magic, ext):
            return _report(name, data, rule.kind, rule.verdict, rule.reason)
    return _report(
        name, data, AttachmentKind.UNKNOWN, Verdict.STORED, "Dateityp wird nicht ausgewertet"
    )


def _pdf(name: str | None, data: bytes, limits: AttachmentLimits) -> AttachmentReport:
    if len(data) > limits.pdf_max_bytes:
        return _report(name, data, AttachmentKind.PDF, Verdict.BLOCKED, "PDF zu groß")
    normalized = _PDF_NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), data)
    active = [
        m.decode() for m in _PDF_DANGEROUS if re.search(re.escape(m) + rb"(?![a-zA-Z])", normalized)
    ]
    if active:
        return _report(
            name,
            data,
            AttachmentKind.PDF,
            Verdict.BLOCKED,
            f"PDF mit aktiven Inhalten: {', '.join(active)}",
        )
    if b"/Encrypt" in normalized:
        return _report(name, data, AttachmentKind.PDF, Verdict.STORED, "PDF ist verschlüsselt")
    if len(_PDF_PAGE.findall(normalized)) > limits.pdf_max_pages:
        return _report(name, data, AttachmentKind.PDF, Verdict.BLOCKED, "PDF hat zu viele Seiten")
    if b"%%EOF" not in data[-2048:]:
        return _report(name, data, AttachmentKind.PDF, Verdict.STORED, "PDF ist unvollständig")
    return _report(name, data, AttachmentKind.PDF, Verdict.ACCEPTED)


def _zip_problem(info: zipfile.ZipInfo, seen: set[str], limits: AttachmentLimits) -> str | None:
    entry, lowered = info.filename, info.filename.lower()
    ratio = info.file_size / max(info.compress_size, 1)
    checks = (
        (
            len(entry) > limits.zip_max_name_chars or "\\" in entry or entry.startswith("/"),
            "unzulässiger Pfad im Archiv",
        ),
        (
            ".." in entry.split("/") or bool(re.match(r"^[A-Za-z]:", entry)),
            "Pfad verlässt das Archiv",
        ),
        (entry in seen, "doppelter Eintrag im Archiv"),
        (bool(info.flag_bits & 0x1), "verschlüsselter Eintrag"),
        (info.file_size > limits.zip_max_entry_bytes, "Eintrag entpackt zu groß"),
        (
            info.file_size > limits.zip_ratio_threshold_bytes and ratio > limits.zip_max_ratio,
            "auffällige Kompressionsrate (mögliche ZIP-Bombe)",
        ),
        (
            lowered.endswith("vbaproject.bin") or "/activex/" in lowered,
            "Makros oder ActiveX enthalten",
        ),
        (bool(_NESTED_ARCHIVE.search(lowered)), "verschachteltes Archiv oder Programm"),
    )
    return next((message for failed, message in checks if failed), None)


def _ooxml(name: str | None, data: bytes, limits: AttachmentLimits) -> AttachmentReport:
    kind = AttachmentKind.SPREADSHEET
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError):
        return _report(name, data, kind, Verdict.BLOCKED, "XLSX ist beschädigt")
    if len(infos) > limits.zip_max_entries:
        return _report(name, data, kind, Verdict.BLOCKED, "XLSX enthält zu viele Einträge")
    seen: set[str] = set()
    total = 0
    for info in infos:
        problem = _zip_problem(info, seen, limits)
        if problem:
            return _report(name, data, kind, Verdict.BLOCKED, problem)
        seen.add(info.filename)
        total += info.file_size
    if total > limits.zip_max_uncompressed_bytes:
        return _report(
            name, data, kind, Verdict.BLOCKED, "XLSX entpackt zu groß (mögliche ZIP-Bombe)"
        )
    if not {"[Content_Types].xml", "xl/workbook.xml"} <= seen:
        return _report(name, data, kind, Verdict.BLOCKED, "kein gültiges XLSX")
    notes = (
        ["enthält externe Verknüpfungen"]
        if any(n.startswith("xl/externalLinks/") for n in seen)
        else []
    )
    return _report(name, data, kind, Verdict.ACCEPTED, *notes)


def read_zip_member(archive: zipfile.ZipFile, member: str, max_bytes: int) -> bytes:
    """Liest einen Eintrag gestreamt und bricht ab, sobald ``max_bytes`` überschritten wird.

    Schützt auch bei gefälschten Größenangaben im Archivverzeichnis.
    """
    chunks: list[bytes] = []
    total = 0
    with archive.open(member) as stream:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Eintrag {member} überschreitet {max_bytes} Byte")
            chunks.append(chunk)
    return b"".join(chunks)


def _csv_rows(text: str, limits: AttachmentLimits) -> Iterator[list[str]]:
    first = text[: limits.csv_max_field_chars].split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    previous = csv.field_size_limit(limits.csv_max_field_chars)
    try:
        yield from csv.reader(io.StringIO(text), delimiter=delimiter)
    finally:
        csv.field_size_limit(previous)


def _csv(name: str | None, data: bytes, limits: AttachmentLimits) -> AttachmentReport:
    kind = AttachmentKind.CSV
    if len(data) > limits.csv_max_bytes:
        return _report(name, data, kind, Verdict.BLOCKED, "CSV zu groß")
    if b"\x00" in data:
        return _report(name, data, kind, Verdict.BLOCKED, "CSV enthält Nullbytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    rows = formulas = 0
    try:
        for row in _csv_rows(text, limits):
            rows += 1
            if rows > limits.csv_max_rows or len(row) > limits.csv_max_columns:
                return _report(
                    name, data, kind, Verdict.BLOCKED, "CSV überschreitet Zeilen- oder Spaltenlimit"
                )
            formulas += sum(1 for cell in row if cell.startswith(_FORMULA_START))
    except csv.Error:
        return _report(name, data, kind, Verdict.BLOCKED, "CSV-Feld zu lang oder fehlerhaft")
    notes = [f"{formulas} Zellen beginnen mit Formelzeichen"] if formulas else []
    return _report(name, data, kind, Verdict.ACCEPTED, *notes)
