"""Serialisierung der Exportrepräsentation in das Lexware-openTRANS-Format.

Die Reihenfolge der Elemente folgt der Spezifikation ``lexware_opentrans_order_v1.json``;
der ExportValidator prüft das Ergebnis unabhängig davon gegen dieselbe Spezifikation.
"""

from __future__ import annotations

from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

from ..model import ExportDocument, ExportParty, ExportPayment, PaymentKind
from .spec import ExportSpec

MONEY_PLACES = Decimal("0.01")
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def decimal_places(value: Decimal) -> int:
    """Anzahl Nachkommastellen eines Dezimalwerts."""
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def format_money(value: Decimal) -> str:
    """Betrag mit Punkt, mindestens zwei und höchstens vier Nachkommastellen."""
    return f"{value if decimal_places(value) >= 2 else value.quantize(MONEY_PLACES):f}"


def format_quantity(value: Decimal) -> str:
    """Menge ohne überflüssige Nullen, Punkt als Dezimaltrenner."""
    return str(int(value)) if value == value.to_integral_value() else f"{value.normalize():f}"


def format_tax(fraction: Decimal) -> str:
    """Steuersatz als Anteil, etwa ``0.19``."""
    return f"{fraction.normalize():f}" if fraction else "0"


class LexwareXmlSerializer:
    """Schreibt ein ``ExportDocument`` als Lexware-openTRANS-Datei."""

    def __init__(self, spec: ExportSpec) -> None:
        self.spec = spec

    def render(
        self, document: ExportDocument, encoding: str, *, notation: str | None = None
    ) -> bytes:
        """Erzeugt die XML-Datei; wirft ``UnicodeEncodeError`` bei nicht darstellbaren Zeichen."""
        settings = self.spec.document
        writer = _XmlWriter(
            settings["indent"], settings["line_ending"], notation or settings["text_notation"]
        )
        writer.declaration(encoding)
        writer.open("ORDER_LIST")
        writer.open(
            "ORDER",
            (
                ("xmlns", settings["order_namespace"]),
                ("xmlns:xsi", settings["xsi_namespace"]),
                ("version", "1.0"),
                ("type", "standard"),
            ),
        )
        self._header(writer, document)
        self._item_list(writer, document)
        writer.close("ORDER")
        writer.close("ORDER_LIST")
        return writer.text().encode(encoding)

    def _header(self, w: _XmlWriter, d: ExportDocument) -> None:
        w.open("ORDER_HEADER")
        w.open("CONTROL_INFO")
        w.leaf("GENERATOR_INFO", d.generator)
        w.leaf("GENERATOR_DATE", d.generated_at.strftime(DATETIME_FORMAT))
        w.close("CONTROL_INFO")
        w.open("ORDER_INFO")
        w.leaf("ORDER_ID", d.external_order_id)
        w.leaf("ORDER_DATE", f"{d.order_date:%Y-%m-%d} 00:00:00")
        w.open("ORDER_PARTIES")
        for tag, party in (
            ("BUYER_PARTY", d.delivery_party),
            ("INVOICE_PARTY", d.invoice_party),
            ("SUPPLIER_PARTY", d.supplier_party),
        ):
            self._party(w, tag, party)
        w.close("ORDER_PARTIES")
        self._payment(w, d.payment)
        if d.delivery_method:
            w.leaf("REMARK", d.delivery_method, (("type", "delivery_method"),))
        if d.shipping_fee is not None:
            w.leaf("REMARK", format_money(d.shipping_fee), (("type", "shipping_fee"),))
        if d.remark:
            w.leaf("REMARK", d.remark, (("type", "order"),))
        w.close("ORDER_INFO")
        w.close("ORDER_HEADER")

    @staticmethod
    def _party(w: _XmlWriter, tag: str, p: ExportParty) -> None:
        w.open(tag)
        w.open("PARTY")
        w.open("ADDRESS")
        for name, value in (
            ("NAME", p.company), ("NAME2", p.last_name), ("NAME3", p.first_name),
            ("STREET", p.street), ("CITY", p.city), ("ZIP", p.postal_code),
            ("COUNTRY", p.country), ("PHONE", p.phone), ("FAX", p.fax),
            ("EMAIL", p.email), ("VAT_ID", p.vat_id),
        ):  # fmt: skip
            w.leaf(name, value)
        w.close("ADDRESS")
        w.close("PARTY")
        w.close(tag)

    @staticmethod
    def _payment(w: _XmlWriter, payment: ExportPayment) -> None:
        w.open("PAYMENT")
        term = (("TYPE", "unece"),)
        if payment.kind is PaymentKind.CASH:
            w.open("CASH")
            w.leaf("PAYMENT_TERM", payment.code, term)
            w.close("CASH")
        else:
            w.open("ACCOUNT")
            for name in ("HOLDER", "BANK_NAME", "BANK_CODE", "BANK_ACCOUNT"):
                w.leaf(name, "")
            w.leaf("PAYMENT_TERM", payment.code, term)
            w.close("ACCOUNT")
        w.close("PAYMENT")

    @staticmethod
    def _item_list(w: _XmlWriter, d: ExportDocument) -> None:
        w.open("ORDER_ITEM_LIST")
        for item in d.items:
            w.open("ORDER_ITEM")
            w.leaf("LINE_ITEM_ID", str(item.line_no))
            w.open("ARTICLE_ID")
            w.leaf("SUPPLIER_AID", item.article_number)
            w.close("ARTICLE_ID")
            w.leaf("QUANTITY", format_quantity(item.quantity))
            w.leaf("ORDER_UNIT", "")
            w.open("ARTICLE_PRICE", (("type", d.price_type),))
            w.leaf("PRICE_AMOUNT", format_money(item.unit_price))
            w.leaf("PRICE_LINE_AMOUNT", format_money(item.line_amount))
            w.leaf("TAX", format_tax(item.tax_rate))
            w.close("ARTICLE_PRICE")
            w.close("ORDER_ITEM")
        w.close("ORDER_ITEM_LIST")


class _XmlWriter:
    """Schreibt Elemente in fester Reihenfolge mit Einrückung wie die Referenz."""

    def __init__(self, indent: str, newline: str, notation: str) -> None:
        if notation not in ("cdata", "entities"):
            raise ValueError(f"Unbekannte Textnotation {notation}")
        self._indent = indent
        self._newline = newline
        self._notation = notation
        self._lines: list[str] = []
        self._depth = 0

    def declaration(self, encoding: str) -> None:
        self._lines.append(f'<?xml version="1.0" encoding="{encoding}"?>')

    @staticmethod
    def _attributes(attributes: tuple[tuple[str, str], ...]) -> str:
        return "".join(f" {name}={quoteattr(value)}" for name, value in attributes)

    def open(self, tag: str, attributes: tuple[tuple[str, str], ...] = ()) -> None:
        self._lines.append(f"{self._indent * self._depth}<{tag}{self._attributes(attributes)}>")
        self._depth += 1

    def close(self, tag: str) -> None:
        self._depth -= 1
        self._lines.append(f"{self._indent * self._depth}</{tag}>")

    def leaf(self, tag: str, text: str, attributes: tuple[tuple[str, str], ...] = ()) -> None:
        if self._notation == "cdata":
            body = "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"
        else:
            body = escape(text, {"'": "&apos;", '"': "&quot;"})
        self._lines.append(
            f"{self._indent * self._depth}<{tag}{self._attributes(attributes)}>{body}</{tag}>"
        )

    def text(self) -> str:
        return self._newline.join(self._lines)
