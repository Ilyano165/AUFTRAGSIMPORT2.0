"""IMAP: nur TLS, Zertifikatsprüfung, Timeouts, Wiederaufbau, keine Passwörter in Texten."""

from __future__ import annotations

import imaplib
import logging
import re
import ssl
from pathlib import Path
from typing import Any

import pytest

from icware_auftragsimport.config.schema import (
    MailAccount,
    Settings,
    TlsMode,
    parse_settings,
    settings_to_dict,
)
from icware_auftragsimport.domain.errors import ConfigError
from icware_auftragsimport.ingest.imap import (
    ImapError,
    ImapErrorKind,
    SecureImapClient,
    tls_context,
)

PASSWORD = "Geheim-Passwort-123!"
ACCOUNT = MailAccount(
    id="einkauf", name="Einkauf", host="imap.example.de", username="einkauf@kunde.de"
)


class FakeConnection:
    def __init__(
        self,
        *,
        encrypted: bool = True,
        login_error: Exception | None = None,
        size: int = 100,
        body: bytes = b"From: a@b.de\\r\\n\\r\\nx",
        network_failures: int = 0,
    ) -> None:
        self.encrypted = encrypted
        self.login_error = login_error
        self.size = size
        self.body = body
        self.network_failures = network_failures
        self.commands: list[str] = []

    def login(self, user: str, password: str) -> tuple[str, list[Any]]:
        if self.login_error:
            raise self.login_error
        return "OK", [b"ok"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[Any]]:
        return "OK", [b"1"]

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self.commands.append(args[-1])
        if self.network_failures:
            self.network_failures -= 1
            raise OSError("Verbindung getrennt")
        if "RFC822.SIZE" in args[-1]:
            return "OK", [f"1 (UID {args[0]} RFC822.SIZE {self.size})".encode()]
        return "OK", [(b"1 (UID 1 BODY[]<0> {%d}" % len(self.body), self.body), b")"]

    def logout(self) -> tuple[str, list[Any]]:
        return "BYE", []


class Harness:
    def __init__(self, *connections: FakeConnection, error: Exception | None = None) -> None:
        self.connections = list(connections)
        self.error = error
        self.contexts: list[ssl.SSLContext] = []
        self.password_calls = 0
        self.sleeps: list[float] = []

    def factory(
        self, host: str, port: int, context: ssl.SSLContext, timeout: float, mode: TlsMode
    ) -> FakeConnection:
        self.contexts.append(context)
        assert timeout > 0
        if self.error:
            raise self.error
        return self.connections.pop(0)

    def password(self) -> str:
        self.password_calls += 1
        return PASSWORD

    def client(self) -> SecureImapClient:
        return SecureImapClient(
            ACCOUNT,
            self.password,
            factory=self.factory,
            is_encrypted=lambda c: c.encrypted,
            sleep=self.sleeps.append,
            jitter=lambda: 1.0,
        )


def test_tls_context_cannot_be_weak() -> None:
    context = tls_context()
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_unencrypted_connection_never_receives_the_password() -> None:
    harness = Harness(FakeConnection(encrypted=False))
    with pytest.raises(ImapError) as info:
        harness.client().message_size("1")
    assert info.value.kind is ImapErrorKind.TLS and harness.password_calls == 0


def test_authentication_failure_hides_password_and_does_not_retry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = imaplib.IMAP4.error(f"LOGIN failed for einkauf@kunde.de using {PASSWORD}")
    harness = Harness(FakeConnection(login_error=error))
    with caplog.at_level(logging.DEBUG), pytest.raises(ImapError) as info:
        harness.client().message_size("1")
    text = str(info.value) + caplog.text
    assert info.value.kind is ImapErrorKind.AUTHENTICATION
    assert PASSWORD not in text and "einkauf@kunde.de" not in str(info.value)
    assert len(harness.contexts) == 1


def test_certificate_error_is_never_retried() -> None:
    harness = Harness(error=ssl.SSLCertVerificationError("certificate verify failed"))
    with pytest.raises(ImapError) as info:
        harness.client().message_size("1")
    assert (
        info.value.kind is ImapErrorKind.TLS and harness.sleeps == [] and len(harness.contexts) == 1
    )


def test_network_errors_reconnect_with_backoff() -> None:
    harness = Harness(
        FakeConnection(network_failures=1), FakeConnection(network_failures=1), FakeConnection()
    )
    assert harness.client().message_size("7") == 100
    assert harness.sleeps == [2.0, 4.0] and len(harness.contexts) == 3


def test_retries_are_bounded() -> None:
    harness = Harness(*[FakeConnection(network_failures=1) for _ in range(4)])
    with pytest.raises(ImapError) as info:
        harness.client().message_size("7")
    assert info.value.kind is ImapErrorKind.NETWORK and len(harness.sleeps) == 3


def test_oversized_mail_is_never_downloaded() -> None:
    connection = FakeConnection(size=50_000)
    with pytest.raises(ImapError) as info:
        Harness(connection).client().fetch("1", max_bytes=1000)
    assert info.value.kind is ImapErrorKind.OVERSIZED
    assert not any("BODY" in c for c in connection.commands)


def test_server_sending_more_than_limit_is_rejected() -> None:
    connection = FakeConnection(size=10, body=b"x" * 5000)
    with pytest.raises(ImapError) as info:
        Harness(connection).client().fetch("1", max_bytes=1000)
    assert info.value.kind is ImapErrorKind.OVERSIZED
    assert any("<0.1001>" in c for c in connection.commands)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"tls_mode": "implicit", "port": 143}, "port"),
        ({"tls_mode": "starttls", "port": 993}, "tls_mode"),
        ({"tls_mode": "none"}, "tls_mode"),
        ({"password": "x"}, "password"),
    ],
)
def test_insecure_account_configuration_is_rejected(changes: dict[str, object], field: str) -> None:
    data = settings_to_dict(Settings(accounts=(ACCOUNT,)))
    data["accounts"][0].update(changes)
    with pytest.raises(ConfigError) as info:
        parse_settings(data)
    assert field in " ".join(info.value.details)


def test_no_code_disables_tls_or_uses_unsafe_primitives() -> None:
    source = Path(__file__).resolve().parents[2] / "src"
    forbidden = re.compile(
        r"CERT_NONE|_create_unverified_context|check_hostname\s*=\s*False|verify\s*=\s*False|"
        r"(?<![.\w])eval\(|(?<![.\w])exec\(|pickle|marshal|shell\s*=\s*True|os\.system|yaml\.load|"
        r"ElementTree\.fromstring|ElementTree\.parse|minidom|xml\.sax\.parse|lxml"
    )
    assert forbidden.search("exec(code)")
    assert forbidden.search("x = eval(text)")
    assert not forbidden.search("dialog.exec()")
    hits = [
        f"{path.name}:{number}"
        for path in source.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if forbidden.search(line) and "noqa: security" not in line
    ]
    assert hits == []
