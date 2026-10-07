"""Dateisystemschutz: sichere Dateinamen, Pfadverknüpfung und Ordnerprüfung.

Dateinamen aus Mails werden nie als Pfad verwendet. Ordner, in die geschrieben wird,
dürfen keine Verknüpfungen (Symlink/Junction) enthalten, nicht welt-beschreibbar sein und
nicht in Systemordnern liegen. Netzlaufwerke sind nur nach ausdrücklicher Freigabe erlaubt.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .sanitize import clean_untrusted

RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
    | {"COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
)
_INVALID = frozenset('<>:"/\\|?*')
_POSIX_SYSTEM = ("/", "/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr")
_DRIVE_REMOTE = 4
MAX_COMPONENT = 255


class UnsafePathError(ValueError):
    """Ein Pfad oder Dateiname ist für den Zweck nicht sicher."""


def _reserved(name: str) -> bool:
    return name.split(".", maxsplit=1)[0].strip().upper() in RESERVED_NAMES


def safe_filename(name: str | None, *, fallback: str = "anhang", max_chars: int = 120) -> str:
    """Anzeige-/Ablagename ohne Pfadanteile, Steuerzeichen oder reservierte Namen."""
    text = unicodedata.normalize("NFKC", name or "")
    text = re.split(r"[/\\]", text)[-1]
    text = clean_untrusted(text, max_chars=max_chars * 2)
    text = "".join("_" if ch in _INVALID else ch for ch in text).rstrip(" .").lstrip(" ")
    stem, dot, extension = text.rpartition(".")
    if not dot:
        stem, extension = text, ""
    extension = re.sub(r"[^A-Za-z0-9]", "", extension)[:10].lower()
    stem = stem.strip(" .") or fallback
    if _reserved(stem):
        stem = f"_{stem}"
    stem = stem[: max(1, max_chars - len(extension) - 1)]
    return f"{stem}.{extension}" if extension else stem


def file_extension(name: str | None) -> str:
    """Kleingeschriebene letzte Endung nach Bereinigung, etwa ``pdf``."""
    cleaned = safe_filename(name)
    return cleaned.rpartition(".")[2] if "." in cleaned else ""


def safe_join(base: Path, name: str) -> Path:
    """``base / name`` nur für einen einzelnen, harmlosen Namensbestandteil."""
    problems = (
        not name,
        name in (".", ".."),
        any(ch in name for ch in "/\\:\x00"),
        name != name.strip(" ."),
        len(name) > MAX_COMPONENT,
        _reserved(name),
        any(ord(ch) < 32 for ch in name),
    )
    if any(problems):
        raise UnsafePathError(f"Unzulässiger Dateiname „{clean_untrusted(name, max_chars=80)}“")
    candidate = base / name
    if os.path.normcase(os.path.abspath(candidate.parent)) != os.path.normcase(
        os.path.abspath(base)
    ):
        raise UnsafePathError("Dateiname verlässt den Zielordner")
    return candidate


@dataclass(frozen=True, slots=True)
class DirectoryIdentity:
    """Kennung eines Ordners; ändert sich bei Austausch, Umleitung oder anderem Laufwerk."""

    path: str
    device: int
    inode: int


def is_network_path(path_text: str) -> bool:
    """UNC-Pfad oder (unter Windows) verbundenes Netzlaufwerk."""
    if path_text.startswith(("\\\\", "//")):
        return True
    if os.name != "nt" or len(path_text) < 2 or path_text[1] != ":":
        return False
    try:
        import ctypes  # noqa: PLC0415

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        return int(kernel32.GetDriveTypeW(f"{path_text[0]}:\\")) == _DRIVE_REMOTE
    except (AttributeError, OSError):
        return False


def _system_roots() -> list[Path]:
    if os.name == "nt":
        names = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")
        roots = [Path(os.environ[n]) for n in names if os.environ.get(n)]
    else:
        roots = []
    roots.append(Path(sys.prefix))
    return roots


def _forbidden(real: Path, extra: Sequence[Path]) -> bool:
    if real == Path(real.anchor):
        return True
    if os.name != "nt" and str(real) in _POSIX_SYSTEM:
        return True
    for root in (*_system_roots(), *extra):
        resolved = Path(os.path.realpath(root))
        if real == resolved or resolved in real.parents:
            return True
    return False


def inspect_directory(
    path_text: str,
    *,
    purpose: str,
    allow_network: bool = False,
    forbidden_roots: Sequence[Path] = (),
) -> DirectoryIdentity:
    """Prüft einen Schreibordner und liefert seine Kennung; wirft ``UnsafePathError``."""
    if not path_text:
        raise UnsafePathError(f"{purpose}: kein Ordner eingerichtet")
    path = Path(path_text)
    if not path.is_absolute():
        raise UnsafePathError(f"{purpose}: Pfad muss absolut sein")
    if is_network_path(path_text) and not allow_network:
        raise UnsafePathError(f"{purpose}: Netzlaufwerk ist nicht freigegeben")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise UnsafePathError(f"{purpose}: Ordner existiert nicht") from exc
    junction = getattr(os.path, "isjunction", lambda _p: False)(path)
    if stat.S_ISLNK(info.st_mode) or junction:
        raise UnsafePathError(f"{purpose}: Ordner ist eine Verknüpfung")
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafePathError(f"{purpose}: Pfad ist kein Ordner")
    real = Path(os.path.realpath(path))
    if os.path.normcase(str(real)) != os.path.normcase(os.path.abspath(path)):
        raise UnsafePathError(f"{purpose}: Pfad enthält eine Verknüpfung")
    if os.name != "nt" and info.st_mode & stat.S_IWOTH and not info.st_mode & stat.S_ISVTX:
        raise UnsafePathError(f"{purpose}: Ordner ist für alle Benutzer beschreibbar")
    if _forbidden(real, forbidden_roots):
        raise UnsafePathError(f"{purpose}: Systemordner oder Laufwerkswurzel ist nicht erlaubt")
    return DirectoryIdentity(str(real), info.st_dev, info.st_ino)
