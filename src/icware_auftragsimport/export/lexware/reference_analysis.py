"""Analyse echter Lexware-Referenzdateien.

Liefert eine maschinenlesbare Beschreibung (Deklaration, Zeilenenden, Namespaces,
Elementpfade, Attributwerte, Wertformate, CDATA, Reihenfolgevarianten, Auffälligkeiten)
und prüft jede Datei gegen die Exportspezifikation. Mit weiteren Referenzdateien wird die
Analyse einfach erneut ausgeführt; Unterschiede zwischen Dateien werden sichtbar.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any
from xml.etree import ElementTree

from ...security.safe_xml import parse_xml
from ..validator import ExportValidator
from .spec import ExportSpec, local_name, namespace

_FORMATS = (
    ("leer", re.compile(r"^$")),
    ("Ganzzahl", re.compile(r"^\d+$")),
    ("Dezimal (Punkt)", re.compile(r"^\d+\.\d+$")),
    ("Datum Zeit (Leerzeichen)", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")),
    ("Datum Zeit (ISO)", re.compile(r"^\d{4}-\d{2}-\d{2}T")),
)
_PLACEHOLDER = re.compile(r"^(X+|x+|TEST|DUMMY|0+)$")


def _format(text: str) -> str:
    return next((name for name, pattern in _FORMATS if pattern.match(text)), "Text")


def _paths(root: ElementTree.Element) -> list[tuple[str, ElementTree.Element]]:
    result = []

    def walk(element: ElementTree.Element, prefix: str) -> None:
        path = f"{prefix}/{local_name(element.tag)}" if prefix else local_name(element.tag)
        result.append((path, element))
        for child in element:
            walk(child, path)

    walk(root, "")
    return result


def analyze_reference(data: bytes, spec: ExportSpec) -> dict[str, Any]:
    """Beschreibt eine Referenzdatei und ihre Abweichungen von der Spezifikation."""
    declaration = re.match(rb'<\?xml version="([^"]+)" encoding="([^"]+)"\?>', data)
    encoding = declaration.group(2).decode() if declaration else "UTF-8"
    root = parse_xml(data)
    paths = _paths(root)
    counts = Counter(path for path, _ in paths)
    formats: dict[str, Counter[str]] = defaultdict(Counter)
    attributes: dict[str, set[str]] = defaultdict(set)
    findings: list[str] = []
    for path, element in paths:
        for name, value in element.attrib.items():
            attributes[path].add(f"{name}={value}")
        if len(element):
            continue
        text = element.text or ""
        formats[path][_format(text)] += 1
        if _PLACEHOLDER.match(text):
            findings.append(f"{path}: Platzhalterwert „{text}“")
        if text.endswith(("(", ",", "-")):
            findings.append(f"{path}: Wert endet abrupt („{text}“), mögliche Kürzung")
    orders = {
        "/".join(local_name(c.tag) for c in element)
        for path, element in paths
        if path.endswith("ARTICLE_PRICE")
    }
    parties = [
        ElementTree.tostring(element[0])
        for path, element in paths
        if path.endswith("_PARTY") and path.count("/") == 5
    ]
    conformance = ExportValidator(spec).check_xml(data, encoding)
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "xml_version": declaration.group(1).decode() if declaration else None,
        "encoding": encoding,
        "line_ending": "CRLF" if b"\r\n" in data else "LF",
        "cdata_values": data.count(b"<![CDATA["),
        "root": local_name(root.tag),
        "namespaces": sorted({namespace(e.tag) or "(keiner)" for _, e in paths}),
        "elements": [{"path": p, "count": n} for p, n in counts.items()],
        "attributes": {p: sorted(v) for p, v in attributes.items()},
        "value_formats": {p: dict(c) for p, c in formats.items() if len(c) > 1 or "Text" not in c},
        "article_price_child_orders": sorted(orders),
        "parties_identical": len(parties) > 1 and len(set(parties)) == 1,
        "notes": findings,
        "conformance": [f.message for f in conformance],
    }
