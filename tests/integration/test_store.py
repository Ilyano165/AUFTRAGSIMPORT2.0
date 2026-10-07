from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from icware_auftragsimport.domain.codec import order_from_dict, order_to_dict
from icware_auftragsimport.domain.errors import ConcurrencyError, StoreError
from icware_auftragsimport.domain.models import (
    Address,
    Article,
    ArticleMatch,
    Contact,
    MailMetadata,
    MatchCandidate,
    MatchStatus,
    MatchStrategy,
    Order,
    OrderLine,
    PaymentMethod,
)
from icware_auftragsimport.domain.provenance import (
    Confidence,
    Evidence,
    Field,
    SourceRef,
)
from icware_auftragsimport.domain.status import MailState, OrderStatus
from icware_auftragsimport.infrastructure import db as db_module
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database, transaction
from icware_auftragsimport.infrastructure.repositories import (
    CatalogRepository,
    CounterRepository,
    IdempotencyRepository,
    JournalRepository,
    MailRepository,
    OrderRepository,
)
from icware_auftragsimport.services.idempotency import (
    MailDuplicate,
    MailIdentity,
    check_mail,
    content_fingerprint,
    customer_key,
    mail_keys,
    reference_conflicts,
)
from icware_auftragsimport.services.numbering import next_document_number


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    database = Database(tmp_path / "test.db")
    connection = database.connect()
    database.migrate(connection)
    return connection


def _evidence(line: int) -> Evidence:
    return Evidence("rule", "Regel", Confidence.CERTAIN, SourceRef(line, line, "Zeile"))


def _order(order_id: str = "o1", reference: str = "PO-251843") -> Order:
    article = Article("YT11YBOR01", "YelloBlade Orange", ("Blade Orange",), "Stk", Decimal("19"))
    return Order(
        id=order_id,
        customer_key="kd:10042",
        customer_reference=Field.found(reference, _evidence(2)),
        invoice_address=Field.found(
            Address(
                company="Muster GmbH",
                street="Weg",
                house_number="1a",
                postal_code="50667",
                city="Köln",
                country="DE",
            ),
            _evidence(5),
        ),
        delivery_same_as_invoice=True,
        contact=Field.found(Contact(first_name="Thomas", last_name="Müller"), _evidence(9)),
        order_date=Field.found(date(2026, 9, 29), _evidence(3)),
        payment_method=Field.review(
            PaymentMethod.INVOICE, _evidence(7), (PaymentMethod.INVOICE, PaymentMethod.PREPAYMENT)
        ),
        shipping_fee=Field.found(Decimal("8.90"), _evidence(8)),
        lines=(
            OrderLine(
                position=1,
                description="YelloBlade Orange",
                quantity=Field.found(Decimal("5"), _evidence(11)),
                unit="Stk",
                unit_price=Field.found(Decimal("12.3450"), _evidence(11)),
                article_hint=Field.found("YT11YBOR01", _evidence(12)),
                match=ArticleMatch(
                    MatchStatus.NEEDS_REVIEW,
                    article,
                    MatchStrategy.FUZZY,
                    "ähnlich",
                    (MatchCandidate(article, MatchStrategy.FUZZY, 0.93),),
                ),
                source=SourceRef(11, 12, "5 x YelloBlade Orange"),
            ),
        ),
        acknowledged=frozenset({"DUPLICATE:customer_reference"}),
    )


def _mail(mail_id: str, *, uid: int | None, message_id: str, content: str) -> MailMetadata:
    return MailMetadata(
        id=mail_id,
        account_id="a",
        folder="INBOX",
        uidvalidity=7 if uid else None,
        uid=uid,
        message_id=message_id,
        content_hash=content,
        raw_sha256="r",
        sender="k@kunde.de",
        sender_name="",
        subject="Bestellung",
        date_header=None,
        size=100,
    )


def test_migration_is_idempotent_and_versioned(tmp_path: Path) -> None:
    database = Database(tmp_path / "x.db")
    connection = database.connect()
    latest = len(db_module.MIGRATIONS)
    assert database.migrate(connection) == latest
    assert database.migrate(connection) == latest
    assert connection.execute("PRAGMA user_version").fetchone()[0] == latest


