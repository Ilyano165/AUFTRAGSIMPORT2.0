"""Darstellungslogik der Oberfläche, unabhängig von Qt und dadurch vollständig testbar."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from ..domain.findings import Finding, Severity, ValidationResult
from ..domain.models import Article, MatchStatus, MatchStrategy, Order, OrderLine
from ..domain.status import OrderStatus


class Tone(StrEnum):
    """Farbton einer Statusanzeige."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"
    DONE = "done"
    NEUTRAL = "neutral"


class Glyph(StrEnum):
    """Form des Statuszeichens; Form und Text tragen die Bedeutung, nicht nur die Farbe."""

    RING = "ring"
    TRIANGLE = "triangle"
    DOT = "dot"
    CHECK = "check"
    CLOCK = "clock"
    DASH = "dash"
    SQUARE = "square"


@dataclass(frozen=True, slots=True)
class StatusLook:
    """Anzeige eines Auftragsstatus."""

    label: str
    tone: Tone
    glyph: Glyph
    priority: int
    description: str


STATUS_LOOK: dict[OrderStatus, StatusLook] = {
    OrderStatus.NEEDS_REVIEW: StatusLook(
        "Prüfen", Tone.WARNING, Glyph.TRIANGLE, 0, "Angaben fehlen oder sind unsicher"
    ),
    OrderStatus.FAILED: StatusLook("Fehler", Tone.DANGER, Glyph.SQUARE, 1, "Export fehlgeschlagen"),
    OrderStatus.UNCLEAR: StatusLook(
        "Unklar", Tone.DANGER, Glyph.SQUARE, 2, "Exportstatus unklar – in Lexware prüfen"
    ),
    OrderStatus.NEW: StatusLook("Neu", Tone.INFO, Glyph.RING, 3, "Eingegangen, noch nicht geprüft"),
    OrderStatus.READY: StatusLook(
        "Bereit", Tone.SUCCESS, Glyph.DOT, 4, "Vollständig, kann freigegeben werden"
    ),
    OrderStatus.APPROVED: StatusLook(
        "Freigegeben", Tone.SUCCESS, Glyph.CHECK, 5, "Freigegeben, wartet auf Export"
    ),
    OrderStatus.EXPORTING: StatusLook(
        "Export läuft", Tone.INFO, Glyph.CLOCK, 6, "Datei wird geschrieben"
    ),
    OrderStatus.EXPORTED: StatusLook(
        "Exportiert", Tone.DONE, Glyph.CHECK, 7, "An Lexware übergeben"
    ),
    OrderStatus.IGNORED: StatusLook("Ignoriert", Tone.NEUTRAL, Glyph.DASH, 8, "Keine Bestellung"),
}


class Category(StrEnum):
    """Bereiche unter der Titelleiste."""

    INBOX = "inbox"
    READY = "ready"
    REVIEW = "review"
    DONE = "done"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CategoryDef:
    """Ein Bereich mit enthaltenen Status."""

    key: Category
    label: str
    statuses: frozenset[OrderStatus]
    description: str


OPEN = frozenset(
    {
        OrderStatus.NEW,
        OrderStatus.NEEDS_REVIEW,
        OrderStatus.READY,
        OrderStatus.APPROVED,
        OrderStatus.EXPORTING,
        OrderStatus.FAILED,
        OrderStatus.UNCLEAR,
    }
)
CATEGORIES: tuple[CategoryDef, ...] = (
    CategoryDef(Category.INBOX, "Posteingang", OPEN, "Alle offenen Aufträge"),
    CategoryDef(
        Category.READY,
        "Bereit",
        frozenset({OrderStatus.READY, OrderStatus.APPROVED}),
        "Vollständig geprüft; freigeben und exportieren",
    ),
    CategoryDef(
        Category.REVIEW,
        "Prüfung erforderlich",
        frozenset({OrderStatus.NEW, OrderStatus.NEEDS_REVIEW}),
        "Angaben fehlen oder sind unsicher",
    ),
    CategoryDef(
        Category.DONE,
        "Erledigt",
        frozenset({OrderStatus.EXPORTED, OrderStatus.IGNORED}),
        "Exportiert oder als keine Bestellung markiert",
    ),
    CategoryDef(
        Category.ERROR,
        "Fehler",
        frozenset({OrderStatus.FAILED, OrderStatus.UNCLEAR}),
        "Export fehlgeschlagen oder Ergebnis unklar",
    ),
)


