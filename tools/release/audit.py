"""Prüfung des fertigen Programmordners vor dem Installer: keine Entwicklungsdateien im Produkt.

Geprüft werden:
- Module im Programmarchiv (PYZ): keine Tests, Testhilfen, Test- oder Build-Werkzeuge;
- Dateinamen in Code-Objekten: relativ, nie ein Pfad der Build-Maschine (erscheint sonst in
  Fehlerberichten);
- Dateien im Ordner: keine Datenbanken, ``.env``, Protokolle, Einstellungen, Zertifikatsdateien
  mit privatem Schlüssel, Versionsverwaltung, Test-Caches;
- Textdateien: kein Pfad der Build-Maschine, kein privater Schlüssel.

Aufruf über ``tools/release/build.py audit`` (im Schritt „all“ automatisch nach „freeze“).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

FORBIDDEN_MODULES = re.compile(
    r"^(tests?|conftest|pytest|_pytest|fetchkit|mailcorpus|imapserver|support|hypothesis|"
    r"coverage|mypy|ruff|pip|setuptools|pkg_resources|_distutils_hack|distutils|IPython|"
    r"unittest)(\.|$)"
)
FORBIDDEN_DIRS = frozenset(
    {".git", ".github", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", "tests"}
)
FORBIDDEN_FILE = re.compile(
    r"(^\.env(\..*)?$|\.(db|sqlite|sqlite3|db-wal|db-shm|log|coverage|pfx|p12|key)$|^conftest\.py$|"
    r"^settings\.json$|^\.coverage$|^policy\.json$|^id_(rsa|ed25519)$)",
    re.IGNORECASE,
)
TEXT_SUFFIXES = frozenset({".json", ".txt", ".py", ".cfg", ".toml", ".ini", ".rtf", ".md", ".pem"})
PRIVATE_KEY = "PRIVATE KEY-----"
MAX_TEXT_BYTES = 2 * 1024 * 1024


def module_problems(names: Iterable[str]) -> list[str]:
    """Verbotene Module im Programmarchiv."""
    return [f"Modul im Produkt: {name}" for name in sorted(names) if FORBIDDEN_MODULES.match(name)]


def code_path_problems(filenames: Iterable[tuple[str, str]], markers: Sequence[str]) -> list[str]:
    """Absolute Pfade der Build-Maschine in Code-Objekten (Modul, Dateiname)."""
    problems = []
    for module, filename in filenames:
        normalized = filename.replace("\\", "/")
        absolute = normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized)
        if absolute or any(marker and marker in normalized for marker in markers):
            problems.append(f"Absoluter Pfad in {module}: {filename}")
    return problems


def file_problems(bundle: Path, markers: Sequence[str]) -> list[str]:
    """Verbotene Dateien und Ordner, Build-Pfade und private Schlüssel in Textdateien."""
    problems = []
    for path in sorted(bundle.rglob("*")):
        relative = PurePosixPath(path.relative_to(bundle).as_posix())
        if path.is_dir():
            if path.name in FORBIDDEN_DIRS:
                problems.append(f"Ordner im Produkt: {relative}")
            continue
        if FORBIDDEN_FILE.search(path.name):
            problems.append(f"Datei im Produkt: {relative}")
            continue
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= MAX_TEXT_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if PRIVATE_KEY in text:
                problems.append(f"Privater Schlüssel in {relative}")
            found = [m for m in markers if m and m in text.replace("\\", "/")]
            if found:
                problems.append(f"Build-Pfad in {relative}: {found[0]}")
    return problems


def _archive(workpath: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Modulnamen und Dateinamen der Code-Objekte aus dem Programmarchiv."""
    from PyInstaller.archive.readers import ZlibArchiveReader  # noqa: PLC0415

    toc = workpath / "PYZ-00.toc"
    _target, entries = ast.literal_eval(toc.read_text(encoding="utf-8"))
    names = [str(entry[0]) for entry in entries]
    reader = ZlibArchiveReader(str(workpath / "PYZ-00.pyz"))
    filenames = []
    for name in reader.toc:
        try:
            code = reader.extract(name)
        except Exception:  # unlesbare Einträge sind kein Pfad-Leck
            continue
        filename = getattr(code, "co_filename", None)
        if isinstance(filename, str):
            filenames.append((str(name), filename))
    return names, filenames


def build_markers(root: Path) -> list[str]:
    """Pfadteile der Build-Maschine, die nie im Produkt stehen dürfen."""
    markers = [root.resolve().as_posix()]
    home = Path.home().as_posix()
    if home not in ("/", ""):
        markers.append(home)
    return markers


def audit(bundle: Path, workpath: Path, root: Path) -> list[str]:
    """Alle Befunde; leer bedeutet: Produkt frei von Entwicklungsdateien."""
    markers = build_markers(root)
    names, filenames = _archive(workpath)
    return (
        module_problems(names)
        + code_path_problems(filenames, markers)
        + file_problems(bundle, markers)
    )