def test_newer_database_is_refused(tmp_path: Path) -> None:
    database = Database(tmp_path / "x.db")
    connection = database.connect()
    connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StoreError) as info:
        database.migrate(connection)
    assert "neueren Programmversion" in str(info.value)


def test_upgrade_backs_up_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "x.db")
    connection = database.connect()
    database.migrate(connection)
    monkeypatch.setattr(db_module, "MIGRATIONS", (*db_module.MIGRATIONS, "CREATE TABLE extra (x)"))
    latest = len(db_module.MIGRATIONS)
    assert database.migrate(connection) == latest
    assert (tmp_path / f"x-vor-migration-v{latest - 1}.db").exists()


def test_transaction_rolls_back_completely(conn: sqlite3.Connection, clock: FixedClock) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        OrderRepository(conn).insert(_order(), "angelegt", clock.now())
        raise RuntimeError("Absturz")
    assert OrderRepository(conn).list_refs() == []


def test_order_codec_roundtrip_is_lossless() -> None:
    order = _order()
    assert order_from_dict(order_to_dict(order)) == order


def test_revisions_use_optimistic_locking(conn: sqlite3.Connection, clock: FixedClock) -> None:
    orders = OrderRepository(conn)
    with transaction(conn):
        orders.insert(_order(), "angelegt", clock.now())
    changed = replace(_order(), revision=2, status=OrderStatus.READY, note="geprüft")
    with transaction(conn):
        orders.save(changed, "Notiz", clock.now())
    stale = replace(_order(), revision=2, note="parallel")
    with pytest.raises(ConcurrencyError), transaction(conn):
        orders.save(stale, "parallel", clock.now())
    assert orders.get("o1").note == "geprüft"
    assert [r for r, _, _ in orders.history("o1")] == [1, 2]
    assert orders.revision("o1", 1).note == ""


def test_damaged_payload_is_reported(conn: sqlite3.Connection, clock: FixedClock) -> None:
    with transaction(conn):
        OrderRepository(conn).insert(_order(), "angelegt", clock.now())
        conn.execute("UPDATE orders SET payload = '{\"format\": 1}'")
    with pytest.raises(StoreError) as info:
        OrderRepository(conn).get("o1")
    assert info.value.code == "STORE_RECORD_DAMAGED"


