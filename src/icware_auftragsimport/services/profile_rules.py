"""Regeln eines Firmenprofils: Absender, Versandarten, Zahlungsarten, Mailvorlage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..config.schema import (
    PLACEHOLDER,
    ImportProfile,
    MailTemplate,
    SenderAction,
    SenderRule,
    ShippingOption,
)
from ..domain.findings import Finding, Severity
from ..domain.models import Order
from .matching import normalize_name


@dataclass(frozen=True, slots=True)
class SenderDecision:
    """Ergebnis der Absenderregeln mit Begründung."""

    action: SenderAction
    rule: SenderRule | None
    explanation: str


def evaluate_sender(profile: ImportProfile, address: str) -> SenderDecision:
    """Genaue Adresse vor Domain, die längste passende Domain gewinnt; sonst Standard des Profils.

    ``@kunde.de`` gilt auch für ``einkauf@nord.kunde.de``, nie für ``kunde.de.example``.
    """
    sender = address.strip().lower()
    domain = sender.rpartition("@")[2]
    exact = next((r for r in profile.sender_rules if r.pattern == sender), None)
    if exact is not None:
        return SenderDecision(exact.action, exact, f"Regel für die Adresse {exact.pattern}")
    domains = [
        r
        for r in profile.sender_rules
        if r.pattern.startswith("@")
        and (domain == r.pattern[1:] or domain.endswith("." + r.pattern[1:]))
    ]
    if domains:
        rule = max(domains, key=lambda r: len(r.pattern))
        return SenderDecision(rule.action, rule, f"Regel für die Domain {rule.pattern}")
    return SenderDecision(profile.sender_default, None, "Keine Regel passt; Standard des Profils")


def match_shipping(profile: ImportProfile, text: str) -> ShippingOption | None:
    """Versandart des Profils, deren Name oder Erkennungsbegriff im Text vorkommt."""
    wanted = normalize_name(text)
    if not wanted:
        return None
    for option in profile.shipping_methods:
        for term in (option.name, *option.aliases):
            key = normalize_name(term)
            if key and (key == wanted or f" {key} " in f" {wanted} "):
                return option
    return None


def template_values(order: Order, profile: ImportProfile) -> dict[str, str]:
    """Werte der Platzhalter für einen Auftrag."""
    invoice = order.invoice_address.value
    contact = order.contact.value
    lines = "\n".join(
        f"{line.position}. {line.quantity.value if line.quantity.value is not None else '?'} × "
        f"{line.match.article.name if line.match.article else line.description}"
        for line in order.lines
    )
    return {
        "firma": (invoice.company or invoice.name) if invoice else "",
        "ansprechpartner": contact.display_name() if contact else "",
        "bestellnummer": order.customer_reference.value or "",
        "belegnummer": order.document_number or "",
        "datum": f"{order.order_date.value:%d.%m.%Y}" if order.order_date.value else "",
        "positionen": lines,
        "lieferant": profile.company_name,
    }


def render_template(template: MailTemplate, values: Mapping[str, str]) -> tuple[str, str]:
    """Betreff und Text mit eingesetzten Werten; unbekannte Platzhalter bleiben sichtbar stehen."""

    def fill(text: str) -> str:
        return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    return fill(template.subject).replace("\n", " ").strip(), fill(template.body)


def profile_findings(order: Order, profile: ImportProfile) -> list[Finding]:
    """Befunde aus den Profilregeln."""
    findings: list[Finding] = []
    if order.profile_id and order.profile_id != profile.id:
        findings.append(
            Finding(
                "PROFILE_MISMATCH",
                Severity.ERROR,
                f"Auftrag gehört zum Profil „{order.profile_id}“, nicht zu „{profile.name}“",
            )
        )
    method = order.payment_method.value
    if method is not None and method not in profile.payment_methods:
        findings.append(
            Finding(
                "PAYMENT_NOT_ALLOWED",
                Severity.WARNING,
                f"Zahlungsart „{method.label}“ ist im Profil nicht freigegeben",
                "payment_method",
                "Zahlungsart ändern oder im Profil freigeben",
            )
        )
    shipping = order.shipping_method.value
    if shipping and profile.shipping_methods and match_shipping(profile, shipping) is None:
        known = ", ".join(o.name for o in profile.shipping_methods)
        findings.append(
            Finding(
                "SHIPPING_UNKNOWN",
                Severity.WARNING,
                f"Versandart „{shipping}“ ist im Profil nicht hinterlegt: bekannt sind {known}",
                "shipping_method",
            )
        )
    return findings
