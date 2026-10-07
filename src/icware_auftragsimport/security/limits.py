"""Zentrale Ressourcenlimits für alle nicht vertrauenswürdigen Eingaben.

Jede Eingabe von außen (Mail, Anhang, Katalog, XML) wird vor der Verarbeitung gegen diese
Grenzen geprüft. Werte sind bewusst konservativ; Änderungen nur mit Sicherheitsreview.
"""

from __future__ import annotations

from dataclasses import dataclass

KIB = 1024
MIB = 1024 * KIB


@dataclass(frozen=True, slots=True)
class MailLimits:
    """Grenzen für eine einzelne Mail."""

    max_message_bytes: int = 25 * MIB
    max_header_block_bytes: int = 256 * KIB
    max_headers: int = 500
    max_header_value_chars: int = 2_000
    max_parts: int = 200
    max_depth: int = 10
    max_boundary_lines: int = 1_000
    max_text_chars: int = 500_000
    max_html_bytes: int = 2 * MIB
    max_attachments: int = 25
    max_attachment_bytes: int = 15 * MIB
    max_total_attachment_bytes: int = 40 * MIB
    max_filename_chars: int = 120


@dataclass(frozen=True, slots=True)
class AttachmentLimits:
    """Grenzen für die strukturelle Prüfung von Anhängen."""

    pdf_max_bytes: int = 15 * MIB
    pdf_max_pages: int = 500
    zip_max_entries: int = 2_000
    zip_max_uncompressed_bytes: int = 100 * MIB
    zip_max_entry_bytes: int = 50 * MIB
    zip_max_ratio: int = 100
    zip_ratio_threshold_bytes: int = 1 * MIB
    zip_max_name_chars: int = 255
    csv_max_bytes: int = 10 * MIB
    csv_max_rows: int = 100_000
    csv_max_columns: int = 200
    csv_max_field_chars: int = 10_000


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Grenzen für die regelbasierte Texterkennung (Schutz vor Regex-Überlast)."""

    max_text_chars: int = 200_000
    max_line_chars: int = 2_000


@dataclass(frozen=True, slots=True)
class FileLimits:
    """Grenzen für Dateien, die Administratoren einlesen (Katalog, Referenz-XML, Einstellungen)."""

    catalog_max_bytes: int = 20 * MIB
    catalog_max_rows: int = 200_000
    catalog_max_field_chars: int = 2_000
    json_max_depth: int = 32
    xml_max_bytes: int = 10 * MIB
    settings_max_bytes: int = 2 * MIB


MAIL = MailLimits()
ATTACHMENTS = AttachmentLimits()
PARSING = ParseLimits()
FILES = FileLimits()
