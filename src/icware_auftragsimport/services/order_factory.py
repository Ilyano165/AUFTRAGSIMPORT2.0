"""Baut aus Erkennungsergebnis, Katalog und Importprofil einen Auftrag."""

from __future__ import annotations

from dataclasses import replace

from ..config.schema import ImportProfile
from ..domain.extraction import ExtractionResult
from ..domain.identity import customer_key
from ..domain.models import ArticleMatch, MatchStatus, Order, PaymentMethod
from ..domain.provenance import Confidence, Evidence, Field, FieldState
from ..domain.status import OrderStatus
from .matching import ArticleMatcher

NO_CATALOG_REASON = "Kein Artikelkatalog für dieses Importprofil aktiv"


def _payment(result: ExtractionResult, profile: ImportProfile) -> Field[PaymentMethod]:
    if (
        result.payment_method.state is not FieldState.UNKNOWN
        or profile.default_payment_method is None
    ):
        return result.payment_method
    default = profile.default_payment_method
    reason = f"Keine Zahlungsart in der Mail; Standard des Importprofils „{profile.name}“"
    return Field.found(default, Evidence("profile_default", reason, Confidence.LIKELY))


def build_order(
    order_id: str,
    mail_id: str | None,
    result: ExtractionResult,
    *,
    sender_email: str,
    profile: ImportProfile,
    matcher: ArticleMatcher | None,
) -> Order:
    """Neuer Auftrag in Revision 1; Status wird erst durch die Validierung gesetzt.

    Die Kundenkennung für den Duplikatschutz stammt aus der Kundennummer, sonst aus der
    Domain des eigentlichen Bestellers; ``sender_email`` dient nur als Rückfallwert.
    """
    if matcher is None:
        missing = ArticleMatch(MatchStatus.UNKNOWN, reason=NO_CATALOG_REASON)
        lines = tuple(replace(line, match=missing) for line in result.lines)
    else:
        lines = tuple(matcher.match_line(line) for line in result.lines)
    return Order(
        id=order_id,
        mail_id=mail_id,
        status=OrderStatus.NEW,
        customer_key=customer_key(
            result.customer_number.value, result.customer_email or sender_email
        ),
        customer_reference=result.customer_reference,
        customer_number=result.customer_number,
        vat_id=result.vat_id,
        invoice_address=result.invoice_address,
        delivery_address=result.delivery_address,
        delivery_same_as_invoice=result.delivery_same_as_invoice,
        contact=result.contact,
        order_date=result.order_date,
        requested_delivery=result.requested_delivery,
        payment_method=_payment(result, profile),
        iban=result.iban,
        shipping_method=result.shipping_method,
        shipping_fee=result.shipping_fee,
        lines=lines,
    )