class DetailTab(StrEnum):
    """Bereiche der Detailansicht in fester Reihenfolge."""

    OVERVIEW = "Übersicht"
    INVOICE = "Rechnungsadresse"
    DELIVERY = "Lieferadresse"
    LINES = "Positionen"
    SHIPPING = "Versand & Zahlung"
    MAIL = "Originalmail"
    VALIDATION = "Validierung"
    EXPORT = "Export"


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def format_received(moment: datetime | None, now: datetime) -> str:
    """Eingangszeit wie in Windows-Mailprogrammen: heute nur Uhrzeit, sonst Datum."""
    if moment is None:
        return "–"
    local, today = moment.astimezone(now.tzinfo), now.date()
    if local.date() == today:
        return f"{local:%H:%M}"
    if local.date() == today - timedelta(days=1):
        return f"Gestern {local:%H:%M}"
    if today - local.date() < timedelta(days=7):
        return f"{WEEKDAYS[local.weekday()]} {local:%d.%m.}"
    return f"{local:%d.%m.%Y}"


def format_date(value: date | None) -> str:
    """Datum im deutschen Format."""
    return f"{value:%d.%m.%Y}" if value else "–"


def format_money(value: Decimal | None) -> str:
    """Betrag mit Tausenderpunkt und Komma, etwa ``1.234,50 €``."""
    if value is None:
        return "–"
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{text} €"


def format_quantity(value: Decimal | None) -> str:
    """Menge ohne überflüssige Nullen, Komma als Dezimaltrenner."""
    if value is None:
        return "–"
    text = str(int(value)) if value == value.to_integral_value() else f"{value.normalize():f}"
    return text.replace(".", ",")


def plural(count: int, singular: str, plural_form: str) -> str:
    """``1 Auftrag`` / ``3 Aufträge``."""
    return f"{count} {singular if count == 1 else plural_form}"


@dataclass(frozen=True, slots=True)
class ExportSummary:
    """Zusammenfassung vor dem Export."""

    ready: int
    blocked: int

    @property
    def ready_text(self) -> str:
        """``3 Aufträge bereit``."""
        return f"{plural(self.ready, 'Auftrag', 'Aufträge')} bereit"

    @property
    def blocked_text(self) -> str:
        """``0 Aufträge mit Fehlern``."""
        return f"{plural(self.blocked, 'Auftrag', 'Aufträge')} mit Fehlern"

    @property
    def can_start(self) -> bool:
        """Export ist sinnvoll, wenn mindestens ein Auftrag bereit ist."""
        return self.ready > 0


@dataclass(frozen=True, slots=True)
class OrderRow:
    """Eine Zeile der Auftragsliste."""

    order_id: str
    status: OrderStatus
    received: datetime | None
    received_text: str
    order_number: str
    company: str
    contact: str
    positions: int
    errors: int
    warnings: int
    first_problem: str

    @property
    def look(self) -> StatusLook:
        """Statusdarstellung."""
        return STATUS_LOOK[self.status]

    @property
    def problems_text(self) -> str:
        """Kurzform für die Spalte „Probleme“."""
        if self.errors:
            return plural(self.errors, "Fehler", "Fehler")
        if self.warnings:
            return plural(self.warnings, "Hinweis", "Hinweise")
        return ""

    def matches(self, query: str) -> bool:
        """Einfache Volltextsuche über die sichtbaren Spalten."""
        haystack = " ".join(
            (self.order_number, self.company, self.contact, self.look.label, self.received_text)
        ).casefold()
        return all(part in haystack for part in query.casefold().split())


def build_row(
    order: Order, result: ValidationResult, received: datetime | None, now: datetime
) -> OrderRow:
    """Listenzeile aus Auftrag, Validierung und Eingangszeit."""
    invoice = order.invoice_address.value
    contact = order.contact.value
    company = (invoice.company or invoice.name) if invoice else ""
    is_open = order.status in OPEN
    errors = result.errors(order.acknowledged) if is_open else ()
    warnings = result.warnings() if is_open else ()
    first = errors[0] if errors else (warnings[0] if warnings else None)
    return OrderRow(
        order_id=order.id,
        status=order.status,
        received=received,
        received_text=format_received(received, now),
        order_number=order.customer_reference.value or "",
        company=company,
        contact=contact.display_name() if contact else "",
        positions=len(order.lines),
        errors=len(errors),
        warnings=len(warnings),
        first_problem=present_finding(first, order).headline if first else "",
    )


