"""IMAP-Client (Ordner, Auflistung, Kopfdaten, Wiederaufbau), Vorprüfung, Testpostfach."""

from __future__ import annotations

import hashlib
import imaplib
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from fetchkit import Harness, build_mail
from icware_auftragsimport.config.schema import MailAccount, TlsMode
from icware_auftragsimport.ingest.imap import (
    Envelope,
    ImapError,
    ImapErrorKind,
    SecureImapClient,
    classify,
    encode_mailbox,
    parse_envelopes,
)
from icware_auftragsimport.ingest.screening import Verdict, read_headers, screen, sender_address
from icware_auftragsimport.ingest.sources import FolderMailSource, ImapMailSource
from icware_auftragsimport.services.mail_fetch import CancelToken

ACCOUNT = MailAccount(
    id="einkauf", name="Einkauf", host="imap.example.de", username="u", folder="Aufträge"
)


class Transport:
    """IMAP-Verbindung im Speicher mit Befehlsprotokoll."""

    def __init__(self, *, uids: str = "3 5 9", fail_on: int | None = None) -> None:
        self.sock = object()
        self.calls: list[tuple[str, ...]] = []
        self.uids = uids
        self.fail_on = fail_on
        self.shutdowns = 0

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        self.calls.append(("LOGIN",))
        return "OK", [b"ok"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        self.calls.append(("SELECT", mailbox, str(readonly)))
        if mailbox == '"Fehlt"':
            return "NO", [b"Mailbox doesn't exist"]
        return "OK", [b"12"]

    def response(self, code: str) -> tuple[str, list[Any]]:
        return code, [b"4711"]

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self.calls.append((command, *args))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise OSError("Verbindung getrennt")
        if command == "SEARCH":
            return "OK", [self.uids.encode()]
        uids = args[0].split(",")
        data: list[Any] = []
        for uid in uids:
            head = b"From: a@b.de\r\nSubject: x\r\n\r\n"
            data += [
                (f"1 (UID {uid} RFC822.SIZE 99 BODY[HEADER]<0> {{{len(head)}}}".encode(), head)
            ]
            data.append(b" FLAGS (\\Seen))")
        if "BODY.PEEK[]" in args[-1]:
            return "OK", [(b"1 (UID 3 BODY[]<0> {5}", b"Hallo"), b")"]
        return "OK", data

    def shutdown(self) -> None:
        self.shutdowns += 1

    def logout(self) -> tuple[str, list[Any]]:
        return "BYE", []


def client_with(*transports: Transport, **kwargs: Any) -> tuple[SecureImapClient, list[float]]:
    pending = list(transports)
    sleeps: list[float] = []

    def factory(*_args: object) -> Transport:
        return pending.pop(0)

    client = SecureImapClient(
        ACCOUNT,
        lambda: "pw",
        factory=factory,  # type: ignore[arg-type]
        is_encrypted=lambda _c: True,
        sleep=sleeps.append,
        jitter=lambda: 0.0,
        **kwargs,
    )
    return client, sleeps


def test_mailbox_names_use_modified_utf7_and_quotes() -> None:
    assert encode_mailbox("INBOX") == '"INBOX"'
    assert encode_mailbox("Entwürfe") == '"Entw&APw-rfe"'
    assert encode_mailbox("Aufträge 2026") == '"Auftr&AOQ-ge 2026"'
    assert encode_mailbox('A&B "x"') == '"A&-B \\"x\\""'


def test_open_mailbox_is_read_only_and_reads_uidvalidity() -> None:
    transport = Transport()
    client, _ = client_with(transport)
    info = client.open_mailbox("Aufträge")
    assert (info.folder, info.uidvalidity, info.exists) == ("Aufträge", 4711, 12)
    assert ("SELECT", '"Auftr&AOQ-ge"', "True") in transport.calls


def test_missing_folder_is_a_mailbox_error_without_retry() -> None:
    client, sleeps = client_with(Transport())
    with pytest.raises(ImapError) as info:
        client.open_mailbox("Fehlt")
    assert info.value.kind is ImapErrorKind.MAILBOX and sleeps == []


def test_search_lists_all_undeleted_not_only_unseen() -> None:
    transport = Transport(uids="9 3 5")
    client, _ = client_with(transport)
    client.open_mailbox("INBOX")
    assert client.search_uids() == [3, 5, 9]
    assert ("SEARCH", "UNDELETED") in transport.calls


def test_envelopes_are_fetched_in_batches_with_peek() -> None:
    transport = Transport()
    client, _ = client_with(transport)
    client.open_mailbox("INBOX")
    found = client.envelopes(list(range(1, 251)), 4096)
    fetches = [c for c in transport.calls if c[0] == "FETCH"]
    assert len(fetches) == 3 and len(found) == 250
    assert all("BODY.PEEK[HEADER]<0.4096>" in c[-1] for c in fetches)
    assert found[0].flags == frozenset({"\\Seen"}) and found[0].size == 99


def test_fetch_with_known_size_skips_size_query() -> None:
    transport = Transport()
    client, _ = client_with(transport)
    client.open_mailbox("INBOX")
    assert client.fetch("3", 1000, size=5) == b"Hallo"
    assert not any(
        "RFC822.SIZE" in c[-1] and len(c) == 3
        for c in transport.calls
        if c[0] == "FETCH" and "BODY" not in c[-1]
    )
    assert [c for c in transport.calls if c[0] == "FETCH"][-1][-1].startswith("(BODY.PEEK[]")


def test_reconnect_selects_the_mailbox_again() -> None:
    first, second = Transport(fail_on=3), Transport()
    retries: list[tuple[int, ImapErrorKind]] = []
    client, sleeps = client_with(first, second, on_retry=lambda n, k, _d: retries.append((n, k)))
    client.open_mailbox("INBOX")
    assert client.search_uids() == [3, 5, 9]
    assert second.calls[:3] == [("LOGIN",), ("SELECT", '"INBOX"', "True"), ("SEARCH", "UNDELETED")]
    assert len(sleeps) == 1 and retries == [(1, ImapErrorKind.NETWORK)]


def test_abort_shuts_the_socket_down() -> None:
    transport = Transport()
    client, _ = client_with(transport)
    client.open_mailbox("INBOX")
    client.abort()
    assert transport.shutdowns == 1


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        TimeoutError(),
        imaplib.IMAP4.abort("socket error: The read operation timed out"),
    ],
)
def test_timeouts_are_their_own_retryable_kind(exc: BaseException) -> None:
    assert classify(exc).kind is ImapErrorKind.TIMEOUT


