"""Echter IMAP-Abruf gegen einen lokalen Dovecot-Server über den Produktions-Codepfad.

Weg: Anmeldespeicher → ``imap_sources`` → ``ImapMailSource`` → ``SecureImapClient`` →
TLS (Systemspeicher, Test-CA per ``SSL_CERT_FILE``) → Dovecot → Abrufdienst → SQLite.
Ohne Dovecot (etwa unter Windows) werden diese Tests übersprungen, nie als bestanden gezählt.
"""

from __future__ import annotations

import re
import socket
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest

from fetchkit import Harness, build_mail, captured_logs, order_text
from icware_auftragsimport.app.fetching import imap_sources, probe
from icware_auftragsimport.config.schema import MailAccount, TlsMode
from icware_auftragsimport.infrastructure.repositories import OrderRepository
from icware_auftragsimport.ingest.imap import Envelope, MailboxInfo
from icware_auftragsimport.ingest.sources import ImapMailSource, MailSource
from icware_auftragsimport.security.credentials import MemoryCredentialStore, credential_key
from icware_auftragsimport.security.limits import MailLimits
from icware_auftragsimport.services.mail_fetch import (
    CancelToken,
    FetchErrorKind,
    FetchSummary,
    SourceFactory,
    SourceHooks,
)
from imapserver import PASSWORD, DovecotServer, unavailable_reason
from mailcorpus import corpus

REASON = unavailable_reason()
pytestmark = pytest.mark.skipif(bool(REASON), reason=f"NOT TESTED – {REASON}")
STATE_FOR = {"order": "extracted", "failed": "failed", "known": "duplicate"}


