"""Live-Tests gegen echte Mail-Anbieter (nur lesend, nur mit ausdrücklich gesetzten Zugangsdaten).

Je Anbieter: ``ICWARE_IMAP_<KENNUNG>_USER`` und ``ICWARE_IMAP_<KENNUNG>_PASSWORD``, optional
``_HOST``, ``_PORT``, ``_FOLDER``,
``_STARTTLS=1`` (sonst SSL/TLS). Ohne Zugangsdaten wird der Anbieter übersprungen und gilt
als NOT TESTED. Der Test öffnet den Ordner nur lesend, lädt höchstens 20 Mails in eine
Wegwerf-Datenbank und prüft, dass sich keine Markierung geändert hat.
Status und Voraussetzungen je Anbieter: docs/mail-anbieter.md.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from fetchkit import Harness
from icware_auftragsimport.app.fetching import imap_sources, probe
from icware_auftragsimport.config.schema import AuthMethod, MailAccount, TlsMode
from icware_auftragsimport.ingest.imap import SecureImapClient
from icware_auftragsimport.security.credentials import MemoryCredentialStore, credential_key
from icware_auftragsimport.services.mail_fetch import (
    CancelToken,
    FetchErrorKind,
    FetchFailure,
    fetch_target,
)

PROVIDERS = {
    "IONOS": "imap.ionos.de",
    "STRATO": "imap.strato.de",
    "ALLINKL": "",  # Server je Kundenkonto (KAS), über _HOST angeben
    "GMX": "imap.gmx.net",
    "WEBDE": "imap.web.de",
    "GMAIL": "imap.gmail.com",
    "YAHOO": "imap.mail.yahoo.com",
    "TONLINE": "secureimap.t-online.de",
}


def _credentials(key: str) -> tuple[str, str, str, int, str, TlsMode] | None:
    env = os.environ
    user, password = env.get(f"ICWARE_IMAP_{key}_USER"), env.get(f"ICWARE_IMAP_{key}_PASSWORD")
    host = env.get(f"ICWARE_IMAP_{key}_HOST") or PROVIDERS[key]
    if not (user and password and host):
        return None
    starttls = env.get(f"ICWARE_IMAP_{key}_STARTTLS") == "1"
    port = int(env.get(f"ICWARE_IMAP_{key}_PORT", "143" if starttls else "993"))
    folder = env.get(f"ICWARE_IMAP_{key}_FOLDER", "INBOX")
    return user, password, host, port, folder, TlsMode.STARTTLS if starttls else TlsMode.IMPLICIT


def _flags(account: MailAccount, password: str) -> dict[int, frozenset[str]]:
    client = SecureImapClient(account, lambda: password, max_attempts=1)
    try:
        client.open_mailbox(account.folder)
        uids = client.search_uids()[-20:]
        return {e.uid: e.flags - {"\\Recent"} for e in client.envelopes(uids, 512)}
    finally:
        client.close()


@pytest.mark.parametrize("key", sorted(PROVIDERS))
def test_live_provider_read_only(key: str, tmp_path: Path) -> None:
    found = _credentials(key)
    if found is None:
        pytest.skip(f"NOT TESTED – keine Zugangsdaten für {key} (ICWARE_IMAP_{key}_USER/_PASSWORD)")
    user, password, host, port, folder, mode = found
    harness = Harness(tmp_path / "bestand")
    account = replace(
        harness.account,
        host=host,
        port=port,
        tls_mode=mode,
        username=user,
        folder=folder,
        max_messages_per_run=20,
        allowed_senders=(),
    )
    store = MemoryCredentialStore()
    store.set(credential_key(account.id), password)
    sources = imap_sources(store)
    before = _flags(account, password)

    result = probe(account, lambda hooks: sources(account, hooks))
    assert result.failure is None, f"Verbindungstest: {result.failure}"
    summary = harness.service(sources).run([harness.target(account=account)], CancelToken())
    assert summary.failure is None, f"Abruf: {summary.failure}"

    assert _flags(account, password) == before, "Abruf hat Markierungen verändert"


def test_microsoft_365_oauth_accounts_are_rejected_as_unsupported(tmp_path: Path) -> None:
    """Microsoft 365 verlangt OAuth2; ohne Implementierung: klare Meldung, kein Login-Versuch."""
    harness = Harness(tmp_path / "bestand")
    m365 = replace(
        harness.account,
        host="outlook.office365.com",
        username="a@firma.de",
        auth_method=AuthMethod.OAUTH2,
    )
    settings = harness.settings_with(
        accounts=tuple(m365 if a.id == m365.id else a for a in harness.settings.accounts)
    )
    assert fetch_target(settings) == FetchFailure(FetchErrorKind.UNSUPPORTED_AUTH, m365.id)
    assert "OAuth2" in FetchErrorKind.UNSUPPORTED_AUTH.action
