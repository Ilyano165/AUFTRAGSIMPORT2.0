"""Erwartete Fehler mit vollständiger Erklärung für den Benutzer."""

from __future__ import annotations

from dataclasses import dataclass

_SENTENCE_ENDINGS = (".", "!", "?", ":")


@dataclass(frozen=True, slots=True)
class UserMessage:
    """Die vier Teile jeder Fehlermeldung.

    ``what`` beschreibt das Ereignis, ``why`` die Ursache, ``unchanged`` den sicheren
    Zustand und ``action`` den nächsten Schritt für den Benutzer.
    """

    what: str
    why: str
    unchanged: str
    action: str

    def render(self) -> str:
        """Gibt die Meldung als zusammenhängenden Text zurück."""
        parts = (self.what, self.why, self.unchanged, self.action)
        return " ".join(_as_sentence(part) for part in parts if part.strip())


def _as_sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith(_SENTENCE_ENDINGS) else f"{text}."


class AppError(Exception):
    """Basisklasse aller erwarteten Fehler.

    ``code`` ist stabil und maschinenlesbar, ``message`` ist für Menschen bestimmt,
    ``details`` enthält optionale Einzelpunkte, etwa alle Konfigurationsprobleme.
    """

    def __init__(self, code: str, message: UserMessage, *, details: tuple[str, ...] = ()) -> None:
        super().__init__(message.render())
        self.code = code
        self.message = message
        self.details = details


class ConfigError(AppError):
    """Einstellungen sind ungültig oder nicht lesbar."""


class StoreError(AppError):
    """Die lokale Datenbank ist nicht nutzbar."""


class CredentialError(AppError):
    """Anmeldedaten können nicht gelesen oder gespeichert werden."""


class InstanceLockedError(AppError):
    """Eine andere Instanz arbeitet bereits mit denselben Daten."""


class TransitionError(AppError):
    """Ein Statuswechsel ist im Ablauf nicht erlaubt."""


class CatalogError(AppError):
    """Ein Artikelkatalog ist nicht importierbar."""


class ConcurrencyError(AppError):
    """Ein Datensatz wurde zwischenzeitlich anderweitig geändert."""
