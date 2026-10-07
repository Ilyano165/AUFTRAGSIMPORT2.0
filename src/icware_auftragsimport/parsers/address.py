"""AddressParser: Rechnungs- und Lieferanschrift als vollständige Objekte.

Überschriften zählen nur am Zeilenanfang und mit Doppelpunkt oder allein in der Zeile,
damit Sätze wie „Bitte schicken Sie die Rechnung an:“ keine Anschrift eröffnen.
Anschriften ohne Überschrift (etwa aus der Signatur) werden nur vorgeschlagen.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..domain.countries import country_from_text, postal_code_problem
from ..domain.extraction import ExtractionNote
from ..domain.models import ADDRESS_FIELD_LABELS, Address
from ..domain.provenance import Confidence, Evidence, Field, FieldState
from .common import EMAIL, PERSON, Consumed, ParseContext, note, span, weakest
from .text import Line, LineKind

MAX_BLOCK_LINES = 10
MAX_HEAD_LINES = 4
HEADER_ONLY_MAX_LENGTH = 45
MIN_INLINE_PARTS = 2


class AddressRole(StrEnum):
    """Wofür eine Überschrift eine Anschrift ankündigt."""

    INVOICE = "invoice"
    DELIVERY = "delivery"
    BOTH = "both"


_HEADERS: tuple[tuple[re.Pattern[str], AddressRole, Confidence], ...] = tuple(
    (
        re.compile(
            rf"^[-*•\s]*(?:{pattern})\s*(?:\([^)]*\))?\s*(?P<sep>:)?\s*(?P<rest>.*)$",
            re.IGNORECASE,
        ),
        role,
        confidence,
    )
    for pattern, role, confidence in (
        (
            r"rechnungs-\s*(?:und|&|/)\s*liefer(?:adresse|anschrift)"
            r"|liefer-\s*(?:und|&|/)\s*rechnungs(?:adresse|anschrift)",
            AddressRole.BOTH,
            Confidence.CERTAIN,
        ),
        (
            r"rechnungs(?:adresse|anschrift|empfänger)|rechnung\s+an|billing\s+address"
            r"|invoice\s+address|bill\s+to",
            AddressRole.INVOICE,
            Confidence.CERTAIN,
        ),
        (
            r"(?:an)?liefer(?:adresse|anschrift)|versand(?:adresse|anschrift)|lieferung\s+an"
            r"|liefern\s+an|warenempfänger|shipping\s+address|delivery\s+address|ship\s+to",
            AddressRole.DELIVERY,
            Confidence.CERTAIN,
        ),
        (
            r"(?:unsere\s+|meine\s+)?(?:firmen)?(?:anschrift|adresse)",
            AddressRole.BOTH,
            Confidence.LIKELY,
        ),
    )
)
_SAME = re.compile(
    r"^[-*•\s]*liefer(?:adresse|anschrift|ung)\s*(?:an\s+)?[:=]?\s*(?:ist\s+)?"
    r"(?:(?:wie|gleich|identisch(?:\s+mit)?|entspricht|=|an\s+die)\s*(?:der\s+|die\s+)?"
    r"rechnungs(?:adresse|anschrift)|(?:siehe|wie)\s+oben\b|identisch\b)",
    re.IGNORECASE,
)
_INVOICE_SAME = re.compile(
    r"^[-*•\s]*rechnungs(?:adresse|anschrift)\s*[:=]?\s*(?:ist\s+)?"
    r"(?:wie|gleich|identisch(?:\s+mit)?|entspricht|=)\s*(?:der\s+|die\s+)?"
    r"liefer(?:adresse|anschrift)",
    re.IGNORECASE,
)
_POSTAL_LINE = re.compile(
    r"^(?:(?P<cc>D|DE|A|AT|CH|NL|B|BE|L|LU|F|FR|I|IT|PL|DK)\s?[-–]\s?)?"
    r"(?P<pc>\d{5}|\d{4}\s?[A-Z]{2}|\d{4}|\d{2}-\d{3})\s+"
    r"(?P<city>[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß .'’()/-]{1,60})$"
)
_PREFIX_COUNTRIES = {
    "D": "DE", "DE": "DE", "A": "AT", "AT": "AT", "CH": "CH", "NL": "NL", "B": "BE",
    "BE": "BE", "L": "LU", "LU": "LU", "F": "FR", "FR": "FR", "I": "IT", "IT": "IT",
    "PL": "PL", "DK": "DK",
}  # fmt: skip
_STREET = re.compile(
    r"^(?P<street>\D.*?)\s*(?P<no>\d{1,5}\s?[a-zA-Z]?(?:\s?[-/]\s?\d{1,5}\s?[a-zA-Z]?)?)$"
)
_PO_BOX = re.compile(r"^(?:postfach|pf\.?)\s*(?P<no>\d[\d ]*)$", re.IGNORECASE)
_PHONE = re.compile(
    r"^(?:tel(?:efon)?\.?|fon|phone|mobil|handy)\s*[:.]?\s*(?P<value>\+?\d[\d ()/-]{5,}\d)$",
    re.IGNORECASE,
)
_EMAIL_LABEL = re.compile(r"^e?-?mail\s*[:.]?\s*(?P<value>\S+@\S+)$", re.IGNORECASE)
_FAX = re.compile(r"^(?:fax|telefax)\b", re.IGNORECASE)
_FIELD_LABEL = re.compile(
    r"^(?P<label>firma|firmenname|name|empfänger|abteilung|straße|strasse|str\.|hausnummer|"
    r"hausnr\.?|plz\s*/\s*ort|plz|postleitzahl|ort|stadt|land)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_LABEL_FIELDS = {
    "firma": "company", "firmenname": "company", "name": "name", "empfänger": "name",
    "abteilung": "department", "straße": "street", "strasse": "street", "str.": "street",
    "hausnummer": "house_number", "hausnr": "house_number", "hausnr.": "house_number",
    "plz": "postal_code", "postleitzahl": "postal_code", "ort": "city", "stadt": "city",
}  # fmt: skip
_LEGAL_FORM = re.compile(
    r"(?<![\w])(?:GmbH|gmbh|GMBH|mbH|AG|KG|OHG|UG|GbR|SE|KGaA|PartG|e\.\s?K\.|e\.\s?Kfm\."
    r"|e\.\s?V\.|Ltd\.?|Limited|Inc\.?|LLC|B\.V\.|BV|S\.A\.|SARL|S\.r\.l\.|GesmbH)(?=[\s,.)&]|$)"
)
_DEPARTMENT = re.compile(
    r"^(?:abt(?:eilung)?\.?|einkauf|wareneingang|buchhaltung|rechnungswesen|lager|logistik"
    r"|disposition|kreditorenbuchhaltung|purchasing|accounts payable)\b",
    re.IGNORECASE,
)
_ATTENTION = re.compile(r"^(?:z\.?\s?hd\.?|zu\s+händen|attn\.?)\s*(?P<rest>.+)$", re.IGNORECASE)
_CARE_OF = re.compile(r"^c/o\b", re.IGNORECASE)
_SALUTATION = re.compile(r"^(?:Herrn?|Frau|Hr\.|Fr\.)\s+(?P<name>.+)$")
_STOP = re.compile(
    r"^(?:\d+\s*(?:x|×|stk\.?|stück)\s|(?:mit\s+)?(?:freundlichen|viele|beste|liebe)\s+gr"
    r"|zahl|versand(?:art|kosten)?\s*:|bestell|artikel|pos\.|menge\s*:|anmerkung|bemerkung|"
    r"hinweis|vielen dank)",
    re.IGNORECASE,
)
_GREETING = re.compile(r"(?:grüße|grüßen|gruß|grüsse|gruss|regards)\b|^(?:mfg|lg|vg)\b", re.I)


@dataclass(slots=True)
class _Candidate:
    address: Address
    confidence: Confidence
    reasons: list[str]
    lines: list[Line]
    method: str

    def evidence(self) -> Evidence:
        return Evidence(self.method, "; ".join(self.reasons), self.confidence, span(self.lines))


@dataclass(frozen=True, slots=True)
class AddressResult:
    """Rechnungs- und Lieferanschrift."""

    invoice: Field[Address]
    delivery: Field[Address]
    delivery_same_as_invoice: bool
    notes: tuple[ExtractionNote, ...] = ()


@dataclass(slots=True)
class _Values:
    data: dict[str, str] = field(default_factory=lambda: dict.fromkeys(ADDRESS_FIELD_LABELS, ""))
    reasons: list[str] = field(default_factory=list)
    confidences: list[Confidence] = field(default_factory=list)

    def uncertain(self, reason: str) -> None:
        self.reasons.append(reason)
        self.confidences.append(Confidence.UNCERTAIN)


def _match_header(text: str) -> tuple[AddressRole, Confidence, str] | None:
    for pattern, role, confidence in _HEADERS:
        match = pattern.match(text)
        if match is None:
            continue
        rest = match["rest"].strip()
        if rest and not match["sep"]:
            return None
        if not rest and len(text) > HEADER_ONLY_MAX_LENGTH:
            return None
        return role, confidence, rest
    return None


def _split_street(text: str) -> tuple[str, str] | None:
    if match := _PO_BOX.match(text):
        return "Postfach", match["no"].strip()
    if match := _STREET.match(text):
        return match["street"].strip().rstrip(","), match["no"].replace(" ", "")
    return None


def _apply_label(values: _Values, match: re.Match[str]) -> None:
    label = match["label"].casefold().replace(" ", "")
    value = match["value"].strip()
    if label == "plz/ort":
        if postal := _POSTAL_LINE.match(value):
            values.data["postal_code"], values.data["city"] = postal["pc"], postal["city"].strip()
        return
    if label == "land":
        values.data["country"] = country_from_text(value) or ""
        return
    target = _LABEL_FIELDS.get(label)
    if target == "street" and (parts := _split_street(value)):
        values.data["street"], values.data["house_number"] = parts
    elif target:
        values.data[target] = value


def _assign_head(head: Sequence[str], values: _Values) -> None:
    data = values.data
    unassigned: list[str] = []
    for raw in head:
        text = raw.strip().rstrip(",")
        if match := _ATTENTION.match(text):
            data["name"] = _SALUTATION.sub(r"\g<name>", match["rest"]).strip()
        elif _CARE_OF.match(text) or _DEPARTMENT.match(text):
            data["department"] = text
        elif match := _SALUTATION.match(text):
            data["name"] = match["name"].strip()
        elif _LEGAL_FORM.search(text) and not data["company"]:
            data["company"] = text
        else:
            unassigned.append(text)
    for text in unassigned:
        if not data["name"] and data["company"] and PERSON.fullmatch(text):
            data["name"] = text
            values.confidences.append(Confidence.LIKELY)
        elif not data["company"]:
            data["company"] = text
            values.uncertain(f"„{text}“ als Firma übernommen, keine Rechtsform erkennbar")
        elif not data["name"]:
            data["name"] = text
            values.uncertain(f"„{text}“ als Name übernommen")
        else:
            data["department"] = f"{data['department']} {text}".strip()
            values.uncertain(f"Zusatzzeile „{text}“ als Abteilung übernommen")


def _resolve_country(values: _Values, explicit: str) -> None:
    data = values.data
    postal = data["postal_code"]
    if explicit:
        data["country"] = explicit
        if postal and (problem := postal_code_problem(explicit, postal)):
            values.uncertain(problem)
        return
    if not postal:
        return
    if re.fullmatch(r"\d{5}", postal):
        data["country"] = "DE"
        values.reasons.append("5-stellige PLZ ohne Landesangabe: Deutschland angenommen")
        values.confidences.append(Confidence.LIKELY)
    elif re.fullmatch(r"\d{4}\s?[A-Z]{2}", postal):
        data["country"] = "NL"
        values.confidences.append(Confidence.LIKELY)
    elif re.fullmatch(r"\d{2}-\d{3}", postal):
        data["country"] = "PL"
        values.confidences.append(Confidence.LIKELY)
    else:
        values.uncertain(
            "4-stellige PLZ ohne Landesangabe: Österreich, Schweiz, Belgien, Luxemburg "
            "oder Dänemark möglich"
        )


def _classify_lines(block: Sequence[Line], values: _Values) -> tuple[list[Line], str]:
    free: list[Line] = []
    explicit_country = ""
    for line in block:
        text = line.text.strip()
        if _FAX.match(text):
            continue
        if match := _PHONE.match(text):
            values.data["phone"] = match["value"]
        elif match := _EMAIL_LABEL.match(text):
            values.data["email"] = match["value"].casefold()
        elif EMAIL.fullmatch(text):
            values.data["email"] = text.casefold()
        elif code := country_from_text(text.rstrip(".")):
            explicit_country = code
        elif match := _FIELD_LABEL.match(text):
            _apply_label(values, match)
        else:
            free.append(line)
    return free, explicit_country


def _parse_block(
    block: Sequence[Line], base: Confidence, method: str, intro: str
) -> _Candidate | None:
    values = _Values(reasons=[intro] if intro else [], confidences=[base])
    free, explicit = _classify_lines(block, values)
    postal_index = next(
        (i for i in range(len(free) - 1, -1, -1) if _POSTAL_LINE.match(free[i].text)), None
    )
    head = list(free)
    if postal_index is not None:
        match = _POSTAL_LINE.match(free[postal_index].text)
        if match is None:
            return None
        values.data["postal_code"], values.data["city"] = match["pc"], match["city"].strip()
        explicit = explicit or _PREFIX_COUNTRIES.get((match["cc"] or "").upper(), "")
        head = free[:postal_index]
        for extra in free[postal_index + 1 :]:
            values.uncertain(f"Zeile „{extra.text}“ nach dem Ort nicht zugeordnet")
    if not values.data["street"] and head and postal_index is not None:
        parts = _split_street(head[-1].text)
        if parts:
            values.data["street"], values.data["house_number"] = parts
        else:
            values.data["street"] = head[-1].text.strip()
            values.uncertain("Hausnummer nicht erkannt")
        head = head[:-1]
    if len(head) > MAX_HEAD_LINES:
        values.uncertain("Ungewöhnlich viele Zeilen vor der Straße")
    _assign_head([line.text for line in head[-MAX_HEAD_LINES:]], values)
    _resolve_country(values, explicit)
    address = Address(**values.data)
    if not any((address.company, address.name, address.street, address.postal_code)):
        return None
    if not address.postal_code:
        values.uncertain("Keine PLZ/Ort-Zeile gefunden")
    return _Candidate(address, weakest(values.confidences), values.reasons, list(block), method)


def _collect_block(lines: Sequence[Line], start: int, consumed: Consumed) -> list[Line]:
    block: list[Line] = []
    skipped_blank = False
    for line in lines[start:]:
        if line.kind is LineKind.BLANK:
            if block or skipped_blank:
                break
            skipped_blank = True
            continue
        text = line.text.strip()
        if line in consumed or _match_header(text) or _STOP.match(text) or _SAME.match(text):
            break
        block.append(line)
        if len(block) >= MAX_BLOCK_LINES:
            break
    return block


def _inline_lines(line: Line, rest: str) -> list[Line]:
    parts = [part.strip() for part in re.split(r"\s*[,;|]\s*", rest) if part.strip()]
    return [Line(line.no, part, line.kind, line.quote_depth) for part in parts]


def _is_own(address: Address, own: Sequence[Address]) -> bool:
    def key(a: Address) -> tuple[str, str]:
        return a.postal_code.replace(" ", ""), re.sub(r"\W", "", a.street.casefold())

    return any(key(address) == key(o) and o.postal_code for o in own)


class AddressParser:
    """Findet beschriftete Anschriften und schlägt unbeschriftete nur zur Prüfung vor."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> AddressResult:
        """Ergebnis mit Rechnungs- und Lieferanschrift; nichts wird still ergänzt."""
        lines = ctx.content()
        notes: list[ExtractionNote] = []
        same_line = next((ln for ln in lines if ln.is_content and _SAME.match(ln.text)), None)
        if same_line:
            consumed.add(same_line)
        invoice_same = next(
            (ln for ln in lines if ln.is_content and _INVOICE_SAME.match(ln.text)), None
        )
        if invoice_same:
            consumed.add(invoice_same)
        labeled = self._labeled(lines, consumed, ctx.own_addresses, notes)
        if invoice_same and labeled[AddressRole.DELIVERY] and not labeled[AddressRole.INVOICE]:
            candidate = labeled[AddressRole.DELIVERY][0]
            reason = "Rechnungsanschrift laut Mail wie Lieferanschrift"
            evidence = Evidence("labeled_same", reason, candidate.confidence, invoice_same.ref())
            return AddressResult(
                Field.found(candidate.address, evidence),
                Field(None, FieldState.RECOGNIZED, evidence),
                True,
                tuple(notes),
            )
        unlabeled: list[_Candidate] = []
        if not labeled[AddressRole.INVOICE] and not labeled[AddressRole.BOTH]:
            unlabeled = self._unlabeled(lines, consumed, ctx.own_addresses, notes)
        invoice = self._invoice(labeled, unlabeled)
        delivery, same = self._delivery(labeled, invoice, same_line, notes)
        return AddressResult(invoice, delivery, same, tuple(notes))

    def _labeled(
        self,
        lines: Sequence[Line],
        consumed: Consumed,
        own: Sequence[Address],
        notes: list[ExtractionNote],
    ) -> dict[AddressRole, list[_Candidate]]:
        found: dict[AddressRole, list[_Candidate]] = {role: [] for role in AddressRole}
        for index, line in enumerate(lines):
            if not line.is_content or line in consumed or _SAME.match(line.text):
                continue
            if _INVOICE_SAME.match(line.text):
                continue
            header = _match_header(line.text)
            if header is None:
                continue
            role, confidence, rest = header
            block = _inline_lines(line, rest) if rest else []
            if len(block) < MIN_INLINE_PARTS or not any(_POSTAL_LINE.match(b.text) for b in block):
                block += _collect_block(lines, index + 1, consumed)
            intro = f"Unter der Überschrift „{line.text.split(':')[0].strip()}“"
            candidate = _parse_block(block, confidence, "labeled_block", intro)
            consumed.add(line, *block)
            if candidate is None:
                continue
            if _is_own(candidate.address, own):
                notes.append(note("OWN_ADDRESS_IGNORED", "Eigene Anschrift ignoriert", line))
                continue
            found[role].append(candidate)
        return found

    def _unlabeled(
        self,
        lines: Sequence[Line],
        consumed: Consumed,
        own: Sequence[Address],
        notes: list[ExtractionNote],
    ) -> list[_Candidate]:
        candidates = []
        for index, line in enumerate(lines):
            if not line.is_content or line in consumed or not _POSTAL_LINE.match(line.text):
                continue
            start = index
            while (
                start > 0
                and index - start <= MAX_HEAD_LINES
                and lines[start - 1].is_content
                and lines[start - 1] not in consumed
                and not _GREETING.search(lines[start - 1].text)
            ):
                start -= 1
            end = index + 1
            while (
                end < len(lines)
                and lines[end].is_content
                and lines[end] not in consumed
                and (
                    _PHONE.match(lines[end].text)
                    or _EMAIL_LABEL.match(lines[end].text)
                    or _FAX.match(lines[end].text)
                    or country_from_text(lines[end].text.rstrip("."))
                )
            ):
                end += 1
            block = list(lines[start:end])
            in_signature = line.kind is LineKind.SIGNATURE
            intro = "Anschrift aus der Signatur" if in_signature else "Anschrift ohne Überschrift"
            method = "signature_block" if in_signature else "unlabeled_block"
            candidate = _parse_block(block, Confidence.UNCERTAIN, method, intro)
            if candidate is None or not candidate.address.street:
                continue
            consumed.add(*block)
            if _is_own(candidate.address, own):
                notes.append(note("OWN_ADDRESS_IGNORED", "Eigene Anschrift ignoriert", line))
                continue
            candidates.append(candidate)
        return candidates

    @staticmethod
    def _invoice(
        labeled: dict[AddressRole, list[_Candidate]], unlabeled: list[_Candidate]
    ) -> Field[Address]:
        chosen = labeled[AddressRole.INVOICE] or labeled[AddressRole.BOTH]
        if chosen:
            return _field(chosen, "Mehrere Rechnungsanschriften angegeben")
        if unlabeled:
            best = unlabeled[-1]
            reason = f"{best.evidence().reason}; als Rechnungsanschrift vorgeschlagen"
            evidence = Evidence(best.method, reason, Confidence.UNCERTAIN, best.evidence().source)
            return Field.review(best.address, evidence, tuple(c.address for c in unlabeled))
        delivery = labeled[AddressRole.DELIVERY]
        if delivery:
            reason = "Keine Rechnungsanschrift angegeben; Lieferanschrift als Vorschlag übernommen"
            source = delivery[0].evidence().source
            return Field.review(
                delivery[0].address, Evidence("suggested", reason, Confidence.UNCERTAIN, source)
            )
        return Field.unknown("Keine Anschrift gefunden")

    @staticmethod
    def _delivery(
        labeled: dict[AddressRole, list[_Candidate]],
        invoice: Field[Address],
        same_line: Line | None,
        notes: list[ExtractionNote],
    ) -> tuple[Field[Address], bool]:
        explicit = labeled[AddressRole.DELIVERY]
        if explicit and same_line is not None:
            notes.append(
                note("DELIVERY_CONFLICT", "Lieferanschrift und „wie Rechnung“ angegeben", same_line)
            )
            field_value = _field(explicit, "Mehrere Lieferanschriften angegeben")
            conflict = Evidence(
                "conflict",
                "Abweichende Lieferanschrift und „wie Rechnungsanschrift“ zugleich angegeben",
                Confidence.UNCERTAIN,
                field_value.evidence.source if field_value.evidence else None,
            )
            return replace(field_value, state=FieldState.NEEDS_REVIEW, evidence=conflict), False
        if explicit:
            return _field(explicit, "Mehrere Lieferanschriften angegeben"), False
        if same_line is not None:
            reason = "Lieferanschrift laut Mail wie Rechnungsanschrift"
            evidence = Evidence("labeled_same", reason, Confidence.CERTAIN, same_line.ref())
            return Field(None, FieldState.RECOGNIZED, evidence), True
        if labeled[AddressRole.BOTH]:
            candidate = labeled[AddressRole.BOTH][0]
            evidence = Evidence(
                "labeled_both",
                "Gemeinsame Rechnungs- und Lieferanschrift",
                candidate.confidence,
                candidate.evidence().source,
            )
            return Field(None, FieldState.RECOGNIZED, evidence), True
        if invoice.has_value:
            reason = "Keine abweichende Lieferanschrift angegeben: Lieferung an Rechnungsanschrift"
            return Field(
                None, FieldState.RECOGNIZED, Evidence("default_same", reason, Confidence.LIKELY)
            ), True
        return Field.unknown("Keine Lieferanschrift gefunden"), False


def _field(candidates: list[_Candidate], conflict_reason: str) -> Field[Address]:
    first = candidates[0]
    if len(candidates) == 1:
        return Field.found(first.address, first.evidence())
    evidence = Evidence("conflict", conflict_reason, Confidence.UNCERTAIN, first.evidence().source)
    return Field.review(first.address, evidence, tuple(c.address for c in candidates))