_LINE_PATH = re.compile(r"lines\[(\d+)\](?:\.(\w+))?")
_LINE_PREFIX = re.compile(r"^Position \d+:\s*")
_FIELD_LABELS = {
    "article": "Artikelnummer",
    "quantity": "Menge",
    "unit_price": "Preis",
    "unit": "Einheit",
    "description": "Bezeichnung",
    "country": "Land",
    "postal_code": "PLZ",
    "house_number": "Hausnummer",
    "street": "Straße",
    "city": "Ort",
}
_HEADER_FIELDS = {
    "customer_reference": "Bestellnummer",
    "order_date": "Bestelldatum",
    "contact": "Ansprechpartner",
    "vat_id": "USt-IdNr.",
    "customer_number": "Kundennummer",
}
_SHIPPING_FIELDS = {
    "payment_method": "Zahlungsart",
    "iban": "IBAN",
    "shipping_method": "Versandart",
    "shipping_fee": "Versandkosten",
    "requested_delivery": "Lieferwunsch",
}
_VOCABULARY = (("Rechnungsanschrift", "Rechnungsadresse"), ("Lieferanschrift", "Lieferadresse"))


@dataclass(frozen=True, slots=True)
class IssueView:
    """Ein Validierungsbefund, so formuliert, dass klar ist, was wo zu tun ist."""

    severity: Severity
    title: str
    detail: str
    location: str
    action: str
    tab: DetailTab
    line_index: int | None
    finding: Finding

    @property
    def steps(self) -> tuple[str, ...]:
        """Was wo zu tun ist, etwa ``("Position 4", "Benutzeraktion erforderlich")``.

        Ort und Feld entfallen, wenn sie schon im Titel stehen.
        """
        place, _, field = self.location.partition(" · ")
        if field and field.casefold() in self.title.casefold():
            location = place
        elif self.location.casefold() in self.title.casefold():
            location = ""
        else:
            location = self.location
        return tuple(part for part in (location, self.detail, self.action) if part)

    @property
    def headline(self) -> str:
        """Einzeilige Kurzform für Listen und Tooltips."""
        text = f"{self.title}: {self.detail}" if self.detail else self.title
        return f"{text} ({self.location})" if self.location and self.location not in text else text


def _locate(order: Order, path: str) -> tuple[str, DetailTab, int | None]:
    root, _, sub = path.partition(".")
    if match := _LINE_PATH.match(path):
        index = int(match.group(1))
        position = order.lines[index].position if index < len(order.lines) else index + 1
        label = _FIELD_LABELS.get(match.group(2) or "", "")
        return (f"Position {position}" + (f" · {label}" if label else "")), DetailTab.LINES, index
    if root in ("invoice_address", "delivery_address"):
        tab = DetailTab.INVOICE if root == "invoice_address" else DetailTab.DELIVERY
        label = _FIELD_LABELS.get(sub, "")
        return (tab.value + (f" · {label}" if label else "")), tab, None
    if root in _SHIPPING_FIELDS:
        return f"Versand & Zahlung · {_SHIPPING_FIELDS[root]}", DetailTab.SHIPPING, None
    if root in _HEADER_FIELDS:
        return f"Übersicht · {_HEADER_FIELDS[root]}", DetailTab.OVERVIEW, None
    return "", DetailTab.OVERVIEW, None


def _action(finding: Finding) -> str:
    if finding.severity is Severity.ERROR:
        return (
            "Prüfen und bewusst bestätigen"
            if finding.acknowledgeable
            else "Benutzeraktion erforderlich"
        )
    return "Bitte prüfen" if finding.severity is Severity.WARNING else "Zur Information"


