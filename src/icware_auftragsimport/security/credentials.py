"""Anmeldedaten: nur im Windows-Anmeldespeicher, nie in Konfigurationsdateien."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..branding import CREDENTIAL_NAMESPACE
from ..domain.errors import CredentialError, UserMessage

ENV_PREFIX = "ICW_SECRET_"
_UNUSABLE_BACKEND_MARKERS = ("fail", "null")


class CredentialStore(Protocol):
    """Ablage für Geheimnisse, adressiert über einen Schlüssel je Mailkonto."""

    def get(self, key: str) -> str | None:
        """Gespeichertes Geheimnis oder ``None``."""
        ...

    def set(self, key: str, secret: str) -> None:
        """Speichert oder ersetzt ein Geheimnis; wirft ``CredentialError`` bei Fehler."""
        ...

    def delete(self, key: str) -> None:
        """Entfernt ein Geheimnis; fehlende Einträge sind kein Fehler."""
        ...


def credential_key(account_id: str) -> str:
    """Zielname im Anmeldespeicher, etwa ``IC-Ware/Auftrags-Import/vertrieb``."""
    return f"{CREDENTIAL_NAMESPACE}/{account_id}"


def env_variable(account_id: str) -> str:
    """Name der optionalen Umgebungsvariable für den Dienstbetrieb."""
    return ENV_PREFIX + account_id.upper().replace("-", "_")


class MemoryCredentialStore:
    """Flüchtige Ablage für Tests und Probeläufe."""

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Gespeichertes Geheimnis oder ``None``."""
        return self._items.get(key)

    def set(self, key: str, secret: str) -> None:
        """Speichert ein Geheimnis."""
        self._items[key] = secret

    def delete(self, key: str) -> None:
        """Entfernt ein Geheimnis."""
        self._items.pop(key, None)


def _unavailable(why: str) -> CredentialError:
    return CredentialError(
        "CREDENTIAL_STORE_UNAVAILABLE",
        UserMessage(
            what="Der Windows-Anmeldespeicher ist nicht erreichbar",
            why=why,
            unchanged="Es wurde kein Passwort gespeichert oder verändert",
            action="Bitte die Anwendung mit einem normalen Windows-Benutzerkonto starten "
            "oder den Support mit einem Diagnosebericht kontaktieren",
        ),
    )


class KeyringCredentialStore:
    """Ablage über ``keyring``; unter Windows die Anmeldeinformationsverwaltung."""

    def __init__(self) -> None:
        try:
            import keyring  # noqa: PLC0415
            from keyring.errors import KeyringError  # noqa: PLC0415
        except ImportError as exc:
            raise _unavailable("Das Modul für den Anmeldespeicher ist nicht installiert") from exc
        backend = keyring.get_keyring()
        kind = type(backend)
        qualified = f"{kind.__module__}.{kind.__name__}".casefold()
        chained = getattr(backend, "backends", None)
        if any(marker in qualified for marker in _UNUSABLE_BACKEND_MARKERS) or chained == []:
            found = type(backend).__name__
            raise _unavailable(f"Es wurde kein nutzbarer Speicher gefunden ({found})")
        self._keyring = keyring
        self._error_type: type[Exception] = KeyringError
        self.backend_name = type(backend).__name__

    def get(self, key: str) -> str | None:
        """Gespeichertes Geheimnis oder ``None``."""
        try:
            value = self._keyring.get_password(key, "secret")
        except self._error_type as exc:
            raise _unavailable(f"Lesen fehlgeschlagen: {type(exc).__name__}") from exc
        return str(value) if value is not None else None

    def set(self, key: str, secret: str) -> None:
        """Speichert ein Geheimnis."""
        try:
            self._keyring.set_password(key, "secret", secret)
        except self._error_type as exc:
            raise _unavailable(f"Speichern fehlgeschlagen: {type(exc).__name__}") from exc

    def delete(self, key: str) -> None:
        """Entfernt ein Geheimnis; ein fehlender Eintrag ist kein Fehler."""
        try:
            self._keyring.delete_password(key, "secret")
        except self._error_type:
            return


class SecretSource(StrEnum):
    """Woher ein Geheimnis stammt; wird angezeigt, nie der Wert selbst."""

    STORE = "store"
    ENVIRONMENT = "environment"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ResolvedSecret:
    """Geheimnis samt Herkunft. ``repr`` verbirgt den Wert."""

    value: str | None
    source: SecretSource

    def __repr__(self) -> str:
        shown = "<vorhanden>" if self.value else "<fehlt>"
        return f"ResolvedSecret(value={shown}, source={self.source.value})"


class LazyCredentialStore:
    """Öffnet die Anmeldeinformationsverwaltung erst bei der ersten Benutzung.

    Ein fehlender Speicher verhindert so nicht den Programmstart, sondern führt erst beim
    Abruf oder beim Speichern eines Passworts zu einer verständlichen Meldung.
    """

    def __init__(self, factory: Callable[[], CredentialStore] | None = None) -> None:
        self._factory: Callable[[], CredentialStore] = factory or KeyringCredentialStore
        self._store: CredentialStore | None = None

    def _open(self) -> CredentialStore:
        if self._store is None:
            self._store = self._factory()
        return self._store

    def get(self, key: str) -> str | None:
        """Gespeichertes Geheimnis oder ``None``."""
        return self._open().get(key)

    def set(self, key: str, secret: str) -> None:
        """Speichert das Geheimnis."""
        self._open().set(key, secret)

    def delete(self, key: str) -> None:
        """Entfernt das Geheimnis."""
        self._open().delete(key)


def resolve_secret(
    store: CredentialStore,
    account_id: str,
    *,
    allow_environment: bool,
    environ: Mapping[str, str] | None = None,
) -> ResolvedSecret:
    """Liest das Geheimnis eines Kontos.

    Die Umgebungsvariable wird nur beachtet, wenn der Administrator sie ausdrücklich
    erlaubt hat; die Herkunft wird zurückgegeben, damit die Oberfläche sie anzeigen kann.
    """
    env = os.environ if environ is None else environ
    if allow_environment:
        value = env.get(env_variable(account_id))
        if value:
            return ResolvedSecret(value, SecretSource.ENVIRONMENT)
    stored = store.get(credential_key(account_id))
    if stored:
        return ResolvedSecret(stored, SecretSource.STORE)
    return ResolvedSecret(None, SecretSource.NONE)
