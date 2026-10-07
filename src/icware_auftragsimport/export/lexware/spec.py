"""Maschinenlesbare Exportspezifikation und Strukturprüfung dagegen."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any
from xml.etree.ElementTree import Element

SPEC_RESOURCE = "lexware_opentrans_order_v1.json"


@dataclass(frozen=True, slots=True)
class TextRule:
    """Regel für den Textinhalt eines Blattelements."""

    type: str
    required: bool = False
    pattern: re.Pattern[str] | None = None
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AttributeRule:
    """Pflichtattribut mit festem Wert oder Werteliste."""

    name: str
    const: str | None = None
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ElementSpec:
    """Ein Element der Spezifikation."""

    tag: str
    min: int
    max: int | None
    children: tuple[ElementSpec, ...]
    text: TextRule | None
    attributes: tuple[AttributeRule, ...]
    choice: bool
    basis: tuple[str, ...]
    effect: str

    def matches(self, element: Element) -> bool:
        """Gleicher lokaler Name und gleiche feste Attributwerte."""
        if local_name(element.tag) != self.tag:
            return False
        return all(
            rule.const is None or element.get(rule.name) == rule.const for rule in self.attributes
        )


@dataclass(frozen=True, slots=True)
class ExportSpec:
    """Geladene Spezifikation mit Versions- und Inhaltskennung."""

    spec_id: str
    version: str
    status: str
    sha256: str
    document: dict[str, Any]
    tree: ElementSpec
    open_questions: tuple[dict[str, str], ...]
    raw: dict[str, Any]


def local_name(tag: str) -> str:
    """Tag ohne Namespace."""
    return tag.rpartition("}")[2]


def namespace(tag: str) -> str:
    """Namespace eines Tags oder leerer Text."""
    return tag[1:].partition("}")[0] if tag.startswith("{") else ""


def _element(data: dict[str, Any]) -> ElementSpec:
    text = None
    if "text" in data:
        rule = data["text"]
        pattern = re.compile(rule["pattern"]) if "pattern" in rule else None
        text = TextRule(
            rule["type"], bool(rule.get("required")), pattern, tuple(rule.get("values", ()))
        )
    attributes = tuple(
        AttributeRule(name, rule.get("const"), tuple(rule.get("values", ())))
        for name, rule in data.get("attributes", {}).items()
    )
    return ElementSpec(
        tag=data["tag"],
        min=int(data["min"]),
        max=None if data["max"] is None else int(data["max"]),
        children=tuple(_element(child) for child in data.get("children", ())),
        text=text,
        attributes=attributes,
        choice=bool(data.get("choice")),
        basis=tuple(data.get("basis", ())),
        effect=str(data.get("lexware_effect", "")),
    )


def load_spec() -> ExportSpec:
    """Lädt die mitgelieferte Spezifikation."""
    raw_bytes = resources.files(__package__).joinpath(SPEC_RESOURCE).read_bytes()
    raw = json.loads(raw_bytes)
    return ExportSpec(
        spec_id=raw["spec_id"],
        version=raw["spec_version"],
        status=raw["status"],
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        document=raw["document"],
        tree=_element(raw["tree"]),
        open_questions=tuple(raw["open_questions"]),
        raw=raw,
    )


@dataclass(frozen=True, slots=True)
class StructureIssue:
    """Abweichung einer XML-Datei von der Spezifikation."""

    path: str
    message: str


def _text_problem(rule: TextRule, text: str) -> str | None:
    if not text:
        return "Pflichtwert ist leer" if rule.required else None
    if rule.values and text not in rule.values:
        return f"Wert „{text}“ nicht erlaubt ({', '.join(rule.values)})"
    if rule.pattern is not None and not rule.pattern.fullmatch(text):
        return f"Wert „{text}“ hat nicht das Format {rule.type}"
    return None


def _check_text(spec: ElementSpec, element: Element, path: str) -> list[StructureIssue]:
    text = element.text or ""
    if spec.children:
        return [StructureIssue(path, "Container-Element enthält Text")] if text.strip() else []
    problem = _text_problem(spec.text, text) if spec.text else None
    return [StructureIssue(path, problem)] if problem else []


def _check_attributes(spec: ElementSpec, element: Element, path: str) -> list[StructureIssue]:
    issues = []
    known = {rule.name for rule in spec.attributes}
    for rule in spec.attributes:
        value = element.get(rule.name)
        if value is None:
            issues.append(StructureIssue(path, f"Attribut {rule.name} fehlt"))
        elif rule.const is not None and value != rule.const:
            issues.append(
                StructureIssue(path, f"Attribut {rule.name}=„{value}“, erwartet „{rule.const}“")
            )
        elif rule.values and value not in rule.values:
            issues.append(StructureIssue(path, f"Attribut {rule.name}=„{value}“ nicht erlaubt"))
    for name in element.attrib:
        if local_name(name) not in known:
            issues.append(StructureIssue(path, f"Unbekanntes Attribut {name}"))
    return issues


def _assign(specs: tuple[ElementSpec, ...], element: Element) -> list[tuple[Element, int | None]]:
    assigned = []
    for child in element:
        index = next((i for i, spec in enumerate(specs) if spec.matches(child)), None)
        assigned.append((child, index))
    return assigned


def _check_children(spec: ElementSpec, element: Element, path: str) -> list[StructureIssue]:
    issues: list[StructureIssue] = []
    assigned = _assign(spec.children, element)
    if spec.choice and (len(assigned) != 1 or assigned[0][1] is None):
        options = " oder ".join(child.tag for child in spec.children)
        return [StructureIssue(path, f"Genau ein Element erwartet: {options}")]
    counts = [0] * len(spec.children)
    last_index = -1
    for child, index in assigned:
        child_path = f"{path}/{local_name(child.tag)}"
        if index is not None and spec.children[index].max != 1:
            child_path += f"[{counts[index] + 1}]"
        if index is None:
            issues.append(
                StructureIssue(child_path, "Element ist in der Spezifikation nicht vorgesehen")
            )
            continue
        counts[index] += 1
        if index < last_index:
            expected = spec.children[last_index].tag
            issues.append(StructureIssue(child_path, f"Falsche Reihenfolge: steht nach {expected}"))
        last_index = max(last_index, index)
        issues += check_structure(spec.children[index], child, child_path)
    for child_spec, count in zip(spec.children, counts, strict=True):
        if spec.choice:
            continue
        if count < child_spec.min:
            issues.append(StructureIssue(f"{path}/{child_spec.tag}", "Pflichtelement fehlt"))
        if child_spec.max is not None and count > child_spec.max:
            issues.append(
                StructureIssue(
                    f"{path}/{child_spec.tag}", f"Mehr als {child_spec.max}-mal vorhanden"
                )
            )
    return issues


def check_structure(spec: ElementSpec, element: Element, path: str = "") -> list[StructureIssue]:
    """Vergleicht ein Element rekursiv mit der Spezifikation."""
    path = path or local_name(element.tag)
    if not spec.matches(element):
        return [StructureIssue(path, f"Element {spec.tag} erwartet")]
    return [
        *_check_attributes(spec, element, path),
        *_check_text(spec, element, path),
        *_check_children(spec, element, path),
    ]
