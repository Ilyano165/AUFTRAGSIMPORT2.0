"""Duplikatschutz über mehrere unabhängige Merkmale.

Eine Mail gilt als dieselbe, wenn Serveradresse (Konto, Ordner, UIDVALIDITY, UID)
oder Message-ID übereinstimmen. Gleicher Inhalt unter anderer Message-ID ist ein
*mögliches* Duplikat und wird zur Prüfung vorgelegt, nie still verworfen: Kunden
bestellen manchmal bewusst zweimal dasselbe.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from ..domain.identity import customer_key, normalize_reference
from ..infrastructure.repositories import MailRepository, OrderRef, OrderRepository

__all__ = [
    "MailDuplicate",
    "MailDuplicateCheck",
    "MailIdentity",
    "check_mail",
    "content_fingerprint",
    "customer_key",
    "mail_keys",
    "normalize_reference",
    "reference_conflicts",
]

_SUBJECT_PREFIX = re.compile(r"^(?:\s*(?:re|aw|wg|fw|fwd|antw|wtr)\s*:\s*)+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MailIdentity:
    """Merkmale, an denen eine Mail wiedererkannt wird."""

    account_id: str
    folder: str
    uidvalidity: int | None
    uid: int | None
    message_id: str
    content_hash: str


class MailDuplicate(StrEnum):
    """Ergebnis der Duplikatprüfung einer Mail."""

    NEW = "new"
    SAME_MAIL = "same_mail"
    SAME_MESSAGE_ID = "same_message_id"
    SAME_CONTENT = "same_content"


@dataclass(frozen=True, slots=True)
class MailDuplicateCheck:
    """Art des Duplikats und die bereits bekannte Mail."""

    kind: MailDuplicate
    existing_mail_id: str | None = None

    @property
    def skip(self) -> bool:
        """True, wenn die Mail nicht erneut verarbeitet werden darf."""
        return self.kind in (MailDuplicate.SAME_MAIL, MailDuplicate.SAME_MESSAGE_ID)


def content_fingerprint(sender: str, subject: str, body: str) -> str:
    """SHA-256 über Absender, Betreff ohne AW/WG und Text ohne Leerraumunterschiede."""
    parts = (
        sender.strip().casefold(),
        _SUBJECT_PREFIX.sub("", subject).strip().casefold(),
        " ".join(body.split()).casefold(),
    )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def check_mail(mails: MailRepository, identity: MailIdentity) -> MailDuplicateCheck:
    """Prüft eine Mail gegen alle bekannten Mails, stärkstes Merkmal zuerst."""
    if identity.uid is not None and identity.uidvalidity is not None:
        existing = mails.find_by_uid(
            identity.account_id, identity.folder, identity.uidvalidity, identity.uid
        )
        if existing:
            return MailDuplicateCheck(MailDuplicate.SAME_MAIL, existing)
    by_message_id = mails.find_by_message_id(identity.message_id)
    if by_message_id:
        return MailDuplicateCheck(MailDuplicate.SAME_MESSAGE_ID, by_message_id[0])
    by_content = mails.find_by_content_hash(identity.content_hash)
    if by_content:
        return MailDuplicateCheck(MailDuplicate.SAME_CONTENT, by_content[0])
    return MailDuplicateCheck(MailDuplicate.NEW)


def mail_keys(identity: MailIdentity) -> tuple[str, ...]:
    """Alle Idempotenzschlüssel einer Mail."""
    keys = []
    if identity.message_id:
        keys.append(f"msgid:{identity.message_id}")
    if identity.uid is not None and identity.uidvalidity is not None:
        keys.append(
            f"uid:{identity.account_id}/{identity.folder}/{identity.uidvalidity}/{identity.uid}"
        )
    keys.append(f"content:{identity.content_hash}")
    return tuple(keys)


def reference_conflicts(
    orders: OrderRepository, order_id: str, key: str, reference: str
) -> list[OrderRef]:
    """Andere Aufträge desselben Kunden mit derselben Bestellnummer."""
    normalized = normalize_reference(reference)
    if not key or not normalized:
        return []
    return orders.find_by_reference(key, normalized, exclude_id=order_id)
