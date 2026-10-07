"""ContactParser: Ansprechpartner des Kunden."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..domain.models import Contact
from ..domain.provenance import Confidence, Field
from .common import EMAIL, PERSON, Consumed, ParseContext, evidence, split_person_name, weakest
from .text import Line

GREETING_LOOKAHEAD = 2
_LABEL = re.compile(
    r"^[-*•\s]*(?:ihr\s+)?(?:ansprechpartner(?:in)?|kontakt(?:person)?|besteller(?:in)?|"
    r"bearbeiter(?:in)?)\s*:\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_PHONE = re.compile(
    r"^[-*•\s]*(?:tel(?:efon)?\.?|fon|phone|mobil|handy|mobile)\s*[:.]?\s*"
    r"(?P<value>\+?\d[\d ()/-]{5,}\d)\s*$",
    re.IGNORECASE,
)
_EMAIL_LABEL = re.compile(r"^[-*•\s]*e?-?mail\s*[:.]?\s*(?P<value>\S+@\S+)\s*$", re.IGNORECASE)
_GREETING = re.compile(
    r"^(?:mit\s+)?(?:(?:sehr\s+)?(?:freundliche[mn]?|beste[mn]?|herzliche[mn]?|viele[mn]?|"
    r"liebe[mn]?|schöne[mn]?|sonnige[mn]?)\s+)?(?:grüße[mn]?|grüsse[mn]?|gruß|gruss)\b.*$"
    r"|^(?:mfg|lg|vg|bg)\b.*$|^(?:best|kind|warm)?\s*regards\b.*$",
    re.IGNORECASE,
)
_SALUTATION = re.compile(r"^(?P<salutation>Herrn?|Frau)\s+(?P<name>.+)$")


@dataclass(frozen=True, slots=True)
class ContactResult:
    """Erkannter Ansprechpartner."""

    field: Field[Contact]


class ContactParser:
    """Beschriftung vor Grußformel; Mailadresse zuletzt aus dem (Original-)Absender."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> ContactResult:
        """Bei Weiterleitungen gilt der ursprüngliche Absender, nicht der Weiterleitende."""
        lines = [line for line in ctx.content() if line.is_content]
        name_hit = self._labeled_name(lines, consumed) or self._greeting_name(lines)
        phone = self._first(lines, _PHONE, consumed)
        mail = self._first(lines, _EMAIL_LABEL, consumed)
        sender, _sender_name, forwarded = ctx.customer_sender()
        confidences = []
        contact = Contact()
        sources: list[Line] = []
        if name_hit:
            salutation, first, last, confidence, line = name_hit
            contact = Contact(salutation=salutation, first_name=first, last_name=last)
            confidences.append(confidence)
            sources.append(line)
        if phone:
            contact = replace(contact, phone=phone[0])
            sources.append(phone[1])
        if mail and EMAIL.fullmatch(mail[0]):
            contact = replace(contact, email=mail[0].casefold())
            sources.append(mail[1])
        elif sender:
            contact = replace(contact, email=sender)
            confidences.append(Confidence.LIKELY)
        if contact == Contact():
            return ContactResult(Field.unknown("Kein Ansprechpartner erkennbar"))
        reason = "Ansprechpartner aus " + (
            "beschrifteten Angaben und Grußformel" if sources else "dem Absender"
        )
        if forwarded:
            reason += " (Absender der weitergeleiteten Originalmail)"
        return ContactResult(
            Field.found(contact, evidence("contact", reason, weakest(confidences), *sources))
        )

    @staticmethod
    def _labeled_name(
        lines: list[Line], consumed: Consumed
    ) -> tuple[str, str, str, Confidence, Line] | None:
        for line in lines:
            if match := _LABEL.match(line.text):
                consumed.add(line)
                return _name_parts(match["value"], line)
        return None

    @staticmethod
    def _greeting_name(lines: list[Line]) -> tuple[str, str, str, Confidence, Line] | None:
        for index, line in enumerate(lines):
            if not _GREETING.match(line.text):
                continue
            for candidate in lines[index + 1 : index + 1 + GREETING_LOOKAHEAD]:
                text = candidate.text.rstrip(",.")
                if PERSON.fullmatch(text) or _SALUTATION.match(text):
                    return _name_parts(text, candidate)
        return None

    @staticmethod
    def _first(
        lines: list[Line], pattern: re.Pattern[str], consumed: Consumed
    ) -> tuple[str, Line] | None:
        for line in lines:
            if match := pattern.match(line.text):
                consumed.add(line)
                return match["value"].strip(), line
        return None


def _name_parts(text: str, line: Line) -> tuple[str, str, str, Confidence, Line]:
    salutation = ""
    if match := _SALUTATION.match(text.strip()):
        salutation = "Herr" if match["salutation"].startswith("Herr") else "Frau"
        text = match["name"]
    first, last, confidence = split_person_name(text)
    return salutation, first, last, confidence, line