@pytest.fixture(scope="module")
def dovecot() -> Iterator[DovecotServer]:
    server = DovecotServer.create().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def trust_test_ca(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if not REASON:
        server: DovecotServer = request.getfixturevalue("dovecot")
        monkeypatch.setenv("SSL_CERT_FILE", str(server.ca_file))


@pytest.fixture
def user(dovecot: DovecotServer, request: pytest.FixtureRequest) -> str:
    return dovecot.add_user(re.sub(r"[^a-z0-9]", "", request.node.name.lower())[:40])


@pytest.fixture
def harness(tmp_path: Any) -> Harness:
    return Harness(tmp_path / "bestand")


def account_for(
    harness: Harness,
    server: DovecotServer,
    user: str,
    *,
    starttls: bool = False,
    folder: str = "INBOX",
    host: str = "localhost",
) -> MailAccount:
    return replace(
        harness.account,
        host=host,
        port=server.imap_port if starttls else server.imaps_port,
        tls_mode=TlsMode.STARTTLS if starttls else TlsMode.IMPLICIT,
        username=user,
        folder=folder,
    )


def production_sources(password: str = PASSWORD) -> SourceFactory:
    store = MemoryCredentialStore()
    store.set(credential_key("bestellungen"), password)
    return imap_sources(store)


def fetch(
    harness: Harness,
    account: MailAccount,
    *,
    sources: SourceFactory | None = None,
    limits: MailLimits | None = None,
    token: CancelToken | None = None,
) -> FetchSummary:
    service = harness.service(
        sources or production_sources(), *(() if limits is None else (limits,))
    )
    return service.run([harness.target(account=account)], token or CancelToken())


def seed_corpus(server: DovecotServer, user: str) -> None:
    for uid, mail in enumerate(corpus(), start=1):
        server.save(user, mail.raw)
        for flag in mail.flags:
            server.add_flags(user, uid, flag)


def order_for(harness: Harness, uid: int) -> Any:
    row = harness.conn.execute(
        "SELECT o.id FROM orders o JOIN mails m ON m.id = o.mail_id "
        "WHERE m.uid = ? AND m.uidvalidity != 1",
        (uid,),
    ).fetchone()
    return OrderRepository(harness.conn).get(row["id"])


# ---------- Vollständiger Ablauf ----------


def test_real_imap_corpus_end_to_end(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    seed_corpus(dovecot, user)
    before = dovecot.flags(user)
    account = account_for(harness, dovecot, user)

    summary = fetch(harness, account)

    assert summary.ok, summary.failure
    counts = (
        summary.checked,
        summary.orders,
        summary.known,
        summary.spam,
        summary.auto_replies,
        summary.sender_filtered,
        summary.failed,
    )
    assert counts == (16, 10, 1, 2, 1, 1, 1)
    mails = corpus()
    expected = {uid: STATE_FOR[m.expect] for uid, m in enumerate(mails, 1) if m.expect in STATE_FOR}
    assert dict(harness.mail_states()) == expected
    assert order_for(harness, 2).customer_reference.value == "HS-0977"
    assert len(order_for(harness, 2).lines) == 2
    assert len(order_for(harness, 3).lines) == 2
    assert len(order_for(harness, 4).lines) == 2
    delivery = order_for(harness, 14).delivery_address.value
    assert delivery is not None and delivery.street == "Rheinweg"
    umlauts = order_for(harness, 15).invoice_address.value
    assert umlauts is not None and umlauts.city == "Köln"
    assert "Café Müller & Söhne" in (umlauts.company, umlauts.name)
    raw = harness.conn.execute(
        "SELECT raw FROM mails WHERE uid = 1 AND uidvalidity != 1"
    ).fetchone()["raw"]
    assert b"Message-ID: <k01@gasthaus.example>" in raw and b"GH-2026-2001" in raw

    assert dovecot.flags(user) == before, "Abruf hat Markierungen verändert"

    again = fetch(harness, account)
    assert again.ok and (again.checked, again.known, again.orders) == (16, 12, 0)
    assert (again.spam, again.auto_replies, again.sender_filtered) == (2, 1, 1)
    assert harness.count("orders", "mail_id IN (SELECT id FROM mails WHERE uidvalidity != 1)") == 10
    assert dovecot.flags(user) == before


def test_starttls(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    dovecot.save(user, build_mail())
    summary = fetch(harness, account_for(harness, dovecot, user, starttls=True))
    assert summary.ok and summary.orders == 1


def test_umlaut_folder(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    dovecot.create_mailbox(user, "Aufträge")
    dovecot.save(user, build_mail(), mailbox="Aufträge")
    summary = fetch(harness, account_for(harness, dovecot, user, folder="Aufträge"))
    assert summary.ok and summary.orders == 1
    assert "\\Seen" not in dovecot.flags(user, "Aufträge")[1]


def test_new_uidvalidity_creates_no_duplicates(
    dovecot: DovecotServer, user: str, harness: Harness
) -> None:
    dovecot.save(user, build_mail(message_id="<u1@x>"))
    dovecot.save(user, build_mail(order_text("GH-2"), message_id="<u2@x>"))
    account = account_for(harness, dovecot, user)
    assert fetch(harness, account).orders == 2
    dovecot.set_uidvalidity(user, "INBOX", 4_000_000_000)
    summary = fetch(harness, account)
    assert summary.ok and (summary.orders, summary.known) == (0, 2)
    states = sorted(state for _uid, state in harness.mail_states())
    assert states == ["duplicate", "duplicate", "extracted", "extracted"]


def test_oversized_mail_is_not_downloaded(
    dovecot: DovecotServer, user: str, harness: Harness
) -> None:
    dovecot.save(user, build_mail(order_text() + "x" * 6000))
    summary = fetch(
        harness, account_for(harness, dovecot, user), limits=MailLimits(max_message_bytes=2000)
    )
    assert summary.ok and (summary.oversized, summary.orders) == (1, 0)
    assert harness.mail_states() == []


# ---------- Fehler ----------


def test_wrong_password_fails_cleanly_and_never_logs_it(
    dovecot: DovecotServer, user: str, harness: Harness
) -> None:
    dovecot.save(user, build_mail())
    with captured_logs() as lines:
        summary = fetch(
            harness, account_for(harness, dovecot, user), sources=production_sources("Falsch-987!")
        )
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.AUTHENTICATION
    assert "Falsch-987!" not in "\n".join(lines) and harness.mail_states() == []


def test_untrusted_certificate_is_rejected(
    dovecot: DovecotServer, user: str, harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE")
    summary = fetch(harness, account_for(harness, dovecot, user))
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.TLS


def test_hostname_mismatch_is_rejected(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    summary = fetch(harness, account_for(harness, dovecot, user, host="127.0.0.1"))
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.TLS


def test_missing_folder(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    summary = fetch(harness, account_for(harness, dovecot, user, folder="Gibt-es-nicht"))
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.MAILBOX


def test_timeout_when_server_never_answers(harness: Harness) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def hold() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    account = replace(
        harness.account, host="localhost", port=listener.getsockname()[1], username="u"
    )

    def sources(acc: MailAccount, _hooks: SourceHooks) -> MailSource:
        return ImapMailSource(acc, lambda: PASSWORD, sleep=lambda _s: None, timeout=0.5)

    try:
        summary = fetch(harness, account, sources=sources)
    finally:
        stop.set()
        thread.join(5)
        for conn in accepted:
            conn.close()
        listener.close()
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.TIMEOUT
    assert len(accepted) == 4, "vier Versuche mit Wiederholung"


# ---------- Verbindungstest und Abbruch ----------


def test_connection_probe_only_reads(dovecot: DovecotServer, user: str, harness: Harness) -> None:
    seed_corpus(dovecot, user)
    before = dovecot.flags(user)
    account = account_for(harness, dovecot, user)
    sources = production_sources()
    result = probe(account, lambda hooks: sources(account, hooks))
    assert result.failure is None and (result.messages, result.folder) == (16, "INBOX")
    assert dovecot.flags(user) == before and harness.mail_states() == []


@dataclass
class CancelAfter:
    """Echte Quelle; bricht nach dem n-ten Download ab."""

    inner: MailSource
    token: CancelToken
    after: int
    downloads: int = 0

    @property
    def account_id(self) -> str:
        return self.inner.account_id

    @property
    def folder(self) -> str:
        return self.inner.folder

    def open(self) -> MailboxInfo:
        return self.inner.open()

    def uids(self) -> list[int]:
        return self.inner.uids()

    def envelopes(self, uids: Sequence[int]) -> list[Envelope]:
        return self.inner.envelopes(uids)

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        self.downloads += 1
        if self.downloads == self.after:
            self.token.cancel()
        return self.inner.fetch(uid, size, max_bytes)

    def abort(self) -> None:
        self.inner.abort()

    def close(self) -> None:
        self.inner.close()


def test_cancel_mid_run_is_consistent_and_next_run_completes(
    dovecot: DovecotServer, user: str, harness: Harness
) -> None:
    seed_corpus(dovecot, user)
    before = dovecot.flags(user)
    account = account_for(harness, dovecot, user)
    token = CancelToken()
    real = production_sources()

    def cancelling(acc: MailAccount, hooks: SourceHooks) -> MailSource:
        return CancelAfter(real(acc, hooks), token, after=3)  # type: ignore[return-value]

    summary = fetch(harness, account, sources=cancelling, token=token)
    assert summary.cancelled and summary.failure is None and summary.orders == 2
    orphans = harness.count(
        "mails",
        "state = 'extracted' AND uidvalidity != 1 AND id NOT IN (SELECT mail_id FROM orders)",
    )
    keyless = harness.count(
        "orders",
        "mail_id IN (SELECT id FROM mails WHERE uidvalidity != 1) "
        "AND id NOT IN (SELECT order_id FROM idempotency_keys WHERE order_id IS NOT NULL)",
    )
    assert (orphans, keyless) == (0, 0)
    assert dovecot.flags(user) == before

    rest = fetch(harness, account)
    assert rest.ok and (rest.orders, rest.known) == (8, 3)
    assert harness.count("orders", "mail_id IN (SELECT id FROM mails WHERE uidvalidity != 1)") == 10
    assert dovecot.flags(user) == before


def test_server_reachable_by_extra_name_with_fixed_ports(harness: Harness) -> None:
    """Testserver für einen anderen Rechner: zusätzliche Adresse im Zertifikat, feste Ports."""
    from imapserver import _free_port  # noqa: PLC0415

    ports = (_free_port(), _free_port())
    server = DovecotServer.create(names=("127.0.0.1",), ports=ports).start()
    try:
        assert (server.imap_port, server.imaps_port) == ports
        server.save(server.add_user("fern"), build_mail())
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("SSL_CERT_FILE", str(server.ca_file))
            account = replace(account_for(harness, server, "fern"), host="127.0.0.1")
            summary = fetch(harness, account)
    finally:
        server.stop()
    assert summary.ok and summary.orders == 1
