"""Katalogverwaltung: Vorschau, atomarer Import mit Backup, Versionen, Rücksprung, Export."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..config.schema import ImportProfile, PriceMode
from ..domain.errors import CatalogError, UserMessage
from ..domain.findings import Severity
from ..domain.models import Article
from ..domain.ports import Clock
from ..infrastructure.db import transaction
from ..infrastructure.repositories import CatalogRepository, CatalogVersion, JournalRepository
from ..services.matching import Catalog
from .backup import BackupError, CatalogBackups
from .diff import CatalogChanges, compare
from .export import ExportFormat, to_csv, to_json, to_xlsx
from .mapping import ColumnMapping, PriceBasis, identity_mapping, map_rows, suggest_mapping
from .model import CatalogIssue, IssueCode, MappedRow
from .table import RawTable, TableOptions, read_table
from .validation import validate_rows

MAX_ERRORS_IN_MESSAGE = 8


@dataclass(frozen=True, slots=True)
class ImportPreview:
    """Ergebnis von Lesen, Zuordnen und Prüfen, noch ohne Speicherung."""

    profile_id: str
    table: RawTable
    mapping: ColumnMapping
    rows: tuple[MappedRow, ...]
    issues: tuple[CatalogIssue, ...]
    changes: CatalogChanges
    based_on: int | None

    @property
    def errors(self) -> tuple[CatalogIssue, ...]:
        """Blockierende Befunde."""
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[CatalogIssue, ...]:
        """Hinweise, die den Import nicht verhindern."""
        return tuple(i for i in self.issues if i.severity is Severity.WARNING)

    @property
    def articles(self) -> tuple[Article, ...]:
        """Gelesene Artikel."""
        return tuple(r.article for r in self.rows)

    @property
    def can_import(self) -> bool:
        """Importierbar ohne Fehler und mit mindestens einem Artikel."""
        return not self.errors and bool(self.rows)


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    """Gespeicherte Fassung; ``number`` zählt je Profil ab 1, ``version`` ist die interne ID."""

    version: int
    number: int
    backup: Path | None
    changes: CatalogChanges


def _basis(profile: ImportProfile) -> PriceBasis:
    return PriceBasis.GROSS if profile.price_mode is PriceMode.GROSS else PriceBasis.NET


def _refused(what: str, problems: Sequence[str], action: str) -> CatalogError:
    shown = list(problems[:MAX_ERRORS_IN_MESSAGE])
    if len(problems) > MAX_ERRORS_IN_MESSAGE:
        shown.append(f"… und {len(problems) - MAX_ERRORS_IN_MESSAGE} weitere")
    return CatalogError(
        "CATALOG_INVALID",
        UserMessage(
            what=what,
            why="; ".join(shown),
            unchanged="Der bisher aktive Katalog bleibt unverändert in Gebrauch",
            action=action,
        ),
        details=tuple(problems),
    )


class CatalogManager:
    """Katalog eines Profils verwalten; nie wird ein fehlerhafter Stand aktiv."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock, backups: CatalogBackups) -> None:
        self._conn = conn
        self._clock = clock
        self.backups = backups

    def _repo(self) -> CatalogRepository:
        return CatalogRepository(self._conn)

    def versions(self, profile_id: str) -> list[CatalogVersion]:
        """Fassungen, neueste zuerst."""
        return self._repo().versions(profile_id)

    def active(self, profile_id: str) -> tuple[CatalogVersion | None, list[Article]]:
        """Aktive Fassung mit Artikeln."""
        version = next((v for v in self.versions(profile_id) if v.active), None)
        return version, (self._repo().articles(version.version) if version else [])

    def catalog(self, profile_id: str) -> tuple[int | None, Catalog]:
        """Aktiver Katalog mit seiner Versionsnummer im Profil."""
        version, articles = self.active(profile_id)
        return (version.number if version else None), Catalog(articles)

    def read(self, data: bytes, name: str, options: TableOptions = TableOptions()) -> RawTable:  # noqa: B008
        """Liest eine Quelle (``CatalogSourceError`` bei unlesbaren Dateien)."""
        return read_table(data, name, options)

    def suggest(self, profile_id: str, table: RawTable) -> ColumnMapping:
        """Zuordnung: zuletzt verwendete, sonst nach bekannten Spaltennamen."""
        previous = next(
            (v.mapping for v in self.versions(profile_id) if v.mapping.get("columns")), None
        )
        return suggest_mapping(table.headers, previous)

    def preview(
        self, profile: ImportProfile, table: RawTable, mapping: ColumnMapping
    ) -> ImportPreview:
        """Liest über die Zuordnung, prüft alles und vergleicht mit der aktiven Fassung."""
        rows = map_rows(table, mapping, target_basis=_basis(profile), tax_rates=profile.tax_rates)
        issues = validate_rows(rows, mapping)
        if not rows:
            issues.insert(
                0,
                CatalogIssue(
                    Severity.ERROR, IssueCode.NUMBER_MISSING, "Die Quelle enthält keine Artikel"
                ),
            )
        version, current = self.active(profile.id)
        changes = compare(current if version else None, [r.article for r in rows])
        return ImportPreview(
            profile.id,
            table,
            mapping,
            tuple(rows),
            tuple(issues),
            changes,
            version.version if version else None,
        )

    def commit(
        self, profile: ImportProfile, preview: ImportPreview, note: str = ""
    ) -> ImportOutcome:
        """Speichert und aktiviert die neue Fassung.

        Reihenfolge: Prüfen → Backup der aktiven Fassung → eine Transaktion für Fassung,
        Artikel, Aktivierung und Protokoll. Scheitert ein Schritt, bleibt der aktive Katalog.
        """
        if preview.profile_id != profile.id:
            raise _refused(
                "Der Katalog wurde nicht importiert",
                ["Vorschau gehört zu einem anderen Profil"],
                "Den Import im richtigen Profil wiederholen",
            )
        if not preview.can_import:
            raise _refused(
                "Der Artikelkatalog wurde nicht importiert",
                [i.render() for i in preview.errors],
                "Die Datei korrigieren oder die Spaltenzuordnung anpassen und erneut prüfen",
            )
        version, current = self.active(profile.id)
        if (version.version if version else None) != preview.based_on:
            raise _refused(
                "Der Artikelkatalog wurde nicht importiert",
                ["Der aktive Katalog wurde seit der Prüfung geändert"],
                "Die Prüfung erneut ausführen",
            )
        backup = self._backup(profile, version, current, "vor Import")
        source = "backup" if note.startswith("Wiederherstellung") else preview.table.kind.value
        now = self._clock.now()
        with transaction(self._conn):
            number = self._repo().add_version(
                profile.id,
                preview.table.source_name,
                preview.table.sha256,
                preview.articles,
                now,
                source_kind=source,
                mapping=preview.mapping.to_json(preview.table.headers),
                changes=preview.changes.to_json(),
                based_on=preview.based_on,
                note=note,
                backup_file=backup.name if backup else "",
            )
            self._repo().activate(profile.id, number)
            JournalRepository(self._conn).append(
                "catalog",
                profile.id,
                "catalog_imported",
                now,
                {
                    "version": number,
                    "source": preview.table.source_name,
                    "articles": len(preview.rows),
                    "changes": preview.changes.summary,
                    "backup": backup.name if backup else "",
                },
            )
        stored = next(v for v in self.versions(profile.id) if v.version == number)
        return ImportOutcome(number, stored.number, backup, preview.changes)

    def _backup(
        self,
        profile: ImportProfile,
        version: CatalogVersion | None,
        articles: list[Article],
        reason: str,
    ) -> Path | None:
        if version is None:
            return None
        try:
            return self.backups.create(
                articles,
                profile_id=profile.id,
                profile_name=profile.name,
                version=version.number,
                now=self._clock.now(),
                reason=reason,
            )
        except OSError as exc:
            raise _refused(
                "Der Katalog wurde nicht geändert",
                [f"Backup konnte nicht geschrieben werden ({exc.strerror})"],
                "Speicherplatz und Schreibrechte im Datenordner prüfen",
            ) from exc

    def backup_now(self, profile: ImportProfile) -> Path:
        """Manuelles Backup der aktiven Fassung."""
        version, articles = self.active(profile.id)
        if version is None:
            raise BackupError("Es gibt noch keinen Katalog, der gesichert werden könnte")
        path = self._backup(profile, version, articles, "manuell")
        assert path is not None
        return path

    def activate(self, profile: ImportProfile, number: int) -> Path | None:
        """Schaltet auf eine frühere Fassung zurück (vorher Backup der aktiven)."""
        versions = {v.version: v for v in self.versions(profile.id)}
        if number not in versions:
            raise KeyError(number)
        version, articles = self.active(profile.id)
        if version and version.version == number:
            return None
        backup = self._backup(
            profile, version, articles, f"vor Rücksprung auf Version {versions[number].number}"
        )
        with transaction(self._conn):
            self._repo().activate(profile.id, number)
            JournalRepository(self._conn).append(
                "catalog",
                profile.id,
                "catalog_activated",
                self._clock.now(),
                {"version": number, "previous": version.version if version else None},
            )
        return backup

    def restore(self, profile: ImportProfile, backup: Path) -> ImportPreview:
        """Vorschau zur Wiederherstellung eines Backups; gespeichert wird über :meth:`commit`."""
        table = self.backups.load(backup, profile.id)
        return self.preview(profile, table, identity_mapping(table.headers))

    def validate_active(self, profile: ImportProfile) -> list[CatalogIssue]:
        """Prüft den aktiven Katalog mit den heutigen Profilregeln (etwa geänderte Steuersätze)."""
        _, articles = self.active(profile.id)
        rows = []
        for index, article in enumerate(articles, start=1):
            issues = []
            if article.tax_rate is not None and article.tax_rate not in profile.tax_rates:
                issues.append(
                    CatalogIssue(
                        Severity.ERROR,
                        IssueCode.TAX_NOT_ALLOWED,
                        f"{article.number}: Steuersatz {article.tax_rate:g} % "
                        "ist im Profil nicht hinterlegt",
                        index,
                    )
                )
            rows.append(MappedRow(index, article, tuple(issues)))
        return validate_rows(rows)

    def export(
        self, profile: ImportProfile, fmt: ExportFormat, version: int | None = None
    ) -> bytes:
        """Exportiert die aktive oder eine bestimmte Fassung."""
        if version is None:
            current, articles = self.active(profile.id)
        else:
            current = next((v for v in self.versions(profile.id) if v.version == version), None)
            articles = self._repo().articles(version)
        label = current.number if current else None
        if fmt is ExportFormat.XLSX:
            return to_xlsx(articles)
        if fmt is ExportFormat.CSV:
            return to_csv(articles)
        return to_json(
            articles,
            profile_id=profile.id,
            profile_name=profile.name,
            version=label,
            created=self._clock.now(),
        )
