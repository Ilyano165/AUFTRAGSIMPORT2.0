"""ExportValidator: prüft vor dem Schreiben Inhalt, XML, Encoding und Dateinamen.

Der Validator prüft die erzeugte Datei unabhängig vom Serializer gegen die
Spezifikation und gleicht die Werte zurück mit der Exportrepräsentation ab.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..domain.findings import Finding, Severity, ValidationResult
from ..security.safe_xml import UnsafeXmlError, has_declarations, parse_xml
from .lexware.spec import ExportSpec, check_structure, local_name, namespace
from .model import ExportDocument, PaymentKind, PreparedExport

MAX_FILE_NAME_LENGTH = 100
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.xml")
RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
XML_ILLEGAL = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
DECLARATION = re.compile(rb'^<\?xml version="1\.0" encoding="(?P<encoding>[A-Za-z0-9_-]+)"\?>')
ALLOWED_CODES = {PaymentKind.CASH: ("10", "25", "52", "56"), PaymentKind.ACCOUNT: ("54",)}
USUAL_TAX_RATES = (Decimal(0), Decimal("0.07"), Decimal("0.19"))
MONEY_PLACES = Decimal("0.01")


def _error(code: str, message: str, field: str = "") -> Finding:
    return Finding(code, Severity.ERROR, message, field)


def check_file_name(name: str) -> list[Finding]:
    """Windows-taugliche, eindeutige Dateinamen ohne Pfadanteile."""
    stem = name.rsplit(".", 1)[0].upper()
    if len(name) > MAX_FILE_NAME_LENGTH or not FILE_NAME.fullmatch(name) or stem in RESERVED_NAMES:
        return [
            _error("EXP_FILE_NAME_INVALID", f"Dateiname „{name}“ ist nicht zulässig", "file_name")
        ]
    return []


class ExportValidator:
    """Gesamtprüfung einer vorbereiteten Exportdatei."""

    def __init__(self, spec: ExportSpec) -> None:
        self._spec = spec

    def validate(
        self, prepared: PreparedExport, *, xml: bytes | None, file_name: str | None, encoding: str
    ) -> ValidationResult:
        """Alle Befunde; nur ein Ergebnis ohne Fehler darf geschrieben werden."""
        findings = list(prepared.findings)
        document = prepared.document
        if encoding not in self._spec.document["encodings"]:
            findings.append(
                _error("EXP_ENCODING_NOT_ALLOWED", f"Encoding {encoding} ist nicht vorgesehen")
            )
        if document is not None:
            findings += self._texts(document, encoding)
            findings += self._business(document)
        if xml is not None:
            findings += self.check_xml(xml, encoding)
            if document is not None:
                findings += self._roundtrip(xml, document)
        if file_name is not None:
            findings += check_file_name(file_name)
        return ValidationResult(tuple(findings))

    @staticmethod
    def _texts(document: ExportDocument, encoding: str) -> list[Finding]:
        findings = []
        for path, text in document.texts():
            if XML_ILLEGAL.search(text):
                findings.append(
                    _error("EXP_ILLEGAL_CHARACTER", f"{path}: unzulässiges Steuerzeichen", path)
                )
            if "€" in text:
                findings.append(
                    _error(
                        "EXP_EURO_SIGN",
                        f"{path}: €-Zeichen wird von Lexware nicht übernommen",
                        path,
                    )
                )
            bad = sorted({ch for ch in text if not _encodable(ch, encoding)})
            if bad:
                findings.append(
                    Finding(
                        "EXP_UNENCODABLE",
                        Severity.ERROR,
                        f"{path}: Zeichen {' '.join(bad)} in {encoding} nicht darstellbar",
                        path,
                        "Zeichen ersetzen oder UTF-8 verwenden (OQ-02)",
                    )
                )
        return findings

    @staticmethod
    def _business(document: ExportDocument) -> list[Finding]:
        findings = []
        if document.currency != "EUR":
            findings.append(
                _error("EXP_CURRENCY", f"Währung {document.currency} nicht unterstützt (OQ-21)")
            )
        if not document.items:
            findings.append(_error("EXP_NO_ITEMS", "Keine exportierbaren Positionen"))
        if document.payment.code not in ALLOWED_CODES[document.payment.kind]:
            findings.append(
                _error("EXP_PAYMENT_CODE", f"Zahlungscode {document.payment.code} unzulässig")
            )
        party = document.invoice_party
        if not all((party.street, party.city, party.postal_code, party.country)) or not (
            party.company or party.last_name
        ):
            findings.append(
                _error(
                    "EXP_INVOICE_PARTY_INCOMPLETE", "INVOICE_PARTY unvollständig", "invoice_party"
                )
            )
        for item in document.items:
            label = f"Position {item.line_no}"
            if item.quantity <= 0 or item.unit_price < 0:
                findings.append(
                    _error(
                        "EXP_ITEM_RANGE", f"{label}: Menge oder Preis außerhalb des Wertebereichs"
                    )
                )
            if (item.unit_price * item.quantity).quantize(
                MONEY_PLACES, ROUND_HALF_UP
            ) != item.line_amount:
                findings.append(
                    _error("EXP_ITEM_TOTAL", f"{label}: Zeilensumme passt nicht zu Menge × Preis")
                )
            if not Decimal(0) <= item.tax_rate < 1:
                findings.append(
                    _error("EXP_TAX_RANGE", f"{label}: Steuersatz {item.tax_rate} ungültig")
                )
            elif item.tax_rate not in USUAL_TAX_RATES:
                findings.append(
                    Finding(
                        "EXP_TAX_UNUSUAL",
                        Severity.WARNING,
                        f"{label}: ungewöhnlicher Steuersatz {item.tax_rate}",
                    )
                )
        return findings

    def check_xml(self, xml: bytes, encoding: str) -> list[Finding]:
        """Deklaration, Wohlgeformtheit, Namespaces und Struktur; auch für Referenzdateien."""
        declaration = DECLARATION.match(xml)
        if declaration is None:
            return [_error("EXP_XML_DECLARATION", "XML-Deklaration fehlt oder ist abweichend")]
        findings = []
        if declaration["encoding"].decode().upper() != encoding.upper():
            findings.append(_error("EXP_XML_ENCODING", "Deklariertes Encoding weicht ab"))
        if has_declarations(xml):
            return [*findings, _error("EXP_XML_DOCTYPE", "DOCTYPE/ENTITY ist nicht erlaubt")]
        try:
            root = parse_xml(xml)
        except UnsafeXmlError as exc:
            return [*findings, _error("EXP_XML_MALFORMED", str(exc))]
        expected_ns = self._spec.document["order_namespace"]
        for element in root.iter():
            wanted = "" if element is root else expected_ns
            if namespace(element.tag) != wanted:
                findings.append(
                    _error(
                        "EXP_XML_NAMESPACE",
                        f"{local_name(element.tag)}: Namespace „{namespace(element.tag)}“",
                    )
                )
                break
        if self._spec.document["xsi_namespace"].encode() not in xml:
            findings.append(_error("EXP_XML_NAMESPACE", "xmlns:xsi fehlt am ORDER-Element"))
        findings += [
            _error("EXP_STRUCTURE", f"{issue.path}: {issue.message}", issue.path)
            for issue in check_structure(self._spec.tree, root)
        ]
        return findings

    def _roundtrip(self, xml: bytes, document: ExportDocument) -> list[Finding]:
        try:
            root = parse_xml(xml)
        except UnsafeXmlError:
            return []
        ns = {"o": self._spec.document["order_namespace"]}
        problems = []
        if root.findtext(".//o:ORDER_ID", namespaces=ns) != document.external_order_id:
            problems.append("ORDER_ID")
        items = root.findall(".//o:ORDER_ITEM", ns)
        if len(items) != len(document.items):
            problems.append("Anzahl Positionen")
        for element, item in zip(items, document.items, strict=False):
            values = {
                "SUPPLIER_AID": element.findtext("o:ARTICLE_ID/o:SUPPLIER_AID", namespaces=ns),
                "QUANTITY": element.findtext("o:QUANTITY", namespaces=ns),
                "PRICE_LINE_AMOUNT": element.findtext(
                    "o:ARTICLE_PRICE/o:PRICE_LINE_AMOUNT", namespaces=ns
                ),
            }
            if values["SUPPLIER_AID"] != item.article_number:
                problems.append(f"Position {item.line_no} Artikelnummer")
            if _decimal(values["QUANTITY"]) != item.quantity:
                problems.append(f"Position {item.line_no} Menge")
            if _decimal(values["PRICE_LINE_AMOUNT"]) != item.line_amount:
                problems.append(f"Position {item.line_no} Zeilensumme")
        if root.findtext(".//o:PAYMENT_TERM", namespaces=ns) != document.payment.code:
            problems.append("Zahlungscode")
        if problems:
            return [
                _error(
                    "EXP_ROUNDTRIP",
                    "Datei weicht von der Exportrepräsentation ab: " + ", ".join(problems),
                )
            ]
        return []


def _encodable(character: str, encoding: str) -> bool:
    try:
        character.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _decimal(text: str | None) -> Decimal | None:
    try:
        return Decimal(text) if text is not None else None
    except InvalidOperation:
        return None
