"""Sicheres Einlesen von XML: kein DOCTYPE, keine Entitäten, kein Nachladen, Größenlimit.

Zweistufig: Vorprüfung der Bytes (Größe, nur UTF-8/ISO-8859-1-kompatible Kodierung, damit
die DOCTYPE-Erkennung nicht per UTF-16 umgangen werden kann) und danach ``defusedxml`` mit
verbotener DTD. Schützt gegen XXE, externe Entitäten, Entity-Expansion und XML-Bomben.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .limits import FILES

_FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY", b"<!ELEMENT", b"<!ATTLIST", b"<!NOTATION")
_WIDE_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")


_FIRST_ELEMENT = re.compile(rb"<[A-Za-z_:]")


def xml_prolog(data: bytes) -> bytes:
    """Bytes vor dem Wurzelelement; nur dort sind DOCTYPE und Entitätsdeklarationen wirksam."""
    match = _FIRST_ELEMENT.search(data)
    return data[: match.start()] if match else data


def has_declarations(data: bytes) -> bool:
    """True, wenn der Prolog DOCTYPE- oder Entitätsdeklarationen enthält."""
    prolog = xml_prolog(data)
    return any(marker in prolog for marker in _FORBIDDEN)


class UnsafeXmlError(ValueError):
    """XML wurde aus Sicherheitsgründen oder wegen Formfehlern abgelehnt."""


def parse_xml(data: bytes, *, max_bytes: int = FILES.xml_max_bytes) -> Element:
    """Parst nicht vertrauenswürdiges XML sicher."""
    if len(data) > max_bytes:
        raise UnsafeXmlError(f"XML größer als {max_bytes} Byte")
    if data.startswith(_WIDE_BOMS) or b"\x00" in data:
        raise UnsafeXmlError("UTF-16/UTF-32 und Nullbytes sind nicht erlaubt")
    if has_declarations(data):
        raise UnsafeXmlError("DOCTYPE/ENTITY-Deklarationen sind nicht erlaubt")
    try:
        return fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise UnsafeXmlError(f"Unsicheres XML: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise UnsafeXmlError(f"XML nicht wohlgeformt: {exc}") from exc