def present_finding(finding: Finding, order: Order) -> IssueView:
    """Übersetzt einen Befund in Titel, Detail, Ort und nötige Aktion."""
    message = _LINE_PREFIX.sub("", finding.message)
    for old, new in _VOCABULARY:
        message = message.replace(old, new)
    location, tab, line = _locate(order, finding.field)
    if message.startswith("kein Artikel zugeordnet") and line is not None:
        hint = order.lines[line].article_hint.value if line < len(order.lines) else None
        message = "Artikel nicht im Katalog: " + hint if hint else "Artikelnummer fehlt"
    title, _, detail = message.partition(": ")
    title = title[:1].upper() + title[1:]
    return IssueView(
        severity=finding.severity,
        title=title,
        detail=detail or finding.hint,
        location=location,
        action=_action(finding),
        tab=tab,
        line_index=line,
        finding=finding,
    )


def present_findings(result: ValidationResult, order: Order) -> list[IssueView]:
    """Alle Befunde, Fehler zuerst, dann Warnungen, dann Hinweise."""
    rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    views = [present_finding(f, order) for f in result.findings]
    return sorted(views, key=lambda v: rank[v.severity])


STRATEGY_LABELS = {
    MatchStrategy.EXPLICIT_NUMBER: "Artikelnummer",
    MatchStrategy.ALIAS: "Alias",
    MatchStrategy.NAME: "exakter Name",
    MatchStrategy.NORMALIZED_NAME: "Name (normalisiert)",
    MatchStrategy.FUZZY: "ähnlicher Name",
    MatchStrategy.MANUAL: "manuell",
    MatchStrategy.NONE: "–",
}
_CERTAIN = frozenset({MatchStrategy.EXPLICIT_NUMBER, MatchStrategy.ALIAS, MatchStrategy.NAME})
MATCH_STATUS = {
    MatchStatus.MATCHED: ("automatisch zugeordnet", Tone.SUCCESS),
    MatchStatus.MANUAL: ("manuell zugeordnet", Tone.INFO),
    MatchStatus.NEEDS_REVIEW: ("Benutzerprüfung erforderlich", Tone.WARNING),
    MatchStatus.UNKNOWN: ("nicht zugeordnet – Benutzeraktion erforderlich", Tone.DANGER),
}


@dataclass(frozen=True, slots=True)
class CandidateView:
    """Vorschlag für eine unsichere Zuordnung."""

    article: Article
    method: str
    score: str


@dataclass(frozen=True, slots=True)
class MatchView:
    """Zuordnung einer Position: Quelle, Artikel, Methode, Sicherheit, Status."""

    source: str
    catalog: str
    article_number: str
    name: str
    method: str
    certainty: str
    status: str
    tone: Tone
    reason: str
    candidates: tuple[CandidateView, ...]

    @property
    def needs_action(self) -> bool:
        """True, wenn der Benutzer entscheiden muss."""
        return self.tone in (Tone.WARNING, Tone.DANGER)


def _certainty(line: OrderLine) -> str:
    match = line.match
    if match.status is MatchStatus.MANUAL:
        return "vom Benutzer festgelegt"
    if match.status is MatchStatus.MATCHED:
        if match.strategy in _CERTAIN:
            return "sicher"
        return "hoch" if match.strategy is MatchStrategy.NORMALIZED_NAME else "mittel"
    if match.candidates:
        best = max(c.score for c in match.candidates)
        return f"unsicher (beste Übereinstimmung {best:.0%})".replace("%", " %")
    return "kein Treffer"


def present_match(line: OrderLine) -> MatchView:
    """Darstellung der Artikelzuordnung einer Position."""
    match = line.match
    hint = line.article_hint.value
    source = f"Mail: „{line.description}“" + (f", Artikelnr. {hint}" if hint else "")
    catalog = (
        f"Katalog Version {match.catalog_version}" if match.catalog_version else "aktiver Katalog"
    )
    status, tone = MATCH_STATUS[match.status]
    article = match.article
    candidates = tuple(
        CandidateView(c.article, STRATEGY_LABELS[c.strategy], f"{c.score:.0%}".replace("%", " %"))
        for c in sorted(match.candidates, key=lambda c: -c.score)
    )
    return MatchView(
        source=source,
        catalog=catalog,
        article_number=article.number if article else "–",
        name=article.name if article else "–",
        method=STRATEGY_LABELS[match.strategy],
        certainty=_certainty(line),
        status=status,
        tone=tone,
        reason=match.reason,
        candidates=candidates,
    )
