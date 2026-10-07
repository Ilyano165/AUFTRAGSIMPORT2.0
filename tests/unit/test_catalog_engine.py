"""Katalog-Engine: Quellen, Zuordnung, Prüfung, atomarer Import, Backup, Versionen, Export."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from icware_auftragsimport.catalog import backup as backup_module
from icware_auftragsimport.catalog.backup import BackupError, CatalogBackups
from icware_auftragsimport.catalog.export import ExportFormat, neutralize, to_csv, to_xlsx
from icware_auftragsimport.catalog.mapping import (
    CatalogField,
    ColumnMapping,
    PriceBasis,
    suggest_mapping,
)
from icware_auftragsimport.catalog.model import IssueCode
from icware_auftragsimport.catalog.service import CatalogManager
from icware_auftragsimport.catalog.table import SourceKind, TableOptions, read_table
from icware_auftragsimport.catalog.xlsx import read_xlsx, sheet_names
from icware_auftragsimport.config.schema import ImportProfile, PriceMode
from icware_auftragsimport.domain.errors import CatalogError
from icware_auftragsimport.domain.findings import Severity
from icware_auftragsimport.domain.models import Article
from icware_auftragsimport.infrastructure import repositories
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database

NOW = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
PROFILE = ImportProfile(id="yt", name="Testprofil")
LEXWARE_ASCII = (
    "MW-0710;Mineralwasser still 12 × 0,7 l;Kiste;6,90;19 %\r\n"
    "KF-1000;Kaffee Crema 1 kg;Beutel;17,50;7 %\r\n"
    "ÖL-200;Rapsöl 5 l;Kanister;1.234,50;19 %\r\n"
).encode("cp1252")
LEXWARE_OFFICE = (
    "\ufeffArtikelnummer;Bezeichnung;Einheit;Steuerart;Verkaufspreis brutto;GTIN;Aktiv\n"
    "MW-0710;Mineralwasser still 12 × 0,7 l;Kiste;19%;8,21;4001234567890;ja\n"
    "KF-1000;Kaffee Crema 1 kg;Beutel;7%;18,73;;ja\n"
    "AS-1000;Apfelschorle 12 × 1,0 l;Kiste;19%;13,57;4001234567890;nein\n"
).encode()


@pytest.fixture
def manager(tmp_path: Path) -> CatalogManager:
    database = Database(tmp_path / "t.db")
    conn = database.connect()
    database.migrate(conn)
    return CatalogManager(conn, FixedClock(NOW), CatalogBackups(tmp_path / "backups"))


def _ascii_mapping(headers: tuple[str, ...]) -> ColumnMapping:
    mapping = suggest_mapping(headers)
    for target, index in (
        (CatalogField.NUMBER, 0),
        (CatalogField.NAME, 1),
        (CatalogField.UNIT, 2),
        (CatalogField.PRICE, 3),
        (CatalogField.TAX_RATE, 4),
    ):
        mapping = mapping.with_columns(target, [index])
    return mapping


def _import_ascii(manager: CatalogManager) -> int:
    table = read_table(LEXWARE_ASCII, "artikel.txt", TableOptions(has_header=False))
    return manager.commit(
        PROFILE, manager.preview(PROFILE, table, _ascii_mapping(table.headers))
    ).number


def test_lexware_ascii_export_without_header_in_windows_1252(manager: CatalogManager) -> None:
    table = read_table(LEXWARE_ASCII, "artikel.txt", TableOptions(has_header=False))
    assert table.encoding.startswith("Windows-1252")
    assert table.delimiter == ";"
    assert table.headers == ("Spalte 1", "Spalte 2", "Spalte 3", "Spalte 4", "Spalte 5")
    preview = manager.preview(PROFILE, table, _ascii_mapping(table.headers))
    assert not preview.errors
    oil = next(a for a in preview.articles if a.number == "ÖL-200")
    assert (oil.name, oil.price, oil.tax_rate) == ("Rapsöl 5 l", Decimal("1234.50"), Decimal(19))
    assert preview.changes.first_import


def test_lexware_office_columns_gross_prices_and_gtin_alias(manager: CatalogManager) -> None:
    table = read_table(LEXWARE_OFFICE, "lexware-office.csv")
    mapping = manager.suggest("yt", table)
    named = {f: [table.headers[i] for i in idx] for f, idx in mapping.columns.items()}
    assert named[CatalogField.PRICE] == ["Verkaufspreis brutto"]
    assert named[CatalogField.TAX_RATE] == ["Steuerart"]
    assert named[CatalogField.ALIASES] == ["GTIN"]
    assert mapping.price_basis is PriceBasis.GROSS
    preview = manager.preview(PROFILE, table, mapping)
    water = preview.articles[0]
    assert water.price == Decimal("6.8992")
    assert water.aliases == ("4001234567890",)
    assert not preview.articles[2].active
    assert [i.code for i in preview.warnings] == [IssueCode.ALIAS_DUPLICATE]


def test_gross_profile_converts_net_source(manager: CatalogManager) -> None:
    gross = ImportProfile(id="b", name="Brutto", price_mode=PriceMode.GROSS)
    table = read_table(b"Artikelnummer;Bezeichnung;Preis;MwSt\nA1;Artikel;10,00;19\n", "n.csv")
    assert manager.preview(gross, table, manager.suggest("b", table)).articles[0].price == Decimal(
        "11.9"
    )


def test_errors_block_import_and_keep_active_catalog(manager: CatalogManager) -> None:
    version = _import_ascii(manager)
    bad = (
        "Artikelnummer;Bezeichnung;Preis;MwSt;Aktiv\nX1;A;12,5x;19;ja\nX1;B;3;19;ja\n"
        ";C;4;19;ja\nX2;D;-1;16;vielleicht\n"
    )
    table = read_table(bad.encode(), "kaputt.csv")
    preview = manager.preview(PROFILE, table, manager.suggest("yt", table))
    codes = {i.code for i in preview.errors}
    assert {
        IssueCode.PRICE_INVALID,
        IssueCode.NUMBER_DUPLICATE,
        IssueCode.NUMBER_MISSING,
        IssueCode.TAX_NOT_ALLOWED,
        IssueCode.ACTIVE_INVALID,
    } <= codes
    assert not preview.can_import
    with pytest.raises(CatalogError) as info:
        manager.commit(PROFILE, preview)
    assert "unverändert" in info.value.message.unchanged
    active, articles = manager.active("yt")
    assert active is not None
    assert active.number == version
    assert len(articles) == 3


def test_failure_inside_transaction_leaves_no_half_version(
    manager: CatalogManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _import_ascii(manager)
    table = read_table(LEXWARE_OFFICE, "x.csv")
    preview = manager.preview(PROFILE, table, manager.suggest("yt", table))

    def broken(*_args: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repositories.CatalogRepository, "activate", broken)
    with pytest.raises(sqlite3.OperationalError):
        manager.commit(PROFILE, preview)
    monkeypatch.undo()
    assert [v.number for v in manager.versions("yt")] == [1]
    assert len(manager.active("yt")[1]) == 3


def test_backup_before_import_versions_rollback_and_restore(manager: CatalogManager) -> None:
    _import_ascii(manager)
    table = read_table(LEXWARE_OFFICE, "lexware-office.csv")
    outcome = manager.commit(
        PROFILE, manager.preview(PROFILE, table, manager.suggest("yt", table)), "Preise"
    )
    assert outcome.number == 2
    assert outcome.backup is not None
    assert "katalog-yt-v0001" in outcome.backup.name
    assert outcome.changes.summary == "+1 neu · −1 entfernt · 2 geändert (2 Preise) · 0 gleich"
    newest = manager.versions("yt")[0]
    assert (newest.source_kind, newest.note, newest.changes["price_changes"]) == (
        "csv",
        "Preise",
        2,
    )
    assert newest.mapping["price_basis"] == "gross"
    first = manager.versions("yt")[1]
    assert manager.activate(PROFILE, first.version) is not None
    assert manager.active("yt")[0].number == 1  # type: ignore[union-attr]
    entry = next(e for e in manager.backups.entries("yt") if e.catalog_version == 2)
    restored = manager.commit(
        PROFILE, manager.restore(PROFILE, entry.path), "Wiederherstellung aus Backup"
    )
    assert restored.number == 3
    assert manager.versions("yt")[0].source_kind == "backup"
    assert {a.number for a in manager.active("yt")[1]} == {"MW-0710", "KF-1000", "AS-1000"}


def test_backup_tampering_and_foreign_profile_are_rejected(manager: CatalogManager) -> None:
    _import_ascii(manager)
    path = manager.backup_now(PROFILE)
    path.write_text(path.read_text(encoding="utf-8").replace("Kaffee", "Tee"), encoding="utf-8")
    assert not manager.backups.entries("yt")[0].intact
    with pytest.raises(BackupError, match="Prüfsumme"):
        manager.restore(PROFILE, path)
    with pytest.raises(BackupError, match="Backup-Ordner"):
        manager.restore(ImportProfile(id="andere", name="Andere"), path)


def test_backups_never_collide_and_are_pruned(
    manager: CatalogManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _import_ascii(manager)
    monkeypatch.setattr(backup_module, "KEEP_PER_PROFILE", 3)
    created = [manager.backup_now(PROFILE) for _ in range(5)]
    assert created[-1].exists()
    entries = manager.backups.entries("yt")
    assert len(entries) == 3
    assert all(e.intact for e in entries)


def test_xlsx_round_trip_is_lossless(manager: CatalogManager) -> None:
    _import_ascii(manager)
    data = manager.export(PROFILE, ExportFormat.XLSX)
    assert sheet_names(data) == ["Artikel"]
    table = read_table(data, "export.xlsx")
    assert table.kind is SourceKind.XLSX
    preview = manager.preview(PROFILE, table, manager.suggest("yt", table))
    assert preview.changes.summary == "+0 neu · −0 entfernt · 0 geändert · 3 gleich"
    _, _, rows = read_xlsx(to_xlsx([Article("A", "x", (), "", Decimal(19), Decimal("6.9"))]))
    assert rows[1][1][3] == "6,9"


def test_csv_export_neutralizes_formulas_and_reimports_original() -> None:
    dangerous = ['=HYPERLINK("x")', "+cmd", "-1+1", "@SUM(A1)", "\tx", "＝1"]
    assert all(neutralize(text).startswith("'") for text in dangerous)
    data = to_csv([Article(text, "Name") for text in dangerous])
    lines = data.decode("utf-8-sig").splitlines()[1:]
    assert all(line.lstrip('"').startswith("'") for line in lines)
    table = read_table(data, "rück.csv")
    assert [row[0] for _, row in table.rows][:4] == dangerous[:4]


def test_json_formats_and_saved_mapping_reuse(manager: CatalogManager) -> None:
    plain = json.dumps(
        [{"number": "J1", "name": "Json", "aliases": ["a", "b"], "price": "1,50"}]
    ).encode()
    table = read_table(plain, "k.json")
    preview = manager.preview(PROFILE, table, manager.suggest("yt", table))
    assert preview.articles[0].aliases == ("a", "b")
    manager.commit(PROFILE, preview)
    custom = read_table(b"Nr.;Text;VK\nC1;Eins;1\n", "eigen.csv")
    mapping = ColumnMapping(
        {CatalogField.NUMBER: (0,), CatalogField.NAME: (1,), CatalogField.PRICE: (2,)}
    )
    manager.commit(PROFILE, manager.preview(PROFILE, custom, mapping))
    again = read_table(b"Nr.;Text;VK\nC2;Zwei;2\n", "eigen2.csv")
    assert manager.suggest("yt", again).columns == mapping.columns


def test_version_numbers_count_per_profile(manager: CatalogManager) -> None:
    _import_ascii(manager)
    _import_ascii(manager)
    other = ImportProfile(id="b", name="B")
    table = read_table(LEXWARE_ASCII, "b.txt", TableOptions(has_header=False))
    assert (
        manager.commit(other, manager.preview(other, table, _ascii_mapping(table.headers))).number
        == 1
    )
    assert [v.number for v in manager.versions("yt")] == [2, 1]


def test_validate_active_uses_current_profile_tax_rates(manager: CatalogManager) -> None:
    _import_ascii(manager)
    stricter = ImportProfile(id="yt", name="Testprofil", tax_rates=(Decimal(19),))
    issues = manager.validate_active(stricter)
    assert [i.code for i in issues if i.severity is Severity.ERROR] == [IssueCode.TAX_NOT_ALLOWED]


def test_old_excel_format_is_refused_with_advice() -> None:
    with pytest.raises(ValueError, match="xlsx"):
        read_table(b"\xd0\xcf\x11\xe0", "alt.xls")


def test_zip_member_is_not_trusted_as_xlsx_when_not_a_workbook() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "kein Excel")
    with pytest.raises(ValueError, match="Pflichtteil"):
        read_table(buffer.getvalue(), "fake.xlsx")