def test_other_errors_keep_their_kind() -> None:
    assert classify(OSError("reset")).kind is ImapErrorKind.NETWORK
    assert classify(ssl.SSLError("bad")).kind is ImapErrorKind.TLS
    assert classify(imaplib.IMAP4.error("NO")).kind is ImapErrorKind.PROTOCOL


def test_envelope_parser_handles_flags_before_and_after_literal() -> None:
    data = [
        (b"1 (UID 7 RFC822.SIZE 120 BODY[HEADER]<0> {5}", b"From:"),
        b" FLAGS (\\Seen $Junk))",
        (b"2 (UID 9 RFC822.SIZE 99 FLAGS () BODY[HEADER]<0> {2}", b"X:"),
        b")",
        b"3 (FLAGS (\\Seen))",
    ]
    assert parse_envelopes(data) == [
        Envelope(7, 120, frozenset({"\\Seen", "$Junk"}), b"From:"),
        Envelope(9, 99, frozenset(), b"X:"),
    ]


# ---------- Vorprüfung ----------


def _envelope(flags: frozenset[str] = frozenset(), **headers: str) -> Envelope:
    head = "".join(f"{k.replace('_', '-')}: {v}\r\n" for k, v in headers.items()).encode()
    return Envelope(1, 100, flags, head + b"\r\n")


@pytest.mark.parametrize(
    "headers",
    [
        {"X_Spam_Flag": "YES"},
        {"X_Spam_Status": "Yes, score=12"},
        {"X_Spam": "yes"},
        {"X_Forefront_Antispam_Report": "SFV:NSPM;SCL:6"},
        {"X_MS_Exchange_Organization_SCL": "7"},
        {"Subject": "***SPAM*** Gewinn"},
        {"Subject": "=?utf-8?q?=5BSPAM=5D_Angebot?="},
    ],
)
def test_server_spam_markings(headers: dict[str, str]) -> None:
    assert screen(_envelope(**headers)).verdict is Verdict.SPAM


@pytest.mark.parametrize(
    "headers",
    [
        {"X_Spam_Flag": "NO"},
        {"X_Spam_Status": "No, score=0.1"},
        {"X_Forefront_Antispam_Report": "SFV:NSPM;SCL:1"},
        {"X_MS_Exchange_Organization_SCL": "-1"},
        {"Subject": "Bestellung Spamfilter-Ersatzteile"},
    ],
)
def test_clean_markings_are_accepted(headers: dict[str, str]) -> None:
    assert screen(_envelope(**headers)).verdict is Verdict.ACCEPT


def test_junk_flag_and_not_junk_override() -> None:
    assert screen(_envelope(frozenset({"Junk"}))).verdict is Verdict.SPAM
    marked = _envelope(frozenset({"$Junk", "$NotJunk"}), X_Spam_Flag="YES")
    assert screen(marked).verdict is Verdict.ACCEPT


