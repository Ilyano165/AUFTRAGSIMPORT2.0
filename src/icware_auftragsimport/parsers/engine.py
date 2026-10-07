"""Erkennungsengine: führt die Pipeline-Schritte 6 bis 11 für eine Mail aus."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from ..domain.extraction import ExtractionNote, ExtractionResult
from ..domain.models import OrderLine
from ..domain.provenance import Confidence, Evidence, Field
from .address import AddressParser
from .common import Consumed, ParseContext, note
from .contact import ContactParser
from .customer import CustomerParser
from .dates import DateParser
from .order_lines import OrderLineParser, WeakLine
from .order_number import OrderNumberParser
from .payment import PaymentParser
from .shipping import ShippingParser

PARSER_VERSION = "2.0-rules.1"
_ORDER_WORDS = re.compile(
    r"\b(?:bestellung|bestellen|bestelle|bestellt|nachbestellung|auftrag|order|"
    r"bitte\s+liefern|liefern\s+sie|wir\s+benötigen)\b",
    re.IGNORECASE,
)

ArticleNameLookup = Callable[[str], bool]


class ExtractionEngine:
    """Ruft die spezialisierten Parser in fester Reihenfolge auf.

    Beschriftete Einzelfelder und Anschriften laufen vor den Positionen, damit deren
    Zeilen nicht als Positionen fehlgedeutet werden. ``known_article_name`` erlaubt,
    Kandidaten ohne Einheit bei exaktem Katalogtreffer zu übernehmen.
    """

    def __init__(self, known_article_name: ArticleNameLookup | None = None) -> None:
        self._known = known_article_name

    def extract(self, ctx: ParseContext) -> ExtractionResult:
        """Liefert alle erkannten Werte mit Herkunft; wirft keine Parserfehler nach außen."""
        consumed = Consumed()
        order_number = OrderNumberParser().parse(ctx, consumed)
        customer = CustomerParser().parse(ctx, consumed)
        dates = DateParser().parse(ctx, consumed)
        payment = PaymentParser().parse(ctx, consumed)
        shipping = ShippingParser().parse(ctx, consumed)
        addresses = AddressParser().parse(ctx, consumed)
        contact = ContactParser().parse(ctx, consumed)
        lines = OrderLineParser().parse(ctx, consumed)
        accepted, weak_notes = self._resolve_weak(lines.lines, lines.weak)
        notes = [*dates.notes, *addresses.notes, *lines.notes, *weak_notes, *self._text_notes(ctx)]
        customer_email, _name, _forwarded = ctx.customer_sender()
        return ExtractionResult(
            parser_version=PARSER_VERSION,
            customer_email=customer_email,
            is_order=self._detect_order(ctx, accepted),
            customer_reference=order_number.field,
            customer_number=customer.customer_number,
            vat_id=customer.vat_id,
            invoice_address=addresses.invoice,
            delivery_address=addresses.delivery,
            delivery_same_as_invoice=addresses.delivery_same_as_invoice,
            contact=contact.field,
            order_date=dates.order_date,
            requested_delivery=dates.requested_delivery,
            payment_method=payment.method,
            iban=payment.iban,
            shipping_method=shipping.method,
            shipping_fee=shipping.fee,
            lines=accepted,
            notes=tuple(notes),
        )

    def _resolve_weak(
        self, strong: tuple[OrderLine, ...], weak: tuple[WeakLine, ...]
    ) -> tuple[tuple[OrderLine, ...], list[ExtractionNote]]:
        kept: list[OrderLine] = list(strong)
        notes: list[ExtractionNote] = []
        for candidate in weak:
            known = self._known is not None and self._known(candidate.line.description)
            if known:
                reason = "Menge ohne Einheit; Artikelname exakt im Katalog gefunden"
                confidence = Confidence.LIKELY
            elif candidate.adjacent_to_item:
                reason = "Menge ohne Einheit; direkt neben erkannten Positionen"
                confidence = Confidence.UNCERTAIN
            else:
                message = (
                    f"Zeile {candidate.source.no} sieht wie eine Position aus, wurde aber nicht "
                    f"übernommen: „{candidate.source.text}“"
                )
                notes.append(note("POSSIBLE_LINE_SKIPPED", message, candidate.source))
                continue
            quantity = candidate.line.quantity
            evidence = Evidence("bare_quantity", reason, confidence, candidate.source.ref())
            kept.append(
                replace(candidate.line, quantity=Field.found(quantity.value, evidence))
                if quantity.value is not None
                else candidate.line
            )
        kept.sort(key=lambda line: line.source.line_start if line.source else 0)
        return tuple(replace(line, position=i) for i, line in enumerate(kept, start=1)), notes

    @staticmethod
    def _detect_order(ctx: ParseContext, lines: tuple[OrderLine, ...]) -> Field[bool]:
        if lines:
            reason = f"{len(lines)} Position(en) erkannt"
            return Field.found(True, Evidence("order_lines", reason, Confidence.CERTAIN))
        body = "\n".join(line.text for line in ctx.body())
        if _ORDER_WORDS.search(ctx.subject) or _ORDER_WORDS.search(body):
            reason = "Bestellbegriffe gefunden, aber keine Positionen erkannt"
            return Field.review(True, Evidence("keywords", reason, Confidence.UNCERTAIN))
        reason = "Weder Positionen noch Bestellbegriffe gefunden"
        return Field.review(False, Evidence("none", reason, Confidence.UNCERTAIN))

    @staticmethod
    def _text_notes(ctx: ParseContext) -> list[ExtractionNote]:
        notes = []
        if ctx.text.forwarded and not ctx.text.original_sender:
            notes.append(
                note(
                    "FORWARD_SENDER_UNKNOWN",
                    "Weitergeleitete Mail ohne erkennbaren Originalabsender",
                )
            )
        if ctx.text.truncated:
            notes.append(note("TEXT_TRUNCATED", "Mailtext sehr lang; nur der Anfang wurde gelesen"))
        return notes
