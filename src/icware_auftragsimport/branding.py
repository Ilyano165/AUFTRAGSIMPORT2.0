"""Produkt- und Herausgeberangaben.

Einzige Quelle für Namen in Oberfläche, Protokoll, Diagnosebericht und Build.
"""

from __future__ import annotations

from . import __version__

PUBLISHER = "IC-Ware"
PRODUCT_NAME = "IC-Ware Auftrags-Import"
PRODUCT_SHORT_NAME = "Auftrags-Import"
VERSION = __version__
DATA_DIR_VENDOR = "IC-Ware"
DATA_DIR_PRODUCT = "Auftrags-Import"
CREDENTIAL_NAMESPACE = "IC-Ware/Auftrags-Import"
LOGGER_NAME = "icware"
# Vor dem ersten Release bestätigen (RELEASE_CHECKLIST.md, Abschnitt Support).
SUPPORT_CONTACT = "support@ic-ware.eu"
EXECUTABLE_NAME = "AuftragsImport"


def about_text() -> str:
    """Text für den Info-Dialog und den Kopf des Diagnoseberichts."""
    return f"{PRODUCT_NAME} {VERSION}\nHerausgeber: {PUBLISHER}"
