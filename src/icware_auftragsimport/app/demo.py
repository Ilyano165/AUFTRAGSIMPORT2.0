"""Demobestand für Vorführung, Schulung und Oberflächentests.

Zwei fiktive Firmenprofile mit getrennten Postfächern, Katalogen, Nummernkreisen und
Exportordnern. Die Artikelzuordnungen entstehen über den echten Matcher, die Kataloge über
den echten Import (mit Versionen, Änderungsstatistik und Backup).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..catalog.backup import CatalogBackups
from ..catalog.export import to_xlsx
from ..catalog.service import CatalogManager
from ..catalog.table import TableOptions
from ..config.schema import (
    ImportProfile,
    MailAccount,
    SenderAction,
    SenderRule,
    Settings,
    ShippingOption,
    parse_settings,
    settings_to_dict,
)
from ..domain.models import Address, Article, Contact, MailMetadata, Order, OrderLine, PaymentMethod
from ..domain.ports import Clock
from ..domain.provenance import Confidence, Evidence, Field
from ..domain.status import MailState, OrderStatus
from ..infrastructure.clock import FixedClock
from ..infrastructure.db import Database, transaction
from ..infrastructure.repositories import CounterRepository, MailRepository, OrderRepository
from ..services.matching import ArticleMatcher
from ..services.numbering import next_document_number
from ..services.validation import ValidationContext, status_after_validation, validate_order
from .testmails import standard_mails, test_mailbox_dir, write_test_mailbox

SURE = Evidence("demo", "Aus der Mail erkannt", Confidence.CERTAIN)
UNSURE = Evidence("demo", "Mehrdeutig in der Mail", Confidence.UNCERTAIN)
STANDARD, NORDLICHT = "standard", "nordlicht"


def _article(number: str, name: str, unit: str, tax: int, price: str, *aliases: str) -> Article:
    return Article(number, name, aliases, unit, Decimal(tax), Decimal(price))


ARTICLES: dict[str, Article] = {
    a.number: a
    for a in (
        _article("MW-0710", "Mineralwasser still 12 × 0,7 l", "Kiste", 19, "6.90", "Wasser still"),
        _article(
            "MW-0720", "Mineralwasser medium 12 × 0,7 l", "Kiste", 19, "6.90", "Wasser medium"
        ),
        _article("AS-1000", "Apfelschorle 12 × 1,0 l", "Kiste", 19, "11.40"),
        _article("OS-1000", "Orangensaft Direktsaft 6 × 1,0 l", "Karton", 19, "14.80"),
        _article("KF-1000", "Kaffee Crema ganze Bohne 1 kg", "Beutel", 7, "17.50", "Crema Bohne"),
        _article("MI-1035", "Frischmilch 3,5 % 12 × 1,0 l", "Karton", 7, "13.20"),
        _article("SV-0500", "Servietten 3-lagig 40 × 40, 500 Stk", "Packung", 19, "9.95"),
        _article("BC-0200", "Becher 0,2 l Pappe, 1000 Stk", "Karton", 19, "42.00"),
    )
}
FIRST_CATALOG = ("MW-0710", "MW-0720", "AS-1000", "KF-1000", "MI-1035", "SV-0500")
OLD_PRICES = {"MW-0710": "6.50", "MW-0720": "6.50", "KF-1000": "16.90"}
NORDLICHT_ARTICLES: dict[str, Article] = {
    a.number: a
    for a in (
        _article(
            "FO-631-BK",
            "Plotterfolie Oracal 631 schwarz 63 cm × 50 m",
            "Rolle",
            19,
            "89.00",
            "Oracal 631 schwarz",
        ),
        _article(
            "FO-631-WH",
            "Plotterfolie Oracal 631 weiß 63 cm × 50 m",
            "Rolle",
            19,
            "89.00",
            "Oracal 631 weiß",
        ),
        _article("RK-FILZ-10", "Rakel mit Filzkante 10 cm", "Stück", 19, "3.40", "Filzrakel"),
        _article("MS-45", "Plottermesser 45°, 5 Stück", "Packung", 19, "24.50", "Messer 45 Grad"),
        _article(
            "AP-100", "Applikationspapier 61 cm × 100 m", "Rolle", 19, "64.00", "Transferpapier"
        ),
    )
}


@dataclass(frozen=True, slots=True)
class Place:
    """Anschrift in Kurzform."""

    company: str
    street: str
    number: str
    postal_code: str
    city: str
    name: str = ""

    def address(self) -> Address:
        """Als Domänenanschrift."""
        return Address(
            company=self.company,
            name=self.name,
            street=self.street,
            house_number=self.number,
            postal_code=self.postal_code,
            city=self.city,
            country="DE",
        )


@dataclass(frozen=True, slots=True)
class DemoLine:
    """Position einer Demomail: Artikelnummer (falls genannt), Menge, Text."""

    number: str | None
    quantity: str
    text: str = ""
    unsure: bool = False


@dataclass(frozen=True, slots=True)
class DemoOrder:
    """Ein Demoauftrag mit Mail."""

    order_id: str
    profile_id: str
    state: OrderStatus
    hours_ago: float
    contact: str
    place: Place
    reference: str
    lines: tuple[DemoLine, ...]
    delivery: Place | None = None
    shipping: str = "Tourlieferung"
    payment: PaymentMethod = PaymentMethod.INVOICE


def _o(  # noqa: PLR0917
    order_id: str,
    state: OrderStatus,
    hours: float,
    contact: str,
    place: Place,
    reference: str,
    *lines: DemoLine,
    profile: str = STANDARD,
    **extra: object,
) -> DemoOrder:
    return DemoOrder(order_id, profile, state, hours, contact, place, reference, lines, **extra)  # type: ignore[arg-type]


L = DemoLine
DEMO_ORDERS: tuple[DemoOrder, ...] = (
    _o(
        "order-001",
        OrderStatus.READY,
        0.4,
        "Petra Lindner",
        Place("Gasthaus Lindenhof", "Lindenallee", "14", "50968", "Köln"),
        "LH-2026-118",
        L("MW-0710", "10"),
        L("AS-1000", "6"),
        L("KF-1000", "4"),
        L("SV-0500", "2"),
    ),
    _o(
        "order-002",
        OrderStatus.NEEDS_REVIEW,
        1.2,
        "Jens Reuter",
        Place("Frischemarkt Reuter e.K.", "Hauptstraße", "122", "53757", "Sankt Augustin"),
        "BS-44871",
        L("MW-0720", "20"),
        L("OS-1000", "8"),
        L("MI-1035", "5"),
        L(None, "3", "Bio-Hafermilch 1 l"),
    ),
    _o(
        "order-003",
        OrderStatus.NEEDS_REVIEW,
        2.5,
        "Sabine Krüger",
        Place("Hotel am Stadtpark GmbH", "Parkstraße", "3", "53111", "Bonn"),
        "HS-0915",
        L("KF-1000", "12"),
        L("MI-1035", "10"),
        delivery=Place("Hotel am Stadtpark, Anlieferung", "Am Stadtpark", "", "53111", "Bonn"),
    ),
    _o(
        "order-004",
        OrderStatus.APPROVED,
        3.1,
        "Martina Blum",
        Place("Café Blum", "Marktplatz", "7", "51545", "Waldbröl"),
        "",
        L("KF-1000", "3"),
        L("MI-1035", "4"),
        L("BC-0200", "1"),
    ),
    _o(
        "order-005",
        OrderStatus.APPROVED,
        5.0,
        "Thomas Weiß",
        Place("Brauhaus am Markt", "Am Markt", "2", "51570", "Windeck"),
        "BM-2209",
        L("MW-0710", "25"),
        L("AS-1000", "15"),
    ),
    _o(
        "order-006",
        OrderStatus.APPROVED,
        6.2,
        "Andreas Schulte",
        Place("Getränke Schulte GmbH", "Industriestraße", "18a", "57537", "Wissen"),
        "4500018822",
        L("MW-0710", "60"),
        L("MW-0720", "60"),
        L("OS-1000", "20"),
    ),
    _o(
        "order-007",
        OrderStatus.NEW,
        0.1,
        "Lena Krämer",
        Place("Bäckerei Krämer", "Bahnhofstraße", "21", "51597", "Morsbach"),
        "",
        L("MI-1035", "6", unsure=True),
        L(None, "2", "Wasser still"),
    ),
    _o(
        "order-008",
        OrderStatus.EXPORTED,
        26,
        "Frank Ohm",
        Place("Kantine Stadtwerke Siegburg", "Wilhelmstraße", "55", "53721", "Siegburg"),
        "SW-77310",
        L("MW-0710", "40"),
        L("AS-1000", "20"),
    ),
    _o(
        "order-009",
        OrderStatus.EXPORTED,
        30,
        "Marco Bellini",
        Place("Restaurant Olivo", "Rheinstraße", "9", "53844", "Troisdorf"),
        "",
        L("MW-0720", "8"),
        L("KF-1000", "2"),
    ),
    _o(
        "order-010",
        OrderStatus.FAILED,
        20,
        "Uwe Weber",
        Place("Partyservice Weber", "Gartenweg", "4", "51588", "Nümbrecht"),
        "PW-1102",
        L("BC-0200", "3"),
        L("SV-0500", "6"),
    ),
    _o(
        "order-011",
        OrderStatus.IGNORED,
        44,
        "Klaus Becker",
        Place("Turnverein Herchen 1911 e.V.", "Sportplatzweg", "1", "51570", "Windeck"),
        "",
    ),
    _o(
        "order-101",
        OrderStatus.READY,
        0.8,
        "Jana Petersen",
        Place("Schilder Petersen", "Holtenauer Straße", "48", "24105", "Kiel"),
        "SP-3310",
        L("FO-631-BK", "4"),
        L("AP-100", "2"),
        profile=NORDLICHT,
        shipping="Paketversand",
    ),
    _o(
        "order-102",
        OrderStatus.NEEDS_REVIEW,
        1.6,
        "Mats Hansen",
        Place("Hansen Fahrzeugbeschriftung", "Werftstraße", "7", "24143", "Kiel"),
        "HF-0221",
        L("FO-631-WH", "2"),
        L(None, "10", "Rakel mit Filz 10 cm"),
        L(None, "1", "Messer 45 Grad"),
        profile=NORDLICHT,
        shipping="DHL",
    ),
)


def _line(
    position: int,
    spec: DemoLine,
    matcher: ArticleMatcher,
    version: int | None,
    articles: dict[str, Article],
) -> OrderLine:
    article = articles.get(spec.number or "")
    amount = Decimal(spec.quantity)
    line = OrderLine(
        position=position,
        description=article.name if article else spec.text,
        quantity=Field.review(amount, UNSURE) if spec.unsure else Field.found(amount, SURE),
        unit=article.unit if article else "",
        article_hint=Field.found(spec.number, SURE) if spec.number else Field(),
    )
    return replace(line, match=replace(matcher.match(line), catalog_version=version))


def mail_body(demo: DemoOrder) -> str:
    """Text der Demomail."""
    articles = {**ARTICLES, **NORDLICHT_ARTICLES}
    rows = []
    for spec in demo.lines:
        article = articles.get(spec.number or "")
        rows.append(
            f"{spec.quantity} x {spec.number} {article.name}"
            if article
            else f"{spec.quantity} x {spec.text}"
        )
    reference = f"Unsere Bestellnummer: {demo.reference}\n" if demo.reference else ""
    return (
        "Guten Tag,\n\nbitte liefern Sie uns folgende Artikel:\n\n"
        + "\n".join(rows)
        + f"\n\n{reference}Versand: {demo.shipping}\n\nMit freundlichen Grüßen\n{demo.contact}\n"
    )


def _sender(demo: DemoOrder) -> str:
    first, _, last = demo.contact.partition(" ")
    text = f"{first}.{last}@{demo.place.company.split()[0]}.example".lower()
    for old, new in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("é", "e")):
        text = text.replace(old, new)
    return text


def _insert(
    conn: sqlite3.Connection, demo: DemoOrder, now: datetime, catalogs: CatalogManager
) -> None:
    received = now - timedelta(hours=demo.hours_ago)
    sender, subject = _sender(demo), f"Bestellung {demo.place.company}"
    raw = (
        f"From: {demo.contact} <{sender}>\r\nTo: bestellung@{demo.profile_id}.example\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: <{demo.order_id}@example>\r\nMIME-Version: 1.0\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n" + mail_body(demo)
    ).encode()
    digest = hashlib.sha256(raw).hexdigest()
    mail_id = demo.order_id.replace("order", "mail")
    account = "bestellungen" if demo.profile_id == STANDARD else "nordlicht-bestellungen"
    uid = int(demo.order_id.rsplit("-", 1)[1])
    meta = MailMetadata(
        id=mail_id,
        account_id=account,
        folder="INBOX",
        uidvalidity=1,
        uid=uid,
        message_id=f"<{demo.order_id}@example>",
        content_hash=digest,
        raw_sha256=digest,
        sender=sender,
        sender_name=demo.contact,
        subject=subject,
        date_header=received,
        size=len(raw),
    )
    MailRepository(conn).insert(meta, MailState.EXTRACTED, now, raw=raw)
    version, catalog = catalogs.catalog(demo.profile_id)
    articles = ARTICLES if demo.profile_id == STANDARD else NORDLICHT_ARTICLES
    matcher = ArticleMatcher(catalog)
    first, _, last = demo.contact.partition(" ")
    reference: Field[str] = (
        Field.found(demo.reference, SURE)
        if demo.reference
        else Field.unknown("Keine Bestellnummer in der Mail")
    )
    order = Order(
        id=demo.order_id,
        profile_id=demo.profile_id,
        mail_id=mail_id,
        customer_key=sender,
        customer_reference=reference,
        invoice_address=Field.found(replace(demo.place, name=demo.contact).address(), SURE),
        delivery_address=Field.found(demo.delivery.address(), SURE) if demo.delivery else Field(),
        delivery_same_as_invoice=demo.delivery is None,
        contact=Field.found(Contact("", first, last, sender), SURE),
        order_date=Field.found(received.date(), SURE),
        payment_method=Field.found(demo.payment, SURE),
        shipping_method=Field.found(demo.shipping, SURE),
        lines=tuple(
            _line(i, spec, matcher, version, articles) for i, spec in enumerate(demo.lines, 1)
        ),
    )
    status = demo.state
    if status in (OrderStatus.READY, OrderStatus.NEEDS_REVIEW):
        status = status_after_validation(
            validate_order(order, ValidationContext(today=now.date())), order.acknowledged
        )
    prefix = "AU" if demo.profile_id == STANDARD else "NL"
    numbered = status in (OrderStatus.APPROVED, OrderStatus.EXPORTED, OrderStatus.FAILED)
    counters = CounterRepository(conn)
    number = next_document_number(counters, prefix, now.year, demo.profile_id) if numbered else None
    OrderRepository(conn).insert(replace(order, status=status, document_number=number), "Demo", now)


def _csv(articles: list[Article]) -> bytes:
    rows = ["Artikelnummer;Bezeichnung;Einheit;MwSt;VK-Preis netto;Suchbegriffe"]
    for a in articles:
        rows.append(
            f"{a.number};{a.name};{a.unit};{a.tax_rate} %;"
            f"{str(a.price).replace('.', ',')};{'|'.join(a.aliases)}"
        )
    return ("\r\n".join(rows) + "\r\n").encode("cp1252")


def _catalogs(
    conn: sqlite3.Connection, directory: Path, now: datetime, profiles: dict[str, ImportProfile]
) -> CatalogManager:
    """Katalog-Historie über den echten Import: Version 1 als CSV, Version 2 als XLSX."""
    history: list[tuple[str, str, bytes, timedelta]] = [
        (
            STANDARD,
            "artikelstamm-2026-01.csv",
            _csv(
                [
                    replace(ARTICLES[n], price=Decimal(OLD_PRICES.get(n, str(ARTICLES[n].price))))
                    for n in FIRST_CATALOG
                ]
            ),
            timedelta(days=240),
        ),
        (
            STANDARD,
            "artikelstamm-2026-09.xlsx",
            to_xlsx(list(ARTICLES.values())),
            timedelta(days=12),
        ),
        (
            NORDLICHT,
            "nordlicht-artikel.csv",
            _csv(list(NORDLICHT_ARTICLES.values())),
            timedelta(days=30),
        ),
    ]
    backups = CatalogBackups(directory / "katalog-backups")
    manager = CatalogManager(conn, FixedClock(now), backups)
    for profile_id, name, data, age in history:
        clock: Clock = FixedClock(now - age)
        step = CatalogManager(conn, clock, backups)
        table = step.read(data, name, TableOptions())
        profile = profiles[profile_id]
        step.commit(profile, step.preview(profile, table, step.suggest(profile_id, table)), "Demo")
    return manager


def build_demo(directory: Path, now: datetime) -> tuple[Database, Settings]:
    """Legt Datenbank, Kataloge, Exportordner und Einstellungen eines Demobestands an."""
    directory.mkdir(parents=True, exist_ok=True)
    database = Database(directory / "demo.db")
    conn = database.connect()
    database.migrate(conn)
    accounts = (
        MailAccount(
            id="bestellungen",
            name="Bestellungen Getränke",
            host="imap.muster-getraenke.example",
            username="bestellung@muster-getraenke.example",
        ),
        MailAccount(
            id="nordlicht-bestellungen",
            name="Bestellungen Nordlicht",
            host="imap.nordlicht.example",
            username="auftrag@nordlicht.example",
        ),
    )
    profiles = {
        STANDARD: ImportProfile(
            id=STANDARD,
            name="Muster Getränke",
            document_prefix="AU",
            supplier=Place(
                "Muster Getränkegroßhandel GmbH", "Siegstraße", "10", "51570", "Windeck"
            ).address(),
            export_dir=str(_folder(directory, "lexware-import")),
            test_export_dir=str(_folder(directory, "lexware-test")),
            mail_account_id="bestellungen",
            sender_rules=(
                SenderRule("@gasthaus.example"),
                SenderRule("@turnverein.example", SenderAction.IGNORE),
                SenderRule("lena.kraemer@baeckerei.example", SenderAction.REVIEW),
            ),
            shipping_methods=(
                ShippingOption("Tourlieferung", ("Tour", "wie gewohnt")),
                ShippingOption("Abholung", ("Selbstabholung", "holen ab")),
            ),
            payment_methods=(PaymentMethod.INVOICE, PaymentMethod.DIRECT_DEBIT),
        ),
        NORDLICHT: ImportProfile(
            id=NORDLICHT,
            name="Nordlicht Werbetechnik",
            document_prefix="NL",
            supplier=Place(
                "Nordlicht Werbetechnik GmbH", "Kaistraße", "12", "24114", "Kiel"
            ).address(),
            export_dir=str(_folder(directory, "nordlicht-import")),
            test_export_dir=str(_folder(directory, "nordlicht-test")),
            mail_account_id="nordlicht-bestellungen",
            sender_rules=(SenderRule("@schilder.example"), SenderRule("@hansen.example")),
            sender_default=SenderAction.REVIEW,
            shipping_methods=(
                ShippingOption("Paketversand", ("DHL", "Paket"), Decimal("6.90")),
                ShippingOption("Abholung"),
            ),
            payment_methods=(PaymentMethod.INVOICE, PaymentMethod.PREPAYMENT),
        ),
    }
    settings = parse_settings(
        settings_to_dict(
            Settings(
                accounts=accounts, profiles=tuple(profiles.values()), active_profile_id=STANDARD
            )
        )
    )
    catalogs = _catalogs(conn, directory, now, profiles)
    with transaction(conn):
        for demo in DEMO_ORDERS:
            _insert(conn, demo, now, catalogs)
    known = MailRepository(conn).raw(DEMO_ORDERS[0].order_id.replace("order", "mail"))
    write_test_mailbox(test_mailbox_dir(directory, "bestellungen"), standard_mails(now, known))
    return database, settings


def _folder(directory: Path, name: str) -> Path:
    path = directory / name
    path.mkdir(exist_ok=True)
    return path
