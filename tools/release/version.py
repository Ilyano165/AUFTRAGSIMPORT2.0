"""Versionspflege nach SemVer: anzeigen, setzen, erhöhen, prüfen.

    python tools/release/version.py show
    python tools/release/version.py bump patch        # 2.0.0 -> 2.0.1
    python tools/release/version.py set 2.1.0-rc.1
    python tools/release/version.py check             # in der Pipeline

Beim Setzen werden alle Abschnitte „## [Unveröffentlicht] – Thema“ im CHANGELOG unter dem neuen
Versionsabschnitt „## [X.Y.Z] – Datum“ zusammengefasst.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "src" / "icware_auftragsimport" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
sys.path.insert(0, str(ROOT / "src"))

from icware_auftragsimport.versioning import Version, VersionError  # noqa: E402

UNRELEASED = re.compile(r"^## \[Unveröffentlicht\](?: – (.+))?$", re.MULTILINE)


def current(init: Path = INIT) -> Version:
    """Version aus ``__init__.py``."""
    match = re.search(r'__version__ = "([^"]+)"', init.read_text(encoding="utf-8"))
    if match is None:
        raise VersionError("__version__ nicht gefunden")
    return Version.parse(match.group(1))


def write(
    version: Version, *, init: Path = INIT, changelog: Path = CHANGELOG, today: date | None = None
) -> None:
    """Setzt die Version und legt den Changelog-Abschnitt an."""
    text = init.read_text(encoding="utf-8")
    init.write_text(
        re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text), encoding="utf-8"
    )
    log = changelog.read_text(encoding="utf-8")
    first = UNRELEASED.search(log)
    if first is None or f"## [{version}]" in log:
        return
    heading = f"## [{version}] – {(today or date.today()).isoformat()}"
    body = UNRELEASED.sub(lambda m: f"### {m.group(1)}" if m.group(1) else "", log)
    position = first.start()
    changelog.write_text(body[:position] + heading + "\n\n" + body[position:], encoding="utf-8")


def check() -> int:
    """Gültige SemVer-Version; bei Tag-Builds muss der Tag zur Version passen."""
    version = current()
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag.startswith("v") and tag != f"v{version}":
        print(f"Tag {tag} passt nicht zur Version {version}", file=sys.stderr)
        return 1
    print(f"{version} (MSI {version.msi_version()})")
    return 0


def main(argv: list[str]) -> int:
    """Kommandozeile."""
    if not argv or argv[0] == "show":
        print(current())
        return 0
    if argv[0] == "check":
        return check()
    if argv[0] == "bump" and len(argv) == 2:
        new = current().bump(argv[1])
    elif argv[0] == "set" and len(argv) == 2:
        new = Version.parse(argv[1])
    else:
        print(__doc__)
        return 2
    if new <= current() and new != current():
        print(f"{new} ist nicht größer als {current()}", file=sys.stderr)
        return 1
    write(new)
    print(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