@pytest.mark.parametrize(
    "headers",
    [
        {"Auto_Submitted": "auto-replied (vacation)"},
        {"X_Autorespond": "1"},
        {"Content_Type": 'multipart/report; report-type="delivery-status"'},
        {"From": "Mail Delivery System <MAILER-DAEMON@mx.example>"},
        {"From": "postmaster@kunde.de"},
    ],
)
def test_automatic_answers(headers: dict[str, str]) -> None:
    assert screen(_envelope(**headers)).verdict is Verdict.AUTO_REPLY


def test_generated_and_bulk_mails_are_accepted() -> None:
    envelope = _envelope(Auto_Submitted="auto-generated", Precedence="bulk", From="shop@kunde.de")
    assert screen(envelope).verdict is Verdict.ACCEPT


def test_broken_headers_do_not_crash_screening() -> None:
    assert screen(Envelope(1, 1, frozenset(), b"\xff\xfe\x00garbage\r\n")).verdict is Verdict.ACCEPT
    assert sender_address(read_headers(b"From: a@b.de, c@d.de\r\n\r\n")) == ""
    assert (
        sender_address(read_headers(b"From: =?utf-8?q?M=C3=BCller?= <M@Kunde.DE>\r\n\r\n"))
        == "m@kunde.de"
    )


# ---------- Testpostfach ----------


def _digest(directory: Path) -> dict[str, tuple[str, int]]:
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(directory.iterdir())
    }


def test_folder_source_reads_without_modifying(tmp_path: Path) -> None:
    (tmp_path / "b.eml").write_bytes(build_mail(message_id="<b@x>"))
    (tmp_path / "a.eml").write_bytes(build_mail(message_id="<a@x>"))
    (tmp_path / "notiz.txt").write_text("ignorieren")
    before = _digest(tmp_path)
    source = FolderMailSource("test", tmp_path)
    info = source.open()
    uids = source.uids()
    envelopes = source.envelopes(uids)
    raws = [source.fetch(e.uid, e.size, 10_000_000) for e in envelopes]
    assert info.exists == 2 and len(raws) == 2 and b"<a@x>" in raws[0]
    assert _digest(tmp_path) == before


def test_folder_source_uids_are_stable_when_files_are_added(tmp_path: Path) -> None:
    (tmp_path / "b.eml").write_bytes(build_mail())
    first = FolderMailSource("test", tmp_path)
    first.open()
    [uid_b] = first.uids()
    (tmp_path / "a.eml").write_bytes(build_mail(message_id="<a@x>"))
    second = FolderMailSource("test", tmp_path)
    second.open()
    assert uid_b in second.uids() and len(second.uids()) == 2


def test_folder_source_errors(tmp_path: Path) -> None:
    with pytest.raises(ImapError) as info:
        FolderMailSource("test", tmp_path / "fehlt").open()
    assert info.value.kind is ImapErrorKind.MAILBOX
    (tmp_path / "gross.eml").write_bytes(build_mail("x" * 5000))
    source = FolderMailSource("test", tmp_path)
    source.open()
    [uid] = source.uids()
    with pytest.raises(ImapError) as big:
        source.fetch(uid, 0, 100)
    assert big.value.kind is ImapErrorKind.OVERSIZED


def test_starttls_mode_is_kept() -> None:
    account = MailAccount(
        id="x", name="x", host="h", port=143, username="u", tls_mode=TlsMode.STARTTLS
    )
    seen: list[TlsMode] = []

    def factory(
        host: str, port: int, context: ssl.SSLContext, timeout: float, mode: TlsMode
    ) -> Transport:
        seen.append(mode)
        return Transport()

    client = SecureImapClient(account, lambda: "pw", factory=factory, is_encrypted=lambda _c: True)  # type: ignore[arg-type]
    client.open_mailbox("INBOX")
    assert seen == [TlsMode.STARTTLS]


def test_cancel_during_connect_ends_after_timeout_without_retry(tmp_path: Path) -> None:
    """Abbruch im Verbindungsaufbau: wirkt spätestens nach dem Zeitlimit, kein weiterer Versuch."""

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def hold() -> None:
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                continue

    server = threading.Thread(target=hold, daemon=True)
    server.start()
    harness = Harness(tmp_path / "bestand")
    account = MailAccount(
        id="bestellungen", name="x", host="localhost", port=listener.getsockname()[1], username="u"
    )
    service = harness.service(
        lambda acc, hooks: ImapMailSource(acc, lambda: "pw", sleep=hooks.sleep, timeout=1.0)
    )
    token = CancelToken()

    def cancel() -> None:
        time.sleep(0.2)
        token.cancel()
        service.abort()

    canceller = threading.Thread(target=cancel)
    canceller.start()
    started = time.monotonic()
    try:
        summary = service.run([harness.target(account=account)], token)
    finally:
        canceller.join(5)
        stop.set()
        server.join(5)
        for conn in accepted:
            conn.close()
        listener.close()
    assert summary.cancelled and summary.failure is None
    assert len(accepted) == 1 and time.monotonic() - started < 3
