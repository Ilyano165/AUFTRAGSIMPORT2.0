"""Prüft die Laufzeitumgebung auf bekannte Sicherheitslücken.

Die ausgelieferte Anwendung bringt ihren Python-Interpreter mit; dieser muss mindestens
die hier genannten Stände haben. Belege:
- CVE-2024-6923 (Header-Injection beim Serialisieren), behoben in 3.12.5
- CVE-2023-27043 (fehlerhafte Adressauswertung), behoben in 3.12.6 (``strict``-Parameter)
- CVE-2024-8176 (Stapelüberlauf in expat), behoben in expat 2.7.0, Regression in 2.7.1
"""

from __future__ import annotations

import inspect
import pyexpat
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass
from email import utils as email_utils

MIN_PYTHON = (3, 12, 6)
MIN_EXPAT = (2, 7, 1)
MIN_OPENSSL = (3, 0, 0)


@dataclass(frozen=True, slots=True)
class RuntimeReport:
    """Versionen und gefundene Probleme der Laufzeit."""

    python: str
    expat: str
    openssl: str
    problems: tuple[str, ...]

    @property
    def secure(self) -> bool:
        """True, wenn keine bekannte Lücke gefunden wurde."""
        return not self.problems


def _version(parts: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in parts)


def check_runtime(
    python: tuple[int, int, int] | None = None,
    expat: tuple[int, int, int] | None = None,
    openssl: tuple[int, ...] | None = None,
    getaddresses: Callable[..., object] = email_utils.getaddresses,
) -> RuntimeReport:
    """Bewertet Interpreter, expat und OpenSSL."""
    python = python or (sys.version_info[0], sys.version_info[1], sys.version_info[2])
    expat = expat or pyexpat.version_info
    info = ssl.OPENSSL_VERSION_INFO
    openssl = openssl or (info[0], info[1], info[3] if info[0] >= 3 else info[2])
    problems = []
    if python < MIN_PYTHON:
        problems.append(
            f"Python {_version(python)} enthält bekannte Lücken im Mailmodul "
            f"(CVE-2023-27043, CVE-2024-6923); erforderlich ist mindestens {_version(MIN_PYTHON)}"
        )
    if "strict" not in inspect.signature(getaddresses).parameters:
        problems.append("email.utils.getaddresses unterstützt keine strikte Adressprüfung")
    if expat < MIN_EXPAT:
        problems.append(
            f"expat {_version(expat)} enthält bekannte Lücken (CVE-2024-8176); "
            f"erforderlich ist mindestens {_version(MIN_EXPAT)}"
        )
    if openssl < MIN_OPENSSL:
        problems.append(f"OpenSSL {_version(openssl)} wird nicht mehr unterstützt")
    return RuntimeReport(_version(python), _version(expat), _version(openssl), tuple(problems))
