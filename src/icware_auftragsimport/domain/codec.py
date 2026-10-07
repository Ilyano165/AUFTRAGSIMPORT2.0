"""Verlustfreie JSON-Darstellung von Aufträgen für Datenbank und Revisionen.

Die Ausgabe ist deterministisch (sortierte Schlüssel, Beträge als Text), damit
gleiche Aufträge byteweise gleich gespeichert und verglichen werden können.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any, TypeVar

from .models import (
    Address,
    Article,
    ArticleMatch,
    Contact,
    MatchCandidate,
    MatchStatus,
    MatchStrategy,
    Order,
    OrderLine,
    PaymentMethod,
)
from .provenance import Confidence, Evidence, Field, FieldState, SourceRef
from .status import OrderStatus

FORMAT_VERSION = 1
T = TypeVar("T")
Json = dict[str, Any]


def _source(ref: SourceRef | None) -> Json | None:
    if ref is None:
        return None
    return {"start": ref.line_start, "end": ref.line_end, "excerpt": ref.excerpt}


def _source_dec(data: Json | None) -> SourceRef | None:
    if data is None:
        return None
    return SourceRef(int(data["start"]), int(data["end"]), str(data["excerpt"]))


def _evidence(value: Evidence | None) -> Json | None:
    if value is None:
        return None
    return {
        "method": value.method,
        "reason": value.reason,
        "confidence": value.confidence.value,
        "source": _source(value.source),
    }


def _evidence_dec(data: Json | None) -> Evidence | None:
    if data is None:
        return None
    return Evidence(
        method=str(data["method"]),
        reason=str(data["reason"]),
        confidence=Confidence(data["confidence"]),
        source=_source_dec(data["source"]),
    )


def _field[T](value: Field[T], encode: Callable[[T], Any]) -> Json:
    return {
        "value": None if value.value is None else encode(value.value),
        "state": value.state.value,
        "evidence": _evidence(value.evidence),
        "candidates": [encode(c) for c in value.candidates],
    }


def _field_dec[T](data: Json, decode: Callable[[Any], T]) -> Field[T]:
    raw = data["value"]
    return Field(
        value=None if raw is None else decode(raw),
        state=FieldState(data["state"]),
        evidence=_evidence_dec(data["evidence"]),
        candidates=tuple(decode(c) for c in data["candidates"]),
    )


def _text(value: str) -> str:
    return value


def _decimal(value: Decimal) -> str:
    return str(value)


def _decimal_dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _date(value: date) -> str:
    return value.isoformat()


def _date_dec(value: Any) -> date:
    return date.fromisoformat(str(value))


def _address(value: Address) -> Json:
    return value.values()


def _address_dec(value: Any) -> Address:
    return Address(**{key: str(val) for key, val in dict(value).items()})


def _contact(value: Contact) -> Json:
    return {
        "salutation": value.salutation,
        "first_name": value.first_name,
        "last_name": value.last_name,
        "email": value.email,
        "phone": value.phone,
    }


def _contact_dec(value: Any) -> Contact:
    return Contact(**{key: str(val) for key, val in dict(value).items()})


def _payment_dec(value: Any) -> PaymentMethod:
    return PaymentMethod(value)


def encode_article(article: Article) -> Json:
    """Artikel als JSON-Objekt."""
    return {
        "number": article.number,
        "name": article.name,
        "aliases": list(article.aliases),
        "unit": article.unit,
        "tax_rate": None if article.tax_rate is None else str(article.tax_rate),
        "price": None if article.price is None else str(article.price),
        "active": article.active,
    }


def decode_article(data: Json) -> Article:
    """Gegenstück zu :func:`encode_article`."""
    return Article(
        number=str(data["number"]),
        name=str(data["name"]),
        aliases=tuple(str(a) for a in data["aliases"]),
        unit=str(data["unit"]),
        tax_rate=None if data["tax_rate"] is None else Decimal(data["tax_rate"]),
        price=None if data["price"] is None else Decimal(data["price"]),
        active=bool(data["active"]),
    )


def _match(value: ArticleMatch) -> Json:
    return {
        "status": value.status.value,
        "article": None if value.article is None else encode_article(value.article),
        "strategy": value.strategy.value,
        "reason": value.reason,
        "candidates": [
            {"article": encode_article(c.article), "strategy": c.strategy.value, "score": c.score}
            for c in value.candidates
        ],
        "catalog_version": value.catalog_version,
    }


def _match_dec(data: Json) -> ArticleMatch:
    return ArticleMatch(
        status=MatchStatus(data["status"]),
        article=None if data["article"] is None else decode_article(data["article"]),
        strategy=MatchStrategy(data["strategy"]),
        reason=str(data["reason"]),
        candidates=tuple(
            MatchCandidate(decode_article(c["article"]), MatchStrategy(c["strategy"]), c["score"])
            for c in data["candidates"]
        ),
        catalog_version=None
        if data.get("catalog_version") is None
        else int(data["catalog_version"]),
    )


def _line(value: OrderLine) -> Json:
    return {
        "position": value.position,
        "description": value.description,
        "quantity": _field(value.quantity, _decimal),
        "unit": value.unit,
        "unit_price": _field(value.unit_price, _decimal),
        "article_hint": _field(value.article_hint, _text),
        "match": _match(value.match),
        "source": _source(value.source),
        "detail": value.detail,
    }


def _line_dec(data: Json) -> OrderLine:
    return OrderLine(
        position=int(data["position"]),
        description=str(data["description"]),
        quantity=_field_dec(data["quantity"], _decimal_dec),
        unit=str(data["unit"]),
        unit_price=_field_dec(data["unit_price"], _decimal_dec),
        article_hint=_field_dec(data["article_hint"], str),
        match=_match_dec(data["match"]),
        source=_source_dec(data["source"]),
        detail=str(data["detail"]),
    )


def order_to_dict(order: Order) -> Json:
    """Auftrag als JSON-kompatibles Objekt."""
    return {
        "format": FORMAT_VERSION,
        "id": order.id,
        "profile_id": order.profile_id,
        "mail_id": order.mail_id,
        "revision": order.revision,
        "status": order.status.value,
        "document_number": order.document_number,
        "customer_key": order.customer_key,
        "customer_reference": _field(order.customer_reference, _text),
        "customer_number": _field(order.customer_number, _text),
        "vat_id": _field(order.vat_id, _text),
        "invoice_address": _field(order.invoice_address, _address),
        "delivery_address": _field(order.delivery_address, _address),
        "delivery_same_as_invoice": order.delivery_same_as_invoice,
        "contact": _field(order.contact, _contact),
        "order_date": _field(order.order_date, _date),
        "requested_delivery": _field(order.requested_delivery, _date),
        "payment_method": _field(order.payment_method, lambda p: p.value),
        "iban": _field(order.iban, _text),
        "shipping_method": _field(order.shipping_method, _text),
        "shipping_fee": _field(order.shipping_fee, _decimal),
        "note": order.note,
        "lines": [_line(line) for line in order.lines],
        "acknowledged": sorted(order.acknowledged),
    }


def order_from_dict(data: Json) -> Order:
    """Gegenstück zu :func:`order_to_dict`; wirft ``KeyError`` oder ``ValueError`` bei Schäden."""
    if data.get("format") != FORMAT_VERSION:
        raise ValueError(f"Unbekanntes Auftragsformat {data.get('format')!r}")
    return Order(
        id=str(data["id"]),
        profile_id=str(data.get("profile_id", "")),
        mail_id=data["mail_id"],
        revision=int(data["revision"]),
        status=OrderStatus(data["status"]),
        document_number=data["document_number"],
        customer_key=str(data["customer_key"]),
        customer_reference=_field_dec(data["customer_reference"], str),
        customer_number=_field_dec(data["customer_number"], str),
        vat_id=_field_dec(data["vat_id"], str),
        invoice_address=_field_dec(data["invoice_address"], _address_dec),
        delivery_address=_field_dec(data["delivery_address"], _address_dec),
        delivery_same_as_invoice=bool(data["delivery_same_as_invoice"]),
        contact=_field_dec(data["contact"], _contact_dec),
        order_date=_field_dec(data["order_date"], _date_dec),
        requested_delivery=_field_dec(data["requested_delivery"], _date_dec),
        payment_method=_field_dec(data["payment_method"], _payment_dec),
        iban=_field_dec(data["iban"], str),
        shipping_method=_field_dec(data["shipping_method"], str),
        shipping_fee=_field_dec(data["shipping_fee"], _decimal_dec),
        note=str(data["note"]),
        lines=tuple(_line_dec(line) for line in data["lines"]),
        acknowledged=frozenset(str(k) for k in data["acknowledged"]),
    )


def dumps(data: Json) -> str:
    """Deterministische JSON-Ausgabe."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
