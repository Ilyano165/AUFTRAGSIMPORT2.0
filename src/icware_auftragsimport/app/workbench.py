"""Anwendungsschicht der Oberfläche: Aufträge laden, bearbeiten, freigeben und exportieren.

Die Oberfläche spricht ausschließlich mit dieser Klasse; Datenbank, Validierung und Export
bleiben dahinter verborgen und sind ohne Qt testbar.
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ..catalog.backup import CatalogBackups
from ..catalog.service import CatalogManager
from ..config.schema import ImportProfile, MailAccount, Settings, parse_settings, settings_to_dict
from ..domain.findings import ValidationResult
from ..domain.models import Article, ArticleMatch, MatchStatus, MatchStrategy, Order
from ..domain.ports import Clock
from ..domain.status import ExportMode, OrderStatus, ensure_transition
from ..infrastructure.db import transaction
from ..infrastructure.repositories import (
    CounterRepository,
    ExportJob,
    ExportJobRepository,
    JournalRepository,
    MailRecord,
    MailRepository,
    OrderRepository,
)
from ..ingest.mime import MailRejected, parse_mail
from ..security.credentials import CredentialStore, MemoryCredentialStore, credential_key
from ..services.export_service import ExportPreview, ExportReport, ExportService
from ..services.mail_fetch import FetchErrorKind, FetchFailure, SourceFactory, fetch_target
from ..services.numbering import next_document_number
from ..services.order_review import review_order
from ..services.rematch import REMATCHABLE, RematchPlan, plan_rematch, rematch_order
from ..services.validation import status_after_validation
from .fetching import FetchJob
from .presentation import CATEGORIES, Category, ExportSummary, OrderRow, build_row

EDITABLE = frozenset({OrderStatus.NEW, OrderStatus.NEEDS_REVIEW, OrderStatus.READY})


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """Auftrag mit allem, was die Detailansicht braucht."""

    order: Order
    mail: MailRecord | None
    validation: ValidationResult
    row: OrderRow

    @property
    def editable(self) -> bool:
        """Bearbeiten ist nur vor der Freigabe möglich."""
        return self.order.status in EDITABLE


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """Vor dem Export: was bereit ist und was blockiert."""

    summary: ExportSummary
    ready_ids: tuple[str, ...]
    blocked: tuple[tuple[str, str], ...]


class SettingsStore(Protocol):
    """Speichert Einstellungen (Konfigurationsdatei oder Speicher im Demo)."""

    def save(self, settings: Settings) -> None:
        """Schreibt die Einstellungen dauerhaft."""


class MemorySettings:
    """Einstellungen nur im Speicher (Demo, Tests)."""

    def __init__(self) -> None:
        self.saved: list[Settings] = []

    def save(self, settings: Settings) -> None:
        """Merkt sich den Stand."""
        self.saved.append(settings)


def _database_dir(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    file = row[2] if row is not None else ""
    return Path(file).parent if file else Path(tempfile.mkdtemp(prefix="icware-"))


class Workbench:
    """Fassade für die Oberfläche; alles bezieht sich auf das aktive Firmenprofil."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        clock: Clock,
        *,
        store: SettingsStore | None = None,
        data_dir: Path | None = None,
        credentials: CredentialStore | None = None,
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._clock = clock
        self._settings_store = store or MemorySettings()
        self._credentials: CredentialStore = credentials or MemoryCredentialStore()
        self._export = ExportService(conn, clock)
        directory = data_dir or _database_dir(conn)
        self._data_dir = directory
        self.catalogs = CatalogManager(conn, clock, CatalogBackups(directory / "katalog-backups"))
        with transaction(conn):
            adopted = OrderRepository(conn).assign_unassigned(settings.profiles[0].id)
            if adopted:
                JournalRepository(conn).append(
                    "profile",
                    settings.profiles[0].id,
                    "orders_adopted",
                    clock.now(),
                    {"orders": adopted},
                )

    @property
    def settings(self) -> Settings:
        """Aktuelle Einstellungen."""
        return self._settings

    @property
    def profile(self) -> ImportProfile:
        """Aktives Firmenprofil."""
        return self._settings.profile()

    @property
    def profiles(self) -> tuple[ImportProfile, ...]:
        """Alle Firmenprofile."""
        return self._settings.profiles

    def profile_of(self, order: Order) -> ImportProfile:
        """Profil eines Auftrags (Fallback: aktives Profil)."""
        try:
            return self._settings.profile(order.profile_id or None)
        except KeyError:
            return self.profile

    def _persist(self, settings: Settings) -> None:
        checked = parse_settings(settings_to_dict(settings))
        self._settings_store.save(checked)
        self._settings = checked

    def switch_profile(self, profile_id: str) -> None:
        """Wechselt das aktive Profil."""
        self._settings.profile(profile_id)
        self._persist(replace(self._settings, active_profile_id=profile_id))

    def save_profile(self, profile: ImportProfile) -> None:
        """Legt ein Profil an oder ersetzt es; prüft alle Profile gemeinsam (``ConfigError``)."""
        others = [p for p in self._settings.profiles if p.id != profile.id]
        position = next(
            (i for i, p in enumerate(self._settings.profiles) if p.id == profile.id), len(others)
        )
        others.insert(position, profile)
        self._persist(replace(self._settings, profiles=tuple(others)))

    def delete_profile(self, profile_id: str) -> None:
        """Löscht ein Profil ohne Aufträge; das aktive und das letzte Profil bleiben."""
        if profile_id == self._settings.active_profile_id:
            raise ValueError("Das aktive Profil kann nicht gelöscht werden")
        if len(self._settings.profiles) == 1:
            raise ValueError("Mindestens ein Profil muss bestehen bleiben")
        if OrderRepository(self._conn).list_refs(profile_id=profile_id):
            raise ValueError("Das Profil hat Aufträge und bleibt zur Nachvollziehbarkeit erhalten")
        profiles = tuple(p for p in self._settings.profiles if p.id != profile_id)
        self._persist(replace(self._settings, profiles=profiles))

    # ---------- Postfächer ----------

    @property
    def accounts(self) -> tuple[MailAccount, ...]:
        """Alle Postfächer."""
        return self._settings.accounts

    def save_account(self, account: MailAccount) -> None:
        """Legt ein Postfach an oder ersetzt es; ungültige Angaben ergeben ``ConfigError``."""
        accounts = [a for a in self._settings.accounts if a.id != account.id]
        position = next(
            (i for i, a in enumerate(self._settings.accounts) if a.id == account.id), len(accounts)
        )
        accounts.insert(position, account)
        self._persist(replace(self._settings, accounts=tuple(accounts)))

    def delete_account(self, account_id: str) -> None:
        """Löscht ein Postfach samt Passwort, wenn kein Profil es nutzt."""
        users = [p.name for p in self._settings.profiles if p.mail_account_id == account_id]
        if users:
            raise ValueError(f"Das Postfach wird von „{users[0]}“ verwendet")
        accounts = tuple(a for a in self._settings.accounts if a.id != account_id)
        self._persist(replace(self._settings, accounts=accounts))
        self._credentials.delete(credential_key(account_id))

    def has_password(self, account_id: str) -> bool:
        """Ist ein Passwort gespeichert? Der Wert selbst verlässt den Speicher nicht."""
        return self._credentials.get(credential_key(account_id)) is not None

    def set_password(self, account_id: str, secret: str) -> None:
        """Speichert das Passwort in der Anmeldeinformationsverwaltung (``CredentialError``)."""
        self._credentials.set(credential_key(account_id), secret)

    @property
    def database_path(self) -> Path | None:
        """Datei der Datenbank (``None`` bei einer Datenbank im Arbeitsspeicher)."""
        row = self._conn.execute("PRAGMA database_list").fetchone()
        return Path(row[2]) if row is not None and row[2] else None

    def fetch_job(self, sources: SourceFactory) -> FetchJob | FetchFailure:
        """Abruf für das Postfach des aktiven Profils oder der Grund, warum er nicht geht."""
        target = fetch_target(self._settings)
        if isinstance(target, FetchFailure):
            return target
        path = self.database_path
        if path is None:
            return FetchFailure(FetchErrorKind.DATABASE, target.account.id)
        return FetchJob(path, self._data_dir, self._clock, sources, (target,))

    def order_count(self, profile_id: str) -> int:
        """Anzahl Aufträge eines Profils."""
        return len(OrderRepository(self._conn).list_refs(profile_id=profile_id))

    def validate(self, order: Order) -> ValidationResult:
        """Validierung mit heutigem Datum, Profilregeln und Duplikatwissen."""
        return review_order(self._conn, order, self.profile_of(order), self._clock.now().date())

    def _mail(self, order: Order) -> MailRecord | None:
        if not order.mail_id:
            return None
        try:
            return MailRepository(self._conn).get(order.mail_id)
        except KeyError:
            return None

    def snapshot(self, order_id: str) -> OrderSnapshot:
        """Aktueller Stand eines Auftrags."""
        order = OrderRepository(self._conn).get(order_id)
        mail = self._mail(order)
        result = self.validate(order)
        received = mail.metadata.date_header if mail else None
        return OrderSnapshot(
            order, mail, result, build_row(order, result, received, self._clock.now())
        )

    def rows(self) -> list[OrderRow]:
        """Alle Aufträge als Listenzeilen."""
        refs = OrderRepository(self._conn).list_refs(profile_id=self.profile.id)
        return [self.snapshot(ref.id).row for ref in refs]

    @staticmethod
    def counts(rows: list[OrderRow]) -> dict[Category, int]:
        """Anzahl je Bereich."""
        return {c.key: sum(1 for r in rows if r.status in c.statuses) for c in CATEGORIES}

    def mail_text(self, snapshot: OrderSnapshot) -> str:
        """Originalmail als reiner Text; HTML wird nie dargestellt."""
        if snapshot.mail is None:
            return ""
        raw = MailRepository(self._conn).raw(snapshot.mail.metadata.id)
        if raw is None:
            return ""
        try:
            parsed = parse_mail(raw)
        except MailRejected as rejected:
            return f"Die Mail kann nicht angezeigt werden: {rejected.reason}"
        return parsed.text or parsed.html_text

    def _store(self, order: Order, status: OrderStatus, reason: str) -> OrderSnapshot:
        ensure_transition(order.status, status)
        current = OrderRepository(self._conn).get(order.id)
        updated = replace(order, status=status, revision=current.revision + 1)
        with transaction(self._conn):
            OrderRepository(self._conn).save(updated, reason, self._clock.now())
        return self.snapshot(order.id)

    def save(self, order: Order) -> OrderSnapshot:
        """Speichert Änderungen und bewertet den Auftrag neu."""
        if order.status not in EDITABLE:
            raise ValueError("Freigegebene oder exportierte Aufträge sind schreibgeschützt")
        status = status_after_validation(self.validate(order), order.acknowledged)
        return self._store(order, status, "Manuell bearbeitet")

    def approve(self, order_id: str) -> OrderSnapshot:
        """Gibt einen fehlerfreien Auftrag frei und vergibt die Belegnummer."""
        snapshot = self.snapshot(order_id)
        order = snapshot.order
        if order.status is not OrderStatus.READY or not snapshot.validation.is_exportable(
            order.acknowledged
        ):
            raise ValueError("Nur vollständig geprüfte Aufträge können freigegeben werden")
        profile = self.profile_of(order)
        year = self._clock.now().year
        with transaction(self._conn):
            number = next_document_number(
                CounterRepository(self._conn), profile.document_prefix, year, profile.id
            )
        return self._store(
            replace(order, document_number=number), OrderStatus.APPROVED, "Freigegeben"
        )

    def ignore(self, order_id: str) -> OrderSnapshot:
        """Markiert eine Mail als keine Bestellung."""
        order = OrderRepository(self._conn).get(order_id)
        return self._store(order, OrderStatus.IGNORED, "Als keine Bestellung markiert")

    def article(self, number: str) -> Article | None:
        """Artikel des aktiven Katalogs zur Artikelnummer (ohne Groß-/Kleinschreibung)."""
        _, articles = self.catalogs.active(self.profile.id)
        wanted = number.strip().casefold()
        return next((a for a in articles if a.number.casefold() == wanted), None)

    def catalog_version(self) -> int | None:
        """Nummer der aktiven Katalogfassung."""
        version, _ = self.catalogs.active(self.profile.id)
        return version.number if version else None

    def assign_article(self, order: Order, line_index: int, article: Article) -> Order:
        """Übernimmt einen Artikel für eine Position als manuelle Zuordnung (noch ungespeichert)."""
        lines = list(order.lines)
        line = lines[line_index]
        match = ArticleMatch(
            MatchStatus.MANUAL,
            article,
            MatchStrategy.MANUAL,
            "Vom Benutzer zugeordnet",
            catalog_version=self.catalog_version(),
        )
        lines[line_index] = replace(line, match=match, unit=line.unit or article.unit)
        return replace(order, lines=tuple(lines))

    def rematch_plan(self) -> RematchPlan:
        """Vorschau: offene Aufträge des Profils gegen den aktiven Katalog."""
        version, catalog = self.catalogs.catalog(self.profile.id)
        refs = OrderRepository(self._conn).list_refs(tuple(REMATCHABLE), profile_id=self.profile.id)
        orders = [OrderRepository(self._conn).get(r.id) for r in refs]
        return plan_rematch(orders, catalog, version, lambda o: self.snapshot(o.id).row.company)

    def apply_rematch(self, plan: RematchPlan) -> int:
        """Übernimmt die Neuzuordnung für die Aufträge der Vorschau; Status wird neu bewertet."""
        version, catalog = self.catalogs.catalog(self.profile.id)
        if version != plan.catalog_version:
            raise ValueError("Der Katalog wurde seit der Vorschau geändert; bitte neu prüfen")
        for order_id in plan.order_ids:
            order = OrderRepository(self._conn).get(order_id)
            if order.status in REMATCHABLE:
                self.save(rematch_order(order, catalog, version))
        return len(plan.order_ids)

    def preview(self, order: Order) -> ExportPreview:
        """XML-Vorschau ohne Speicherung."""
        return self._export.preview(order, self.profile_of(order))

    def export_plan(self) -> ExportPlan:
        """Freigegebene Aufträge, getrennt nach exportierbar und blockiert."""
        ready: list[str] = []
        blocked: list[tuple[str, str]] = []
        refs = OrderRepository(self._conn).list_refs(
            (OrderStatus.APPROVED,), profile_id=self.profile.id
        )
        for ref in refs:
            order = OrderRepository(self._conn).get(ref.id)
            errors = self.preview(order).validation.errors()
            if errors:
                blocked.append((ref.id, errors[0].message))
            else:
                ready.append(ref.id)
        return ExportPlan(ExportSummary(len(ready), len(blocked)), tuple(ready), tuple(blocked))

    def export(self, order_ids: tuple[str, ...], mode: ExportMode) -> list[ExportReport]:
        """Exportiert die Aufträge nacheinander; jeder Versuch erhält einen Bericht."""
        reports = []
        for order_id in order_ids:
            order = OrderRepository(self._conn).get(order_id)
            if order.profile_id != self.profile.id:
                raise ValueError("Exportiert werden nur Aufträge des aktiven Profils")
            reports.append(self._export.run(order_id, self.profile, mode))
        return reports

    def export_history(self, order_id: str) -> list[ExportJob]:
        """Bisherige Exportversuche eines Auftrags."""
        return ExportJobRepository(self._conn).for_order(order_id)

    @property
    def production_allowed(self) -> bool:
        """Produktivexport erst nach bestätigter Zielsystemvalidierung."""
        return self.profile.target_validated

    def now(self) -> datetime:
        """Aktuelle Zeit der Anwendung."""
        return self._clock.now()