def test_document_numbers_are_unique_in_database(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    orders = OrderRepository(conn)
    with transaction(conn):
        orders.insert(replace(_order("o1"), document_number="AI-2026-000001"), "a", clock.now())
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        orders.insert(replace(_order("o2"), document_number="AI-2026-000001"), "b", clock.now())


def test_only_one_live_export_job_per_order(conn: sqlite3.Connection, clock: FixedClock) -> None:
    with transaction(conn):
        OrderRepository(conn).insert(_order(), "angelegt", clock.now())
    insert = (
        "INSERT INTO export_jobs (id, order_id, revision, profile_id, mode, state, created_at, "
        "updated_at) VALUES (?, 'o1', 1, 'standard', ?, ?, 'now', 'now')"
    )
    conn.execute(insert, ("j1", "live", "failed"))
    conn.execute(insert, ("j2", "live", "committed"))
    conn.execute(insert, ("j3", "test", "done"))
    conn.execute(insert, ("j4", "dry_run", "done"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("j5", "live", "pending"))


def test_counters_are_sequential_and_need_a_transaction(conn: sqlite3.Connection) -> None:
    counters = CounterRepository(conn)
    with pytest.raises(RuntimeError):
        counters.next("x")
    with transaction(conn):
        first = next_document_number(counters, "AI", 2026)
        second = next_document_number(counters, "AI", 2026)
        other_year = next_document_number(counters, "AI", 2027)
    assert (first, second, other_year) == ("AI-2026-000001", "AI-2026-000002", "AI-2027-000001")


def test_counters_stay_unique_across_parallel_connections(tmp_path: Path) -> None:
    database = Database(tmp_path / "par.db")
    database.migrate(database.connect())
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        connection = database.connect()
        for _ in range(20):
            with transaction(connection):
                number = next_document_number(CounterRepository(connection), "AI", 2026)
            with lock:
                results.append(number)
        connection.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 160 and len(set(results)) == 160


def test_journal_details_are_redacted(conn: sqlite3.Connection, clock: FixedClock) -> None:
    journal = JournalRepository(conn)
    journal.append(
        "mail", "m1", "fetched", clock.now(), {"email": "k@kunde.de", "note": "von a@b.de"}
    )
    [(_, event, detail)] = journal.entries("mail", "m1")
    assert event == "fetched"
    assert detail == {"email": "<entfernt>", "note": "von <E-Mail>"}


def test_mail_duplicate_detection_uses_several_keys(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    mails = MailRepository(conn)
    first = MailIdentity("a", "INBOX", 7, 11, "<m1@x>", "h1")
    assert check_mail(mails, first).kind is MailDuplicate.NEW
    mails.insert(
        _mail("m1", uid=11, message_id="<m1@x>", content="h1"), MailState.LOADED, clock.now()
    )
    assert check_mail(mails, first).kind is MailDuplicate.SAME_MAIL
    moved = MailIdentity("a", "Archiv", 3, 99, "<m1@x>", "h1")
    assert check_mail(mails, moved).kind is MailDuplicate.SAME_MESSAGE_ID
    resent = MailIdentity("a", "INBOX", 7, 12, "<m2@x>", "h1")
    check = check_mail(mails, resent)
    assert check.kind is MailDuplicate.SAME_CONTENT and not check.skip
    assert check.existing_mail_id == "m1"
    assert mail_keys(first) == ("msgid:<m1@x>", "uid:a/INBOX/7/11", "content:h1")


def test_idempotency_keys_report_their_owner(conn: sqlite3.Connection, clock: FixedClock) -> None:
    MailRepository(conn).insert(
        _mail("m1", uid=1, message_id="<a>", content="h"), MailState.LOADED, clock.now()
    )
    keys = IdempotencyRepository(conn)
    assert keys.register("msgid:<a>", "mail", clock.now(), mail_id="m1") == (None, None)
    assert keys.register("msgid:<a>", "mail", clock.now(), mail_id="m9") == ("m1", None)


def test_fingerprint_ignores_reply_prefixes_and_whitespace() -> None:
    a = content_fingerprint("K@Kunde.de", "AW: WG: Bestellung 12", "5 x  Rakel\n\nDanke")
    b = content_fingerprint("k@kunde.de", "Bestellung 12", "5 x Rakel Danke")
    assert a == b


def test_customer_key_prefers_customer_number() -> None:
    assert customer_key("10-042", "a@kunde.de") == "kd:10042"
    assert customer_key(None, "Einkauf@Kunde.DE") == "dom:kunde.de"
    assert customer_key("", "") == ""


def test_reference_conflicts_ignore_formatting(conn: sqlite3.Connection, clock: FixedClock) -> None:
    orders = OrderRepository(conn)
    with transaction(conn):
        orders.insert(_order("o1", "PO-251843"), "a", clock.now())
        orders.insert(_order("o2", "po 251843"), "b", clock.now())
    conflicts = reference_conflicts(orders, "o2", "kd:10042", "PO/251843")
    assert [c.id for c in conflicts] == ["o1"]


def test_catalog_versions(conn: sqlite3.Connection, clock: FixedClock) -> None:
    catalogs = CatalogRepository(conn)
    articles = [Article("B2", "Filz"), Article("A1", "Rakel", ("Squeegee",), "Stk", Decimal("19"))]
    with transaction(conn):
        v1 = catalogs.add_version("standard", "katalog.csv", "sha", articles, clock.now())
        v2 = catalogs.add_version("standard", "katalog2.csv", "sha2", articles[:1], clock.now())
        catalogs.activate("standard", v1)
        catalogs.activate("standard", v2)
    assert catalogs.active_version("standard") == v2
    assert [v.active for v in catalogs.versions("standard")] == [True, False]
    assert catalogs.articles(v1) == sorted(articles, key=lambda a: a.number)
