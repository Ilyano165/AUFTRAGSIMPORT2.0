"""Sperrt Abhängigkeiten mit SHA-256-Prüfsummen für den Windows-Build (Python 3.14, x64).

Löst über die PyPI-JSON-Schnittstelle auf, wertet Umgebungsmarker für Windows aus und nimmt nur
Wheels auf. Aufruf: ``python tools/release/lock.py requirements/build-windows.in``.
Installation: ``pip install --require-hashes --no-deps --only-binary=:all: -r <lock>``.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import deque
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

PYTHON = (3, 14)
PYTHON_FULL = "3.14.4"
PLATFORMS = ["win_amd64"]
ENVIRONMENT = {
    **default_environment(),
    "python_version": "3.14",
    "python_full_version": PYTHON_FULL,
    "sys_platform": "win32",
    "platform_system": "Windows",
    "os_name": "nt",
    "platform_machine": "AMD64",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "extra": "",
}
TAGS = {
    str(t)
    for t in [
        *cpython_tags(PYTHON, abis=["cp314", "abi3", "none"], platforms=PLATFORMS),
        *compatible_tags(PYTHON, "cp314", PLATFORMS),
    ]
}


def _json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)  # type: ignore[no-any-return]


def _wheels(files: list[dict[str, object]]) -> list[tuple[str, str]]:
    found = []
    for item in files:
        name = str(item["filename"])
        if not name.endswith(".whl") or item.get("yanked"):
            continue
        _, _, _, tags = parse_wheel_filename(name)
        if any(str(tag) in TAGS for tag in tags):
            found.append((name, str(item["digests"]["sha256"])))  # type: ignore[index]
    return sorted(found)


def _choose(name: str, specifier: SpecifierSet) -> tuple[str, list[tuple[str, str]], list[str]]:
    index = _json(f"https://pypi.org/pypi/{name}/json")
    releases: dict[str, list[dict[str, object]]] = index["releases"]  # type: ignore[assignment]
    candidates = sorted(
        (Version(v) for v in releases if not Version(v).is_prerelease and v in specifier),
        reverse=True,
    )
    for version in candidates:
        wheels = _wheels(releases[str(version)])
        if wheels:
            meta = _json(f"https://pypi.org/pypi/{name}/{version}/json")["info"]
            requires = list(meta.get("requires_dist") or [])  # type: ignore[union-attr]
            return str(version), wheels, requires
    raise SystemExit(f"Kein passendes Wheel für {name}{specifier} (Windows, Python 3.14)")


def dependencies(requires: list[str], extras: frozenset[str]) -> list[Requirement]:
    """Abhängigkeiten, die unter Windows/3.14 für die angeforderten Extras gelten.

    Marker werden je Extra ausgewertet (``extra == "filecache"``); ohne Extra zählt nur der
    Basisumfang. Das Ergebnis trägt keinen Marker mehr, er ist bereits entschieden.
    """
    found = []
    for raw in requires:
        requirement = Requirement(raw)
        marker = requirement.marker
        if marker is not None and not any(
            marker.evaluate({**ENVIRONMENT, "extra": extra}) for extra in {"", *extras}
        ):
            continue
        requirement.marker = None
        found.append(requirement)
    return found


def resolve(path: Path) -> list[tuple[str, str, list[tuple[str, str]], list[str]]]:
    """Breitensuche über die Abhängigkeiten; ``==`` in der Eingabedatei hat Vorrang.

    Extras werden aufgelöst, auch wenn ein Paket sie erst später verlangt
    (``pip-audit`` → ``CacheControl[filecache]`` → ``filelock``).
    """
    queue: deque[tuple[Requirement, str]] = deque()
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            queue.append((Requirement(text), path.name))
    chosen: dict[str, tuple[str, str, list[tuple[str, str]], list[str]]] = {}
    requires_of: dict[str, list[str]] = {}
    resolved_extras: dict[str, set[str]] = {}
    while queue:
        requirement, origin = queue.popleft()
        if requirement.marker is not None and not requirement.marker.evaluate(ENVIRONMENT):
            continue
        key = canonicalize_name(requirement.name)
        wanted = {canonicalize_name(extra) for extra in requirement.extras}
        if key in chosen:
            name, version = chosen[key][0], chosen[key][1]
            if version not in requirement.specifier:
                raise SystemExit(
                    f"Konflikt: {key}=={version} erfüllt {requirement} (aus {origin}) nicht"
                )
            missing = wanted - resolved_extras[key]
            if missing:
                resolved_extras[key] |= missing
                base = {str(r) for r in dependencies(requires_of[key], frozenset())}
                via = f"{name}[{','.join(sorted(missing))}]"
                for added in dependencies(requires_of[key], frozenset(missing)):
                    if str(added) not in base:
                        queue.append((added, via))
            continue
        version, wheels, requires = _choose(requirement.name, requirement.specifier)
        chosen[key] = (requirement.name, version, wheels, [origin])
        requires_of[key] = requires
        resolved_extras[key] = set(wanted)
        for dependency in dependencies(requires, frozenset(wanted)):
            queue.append((dependency, f"{requirement.name}=={version}"))
    return sorted(chosen.values(), key=lambda item: canonicalize_name(item[0]))


def main() -> None:
    """Schreibt ``<eingabe>.lock`` neben die Eingabedatei."""
    source = Path(sys.argv[1])
    target = source.with_suffix(".lock")
    lines = [
        f"# Erzeugt von tools/release/lock.py aus {source.name}. Nicht von Hand ändern.",
        f"# Ziel: CPython {PYTHON_FULL}, Windows x64, nur Wheels.",
        "# Installation: pip install --require-hashes --no-deps --only-binary=:all: -r <Datei>",
        "",
    ]
    for name, version, wheels, origins in resolve(source):
        lines.append(f"{canonicalize_name(name)}=={version} \\")
        lines += [f"    --hash=sha256:{digest} \\" for _, digest in wheels[:-1]]
        lines.append(f"    --hash=sha256:{wheels[-1][1]}")
        lines.append(f"    # über {origins[0]}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    count = sum(1 for line in lines if "==" in line and not line.lstrip().startswith("#"))
    print(f"{target}: {count} Pakete")


if __name__ == "__main__":
    main()
