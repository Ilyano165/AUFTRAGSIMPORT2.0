"""Mailquellen: echtes IMAP-Postfach oder Testpostfach (Ordner mit .eml-Dateien).

Beide Quellen liefern dieselben Daten (UIDs, Kopfdaten, Rohmail) an denselben Abrufdienst.
Keine Quelle verändert Mails: IMAP öffnet den Ordner lesend und lädt mit ``BODY.PEEK``,
das Testpostfach öffnet Dateien nur zum Lesen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from ..config.schema import MailAccount
from ..domain.errors import UserMessage
from .imap import (
    DEFAULT_TIMEOUT,
    MAX_ATTEMPTS,
    Envelope,
    Factory,
    ImapError,
    ImapErrorKind,
    MailboxInfo,
    RetryNotice,
    SecureImapClient,
    default_factory,
)
from .screening import HEADER_BYTES

TEST_FOLDER = "Testpostfach"
MAX_TEST_FILES = 2_000


class MailSource(Protocol):
    """Was der Abrufdienst von einer Quelle braucht."""

    account_id: str
    folder: str

    def open(self) -> MailboxInfo:
        """Verbindet und öffnet den Ordner lesend."""
        ...

    def uids(self) -> list[int]:
        """UIDs aller nicht gelöschten Nachrichten, aufsteigend."""
        ...

    def envelopes(self, uids: Sequence[int]) -> list[Envelope]:
        """Größe, Markierungen und Kopfzeilen, ohne die Mails zu laden."""
        ...

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        """Rohmail; größere Mails als ``max_bytes`` werden nicht geladen."""
        ...

    def abort(self) -> None:
        """Bricht eine laufende Operation aus einem anderen Thread ab."""
        ...

    def close(self) -> None:
        """Trennt die Verbindung."""
        ...


class ImapMailSource:
    """IMAP-Postfach über ``SecureImapClient``."""

    def __init__(
        self,
        account: MailAccount,
        password: Callable[[], str],
        *,
        sleep: Callable[[float], None],
        on_retry: RetryNotice | None = None,
        factory: Factory = default_factory,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.account_id = account.id
        self.folder = account.folder
        self._client = SecureImapClient(
            account,
            password,
            factory=factory,
            timeout=timeout,
            max_attempts=max_attempts,
            sleep=sleep,
            on_retry=on_retry,
        )

    def open(self) -> MailboxInfo:
        """Anmelden und Ordner lesend öffnen."""
        return self._client.open_mailbox(self.folder)

    def uids(self) -> list[int]:
        """UIDs aller nicht gelöschten Nachrichten."""
        return self._client.search_uids()

    def envelopes(self, uids: Sequence[int]) -> list[Envelope]:
        """Kopfdaten in Paketen."""
        return self._client.envelopes(uids, HEADER_BYTES)

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        """Rohmail bis zum Limit."""
        return self._client.fetch(str(uid), max_bytes, size=size)

    def abort(self) -> None:
        """Socket schließen; die laufende Operation endet mit einem Fehler."""
        self._client.abort()

    def close(self) -> None:
        """Abmelden."""
        self._client.close()


def _stable_number(text: str) -> int:
    """Stabile positive Zahl < 2^28 aus einem Text (für UIDs und UIDVALIDITY)."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:7], 16) + 1


class FolderMailSource:
    """Testpostfach: liest .eml-Dateien eines Ordners, ändert und verschiebt nie etwas.

    Die UID einer Datei ergibt sich aus ihrem Namen, damit wiederholte Abrufe dieselbe Datei
    als bekannt erkennen, auch wenn weitere Dateien hinzukommen.
    """

    def __init__(self, account_id: str, directory: Path) -> None:
        self.account_id = account_id
        self.folder = TEST_FOLDER
        self.directory = directory
        self._files: dict[int, Path] = {}

    def open(self) -> MailboxInfo:
        """Prüft den Ordner und listet die Dateien."""
        if not self.directory.is_dir():
            raise ImapError(
                ImapErrorKind.MAILBOX,
                UserMessage(
                    what="Das Testpostfach ist nicht erreichbar",
                    why=f"Ordner „{self.directory.name}“ fehlt",
                    unchanged="Es wurde nichts verändert",
                    action="Ordner mit .eml-Dateien angeben",
                ),
            )
        files = sorted(p for p in self.directory.iterdir() if p.suffix.lower() == ".eml")
        self._files = {_stable_number(p.name): p for p in files[:MAX_TEST_FILES]}
        validity = _stable_number(str(self.directory.resolve()))
        return MailboxInfo(self.folder, validity, len(self._files))

    def uids(self) -> list[int]:
        """UIDs der Dateien in Namensreihenfolge (stabil, eindeutig je Dateiname)."""
        order = {path: index for index, path in enumerate(sorted(self._files.values()))}
        return sorted(self._files, key=lambda uid: order[self._files[uid]])

    def envelopes(self, uids: Sequence[int]) -> list[Envelope]:
        """Größe und Kopfzeilen aus der Datei; Testmails haben keine Markierungen."""
        found = []
        for uid in uids:
            path = self._files.get(uid)
            if path is None:
                continue
            with path.open("rb") as handle:
                head = handle.read(HEADER_BYTES)
            found.append(Envelope(uid, path.stat().st_size, frozenset(), head))
        return found

    def fetch(self, uid: int, size: int, max_bytes: int) -> bytes:
        """Liest die Datei, wenn sie das Limit nicht überschreitet."""
        path = self._files[uid]
        if path.stat().st_size > max_bytes:
            raise ImapError(
                ImapErrorKind.OVERSIZED,
                UserMessage(
                    what="Mail überschreitet das Größenlimit",
                    why=f"mehr als {max_bytes} Byte",
                    unchanged="Die Datei bleibt unverändert",
                    action="-",
                ),
            )
        return path.read_bytes()

    def abort(self) -> None:
        """Dateizugriffe sind kurz; nichts zu unterbrechen."""

    def close(self) -> None:
        """Nichts zu schließen."""
