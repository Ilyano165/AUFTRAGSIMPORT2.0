"""OrderLineParser: Bestellpositionen mit Menge, Einheit, Preis und Artikelnummer.

Sichere Positionen brauchen eine Einheit oder ein „x“. Zeilen wie „5 Rakel Gold“ ohne
Einheit werden nur als schwache Kandidaten geliefert; die Engine übernimmt sie nur mit
zusätzlichem Beleg und sonst als sichtbaren Hinweis. Attributzeilen wie „Art.-Nr.: …“
gehören ausschließlich zur unmittelbar darüberstehenden Position.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from ..domain.extraction import ExtractionNote
from ..domain.models import OrderLine
from ..domain.provenance import Confidence, Evidence, Field
from .common import Consumed, ParseContext, evidence, note, span, strip_bullet
from .numbers import parse_decimal_de
from .text import Line, LineKind

UNIT_WORDS: dict[str, str] = {
    "x": "", "×": "", "*": "", "stk": "Stk", "stk.": "Stk", "stück": "Stk", "st.": "Stk",
    "st": "Stk", "ve": "VE", "karton": "Karton", "kartons": "Karton", "krt.": "Karton",
    "kiste": "Kiste", "kisten": "Kiste", "palette": "Palette", "paletten": "Palette",
    "pal.": "Palette", "fl.": "Flasche", "flasche": "Flasche", "flaschen": "Flasche",
    "pck": "Pack", "pck.": "Pack", "pack": "Pack", "packung": "Pack", "packungen": "Pack",
    "rolle": "Rolle", "rollen": "Rolle", "kg": "kg", "l": "l", "m": "m", "lfm": "m",
    "set": "Set", "sets": "Set", "paar": "Paar", "beutel": "Beutel", "eimer": "Eimer",
    "dose": "Dose", "dosen": "Dose", "gebinde": "Gebinde", "fass": "Fass", "fässer": "Fass",
    "träger": "Träger", "tray": "Tray", "trays": "Tray", "sack": "Sack", "säcke": "Sack",
    "kanister": "Kanister", "bund": "Bund", "meter": "m", "liter": "l",
}  # fmt: skip
SINGLE_LETTER_UNITS = frozenset({"m", "l"})
STOP_WORDS = frozenset(
    {
        "tage", "tag", "wochen", "woche", "monate", "monat", "jahre", "jahr", "uhr", "stunden",
        "stunde", "minuten", "prozent", "kw", "mal", "personen", "leute", "mitarbeiter", "grad",
        "cm", "mm", "km", "euro", "eur", "seiten", "punkte",
    }
)  # fmt: skip
_QTY = r"(?P<qty>\d{1,3}(?:\.\d{3})+(?:,\d{1,3})?|\d{1,6}(?:[.,]\d{1,3})?)"
_UNIT = r"(?P<unit>[x×*]|[A-Za-zÄÖÜäöüß]{1,12}\.?)"
_LEADING = re.compile(rf"^{_QTY}\s*{_UNIT}\s+(?P<rest>\S.*)$")
_TRAILING = re.compile(
    rf"^(?P<rest>\S.*?)\s*(?:[-–:,|]\s*|\s)(?P<label>(?:menge|anzahl|stückzahl|qty)\s*[:=]?\s*)?"
    rf"{_QTY}\s*(?P<unit>[x×]|[A-Za-zÄÖÜäöüß]{{2,12}}\.?)?\s*$",
    re.IGNORECASE,
)
_TRAILING_X = re.compile(rf"^(?P<rest>\S.*?)\s+[x×]\s*{_QTY}\s*$")
_BARE = re.compile(r"^(?P<qty>\d{1,4})\s+(?P<rest>[A-Za-zÄÖÜäöüß].{2,})$")
_CODE = r"(?=[A-Za-z0-9./-]*\d)(?=[A-Za-z0-9./-]*[A-Z])[A-Z0-9][A-Za-z0-9./-]{3,39}"
_INLINE_NUMBER = re.compile(
    r"(?<![\s,;(\[–-])[\s,;(\[–-]*(?:art(?:ikel)?\.?\s*-?\s*(?:nr|nummer)\.?|artnr\.?|sku)\s*[:.]?\s*"
    r"(?P<num>[A-Za-z0-9][A-Za-z0-9\-./]{1,39}?)\s*[)\]]?(?=[\s,;]|$)",
    re.IGNORECASE,
)
_PRICE = re.compile(
    r"(?:\s+(?:à|a|je|@)|[,;]?\s*(?:ep|einzelpreis|stückpreis|preis)\s*:?)\s*"
    r"(?P<price>\d[\d.,]*)\s*(?:€|eur(?:o)?)?(?:\s*/\s*(?:stk\.?|stück|st\.?|ve|kiste|karton))?",
    re.IGNORECASE,
)
_TRAILING_PRICE = re.compile(
    r"\s+(?P<price>\d[\d.,]*)\s*(?:€|eur(?:o)?)(?:\s*/\s*(?:stk\.?|stück|st\.?))?\s*$",
    re.IGNORECASE,
)
_TOTAL = re.compile(r"\s*=\s*\d[\d.,]*\s*(?:€|eur(?:o)?)?\s*$", re.IGNORECASE)
_CODE_START = re.compile(rf"^(?P<code>{_CODE})\s*(?:[-–:]\s*)?(?P<desc>\S.*)$")
_CODE_PAREN = re.compile(rf"\s*\((?P<code>{_CODE})\)\s*$")
_ATTR_NUMBER = re.compile(
    r"^(?:art(?:ikel)?\.?\s*-?\s*(?:nr|nummer)\.?|artikelnummer|artnr\.?|sku)\s*[:.]?\s*"
    r"(?P<num>[A-Za-z0-9][A-Za-z0-9\-./]{1,39})$",
    re.IGNORECASE,
)
_ATTR_PRICE = re.compile(
    r"^(?:einzelpreis|stückpreis|preis|ep)\s*[:.]?\s*(?P<price>\d[\d.,]*)\s*(?:€|eur(?:o)?)?"
    r"\s*(?:/\s*\S+)?$",
    re.IGNORECASE,
)
_ATTR_DETAIL = re.compile(
    r"^(?P<label>farbe|größe|groesse|variante|ausführung|länge|breite|stärke|material|farbton"
    r"|format)\s*:\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_BLOCK = re.compile(
    r"^(?P<label>artikelbezeichnung|artikelnummer|art(?:ikel)?\.?\s*-?\s*(?:nr|nummer)\.?|artnr\.?"
    r"|sku|artikel|bezeichnung|produkt|beschreibung|menge|anzahl|stückzahl|einheit|einzelpreis"
    r"|stückpreis|preis)\s*[:=]\s*(?P<value>.+)$",
    re.IGNORECASE,
)
_BLOCK_QTY = re.compile(rf"^{_QTY}\s*(?P<unit>[A-Za-zÄÖÜäöüß]{{1,12}}\.?)?$")
_TABLE_SPLIT = {"tab": re.compile(r"\t"), "semicolon": re.compile(r";"), "pipe": re.compile(r"\|")}
_TABLE_SPACES = re.compile(r"\s{2,}")
_DIVIDER = re.compile(r"^[\s|:+-]+$")
HEADER_ROLES: dict[str, str] = {
    "menge": "qty", "anzahl": "qty", "stück": "qty", "stückzahl": "qty", "qty": "qty",
    "quantity": "qty", "stk": "qty", "artnr": "number", "artikelnr": "number",
    "artikelnummer": "number", "sku": "number", "nr": "number", "nummer": "number",
    "bezeichnung": "desc", "artikel": "desc", "artikelbezeichnung": "desc", "produkt": "desc",
    "beschreibung": "desc", "einheit": "unit", "me": "unit", "preis": "price",
    "einzelpreis": "price", "ep": "price", "epreis": "price", "stückpreis": "price",
    "pos": "pos", "position": "pos", "gesamt": "total", "gesamtpreis": "total",
    "summe": "total", "betrag": "total",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class WeakLine:
    """Zeile, die nach Position aussieht, aber keine Einheit hat."""

    line: OrderLine
    source: Line
    adjacent_to_item: bool


@dataclass(frozen=True, slots=True)
class OrderLineResult:
    """Sichere Positionen, schwache Kandidaten und Hinweise."""

    lines: tuple[OrderLine, ...]
    weak: tuple[WeakLine, ...]
    notes: tuple[ExtractionNote, ...]


@dataclass(slots=True)
class _Item:
    first_line: int
    description: str
    quantity: Field[Decimal]
    unit: str = ""
    price: Field[Decimal] = field(default_factory=Field)
    hint: Field[str] = field(default_factory=Field)
    detail: list[str] = field(default_factory=list)
    sources: list[Line] = field(default_factory=list)

    def build(self) -> OrderLine:
        return OrderLine(
            position=0,
            description=self.description,
            quantity=self.quantity,
            unit=self.unit,
            unit_price=self.price,
            article_hint=self.hint,
            source=span(self.sources),
            detail="; ".join(self.detail),
        )


def _unit(raw: str | None) -> str | None:
    if raw is None:
        return ""
    return UNIT_WORDS.get(raw.casefold())


def _quantity(raw: str, line: Line, method: str, reason: str) -> Field[Decimal]:
    number = parse_decimal_de(raw)
    if number is None:
        text = f"Menge „{raw}“ nicht lesbar"
        return Field.review(None, evidence(method, text, Confidence.UNCERTAIN, line))
    if number.value <= 0:
        text = f"Menge {raw} ist nicht größer als null"
        return Field.review(number.value, evidence(method, text, Confidence.UNCERTAIN, line))
    if number.ambiguous:
        text = f"{reason}; Schreibweise „{raw}“ mehrdeutig"
        return Field.review(number.value, evidence(method, text, Confidence.UNCERTAIN, line))
    return Field.found(number.value, evidence(method, reason, Confidence.CERTAIN, line))


def _price(raw: str, line: Line) -> Field[Decimal]:
    number = parse_decimal_de(raw)
    if number is None:
        return Field.unknown()
    confidence = Confidence.UNCERTAIN if number.ambiguous else Confidence.CERTAIN
    return Field.found(number.value, evidence("price", "Preis aus der Position", confidence, line))


def _split_rest(rest: str, line: Line) -> tuple[str, Field[str], Field[Decimal]]:
    hint: Field[str] = Field.unknown()
    price: Field[Decimal] = Field.unknown()
    rest = _TOTAL.sub("", rest)
    if match := _INLINE_NUMBER.search(rest):
        hint = Field.found(
            match["num"].rstrip("."),
            evidence(
                "labeled_article_number", "Beschriftete Artikelnummer", Confidence.CERTAIN, line
            ),
        )
        rest = rest[: match.start()] + rest[match.end() :]
    if match := _PRICE.search(rest) or _TRAILING_PRICE.search(rest):
        price = _price(match["price"], line)
        rest = rest[: match.start()] + rest[match.end() :]
    if not hint.has_value and (match := _CODE_PAREN.search(rest) or _CODE_START.match(rest)):
        hint = Field.found(
            match["code"],
            evidence("code_token", "Artikelcode in der Position", Confidence.LIKELY, line),
        )
        rest = match["desc"] if "desc" in match.groupdict() else rest[: match.start()]
    return rest.strip(" \t-–:,;|"), hint, price


def _strong(text: str, line: Line) -> _Item | None:
    match = _LEADING.match(text)
    if match and (unit := _unit(match["unit"])) is not None:
        rest = match["rest"]
        reason = "Menge mit Einheit am Zeilenanfang"
    else:
        match = _TRAILING_X.match(text) or _TRAILING.match(text)
        if match is None:
            return None
        unit_raw = match.groupdict().get("unit")
        labeled = bool(match.groupdict().get("label"))
        if unit_raw:
            unit = _unit(unit_raw)
            if unit is None or unit_raw.casefold() in SINGLE_LETTER_UNITS:
                return None
        elif match.re is _TRAILING_X or labeled:
            unit = ""
        else:
            return None
        rest = match["rest"]
        reason = "Menge am Zeilenende"
    description, hint, price = _split_rest(rest, line)
    if not description and not hint.has_value:
        return None
    quantity = _quantity(match["qty"], line, "order_line", reason)
    return _Item(line.no, description, quantity, unit, price, hint, [], [line])


def _attach_attributes(item: _Item, following: Sequence[Line], consumed: Consumed) -> None:
    for line in following:
        if not line.is_content or line in consumed:
            return
        text = strip_bullet(line.text)
        if match := _ATTR_NUMBER.match(text):
            if item.hint.evidence and item.hint.evidence.confidence is Confidence.CERTAIN:
                return
            item.hint = Field.found(
                match["num"].rstrip("."),
                evidence(
                    "attribute_article_number",
                    "Artikelnummer direkt unter der Position",
                    Confidence.CERTAIN,
                    line,
                ),
            )
        elif match := _ATTR_PRICE.match(text):
            item.price = _price(match["price"], line)
        elif match := _ATTR_DETAIL.match(text):
            item.detail.append(f"{match['label'].capitalize()}: {match['value'].strip()}")
        else:
            return
        item.sources.append(line)
        consumed.add(line)


def _header_role(cell: str) -> str | None:
    return HEADER_ROLES.get(re.sub(r"[^a-zäöüß]", "", cell.casefold()))


def _split_cells(text: str, kind: str) -> list[str]:
    parts = (
        _TABLE_SPACES.split(text.strip()) if kind == "spaces" else _TABLE_SPLIT[kind].split(text)
    )
    cells = [cell.strip() for cell in parts]
    if kind == "pipe":
        cells = cells[1:] if cells and not cells[0] else cells
        cells = cells[:-1] if cells and not cells[-1] else cells
    return cells


def _table_header(text: str) -> tuple[str, dict[int, str], int] | None:
    for kind in (*_TABLE_SPLIT, "spaces"):
        cells = _split_cells(text, kind)
        if len(cells) < 2:
            continue
        roles = {i: role for i, cell in enumerate(cells) if (role := _header_role(cell))}
        if "qty" in roles.values() and {"desc", "number"} & set(roles.values()):
            return kind, roles, len(cells)
    return None


class OrderLineParser:
    """Erkennt Tabellen, Zeilenpositionen, beschriftete Blöcke und schwache Kandidaten."""

    def parse(self, ctx: ParseContext, consumed: Consumed) -> OrderLineResult:
        """Doppelte Positionen bleiben getrennt; nichts wird zusammengeführt."""
        lines = ctx.content()
        notes: list[ExtractionNote] = []
        items = self._tables(lines, consumed)
        items += self._single_lines(lines, consumed)
        items += self._blocks(lines, consumed, notes)
        items.sort(key=lambda item: item.first_line)
        built = tuple(
            replace(item.build(), position=index) for index, item in enumerate(items, start=1)
        )
        item_lines = {src.no for item in items for src in item.sources}
        weak = self._weak(lines, consumed, item_lines)
        return OrderLineResult(built, weak, tuple(notes))

    def _single_lines(self, lines: Sequence[Line], consumed: Consumed) -> list[_Item]:
        items = []
        for index, line in enumerate(lines):
            if not line.is_content or line in consumed:
                continue
            text = strip_bullet(line.text)
            if _BLOCK.match(text) or _ATTR_NUMBER.match(text) or _ATTR_PRICE.match(text):
                continue
            item = _strong(text, line)
            if item is None:
                continue
            consumed.add(line)
            _attach_attributes(item, lines[index + 1 :], consumed)
            items.append(item)
        return items

    def _tables(self, lines: Sequence[Line], consumed: Consumed) -> list[_Item]:
        items: list[_Item] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            header = _table_header(line.text) if line.is_content and line not in consumed else None
            if header is None:
                index += 1
                continue
            kind, roles, width = header
            consumed.add(line)
            index += 1
            while index < len(lines) and lines[index].is_content:
                row = lines[index]
                if _DIVIDER.match(row.text):
                    consumed.add(row)
                    index += 1
                    continue
                cells = _split_cells(row.text, kind)
                if len(cells) != width:
                    break
                if item := self._row(cells, roles, row):
                    items.append(item)
                consumed.add(row)
                index += 1
        return items

    @staticmethod
    def _row(cells: list[str], roles: dict[int, str], row: Line) -> _Item | None:
        values = {role: cells[i] for i, role in roles.items()}
        description = values.get("desc", "")
        number = values.get("number", "")
        if not description and not number:
            return None
        quantity = _quantity(values.get("qty", ""), row, "table_row", "Menge aus Tabellenspalte")
        item = _Item(
            row.no, description, quantity, _unit(values.get("unit")) or values.get("unit", "")
        )
        item.sources.append(row)
        if number:
            item.hint = Field.found(
                number,
                evidence(
                    "table_article_number", "Tabellenspalte Artikelnummer", Confidence.CERTAIN, row
                ),
            )
        if values.get("price"):
            item.price = _price(values["price"], row)
        return item

    def _blocks(
        self, lines: Sequence[Line], consumed: Consumed, notes: list[ExtractionNote]
    ) -> list[_Item]:
        items: list[_Item] = []
        current: dict[str, tuple[str, Line]] = {}

        def flush() -> None:
            if current:
                item = self._block_item(current)
                if item is not None:
                    items.append(item)
                    consumed.add(*(line for _, line in current.values()))
                elif "number" in current:
                    _, line = current["number"]
                    notes.append(note("ORPHAN_ARTICLE_NUMBER", "Artikelnummer ohne Position", line))
                current.clear()

        for line in lines:
            match = _BLOCK.match(strip_bullet(line.text)) if line.is_content else None
            if match is None or line in consumed:
                flush()
                continue
            role = _block_role(match["label"])
            if role in current:
                flush()
            current[role] = (match["value"].strip(), line)
        flush()
        return items

    @staticmethod
    def _block_item(values: dict[str, tuple[str, Line]]) -> _Item | None:
        if "qty" not in values or not ({"desc", "number"} & values.keys()):
            return None
        raw_qty, qty_line = values["qty"]
        qty_match = _BLOCK_QTY.match(raw_qty)
        quantity = _quantity(
            qty_match["qty"] if qty_match else raw_qty,
            qty_line,
            "labeled_block",
            "Beschriftete Menge",
        )
        unit_raw = (qty_match["unit"] if qty_match else None) or values.get("unit", ("", qty_line))[
            0
        ]
        sources = sorted((line for _, line in values.values()), key=lambda ln: ln.no)
        item = _Item(sources[0].no, values.get("desc", ("", qty_line))[0], quantity)
        item.unit = _unit(unit_raw) or unit_raw
        item.sources = sources
        if "number" in values:
            number, line = values["number"]
            item.hint = Field.found(
                number,
                evidence(
                    "labeled_article_number", "Beschriftete Artikelnummer", Confidence.CERTAIN, line
                ),
            )
        if "price" in values:
            item.price = _price(*values["price"])
        return item

    @staticmethod
    def _weak(
        lines: Sequence[Line], consumed: Consumed, item_lines: set[int]
    ) -> tuple[WeakLine, ...]:
        weak = []
        content = [line for line in lines if line.kind is not LineKind.BLANK]
        for index, line in enumerate(content):
            if not line.is_content or line in consumed:
                continue
            match = _BARE.match(strip_bullet(line.text))
            if match is None or "?" in line.text:
                continue
            if match["rest"].split()[0].casefold().rstrip(".,") in STOP_WORDS:
                continue
            neighbours = {content[i].no for i in (index - 1, index + 1) if 0 <= i < len(content)}
            adjacent = bool(neighbours & item_lines) and _no_gap(
                lines, line, neighbours & item_lines
            )
            reason = "Menge ohne Einheit"
            quantity = _quantity(match["qty"], line, "bare_quantity", reason)
            quantity = Field.review(
                quantity.value, Evidence("bare_quantity", reason, Confidence.UNCERTAIN, line.ref())
            )
            order_line = OrderLine(0, match["rest"].strip(), quantity, source=line.ref())
            weak.append(WeakLine(order_line, line, adjacent))
        return tuple(weak)


def _no_gap(lines: Sequence[Line], line: Line, neighbours: set[int]) -> bool:
    numbers = {ln.no: ln for ln in lines}
    for other in neighbours:
        low, high = sorted((other, line.no))
        if any(numbers[n].kind is LineKind.BLANK for n in range(low + 1, high) if n in numbers):
            return False
    return True


def _block_role(label: str) -> str:
    key = label.casefold()
    if "nr" in key or "nummer" in key or key == "sku":
        return "number"
    if key in ("menge", "anzahl", "stückzahl"):
        return "qty"
    if key == "einheit":
        return "unit"
    if "preis" in key:
        return "price"
    return "desc"
