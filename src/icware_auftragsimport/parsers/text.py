"""Mailtext dekodieren und normalisieren (Pipeline-Schritt 3).

Ergebnis ist eine nummerierte Zeilenliste. Jede Zeile trägt ihre Art, damit
Parser nur echten Bestellinhalt lesen: Antwortverläufe werden ausgeschlossen,
weitergeleitete Bestellungen dagegen entzitiert und gelesen.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterator
from dataclasses import dataclass
from email.utils import parseaddr
from enum import StrEnum
from html.parser import HTMLParser

from ..domain.provenance import SourceRef
from ..security.limits import MAIL, PARSING

MAX_LINES = 5000
MAX_EXCERPT = 160
FALLBACK_CHARSETS = ("utf-8", "cp1252", "latin-1")

_CONTROL = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]"
)
_SPACE_LIKE = re.compile("[\u00a0\u2007\u202f\u2009]")
_QUOTE_PREFIX = re.compile(r"^((?:[ \t]*>)+)[ \t]?")
_FORWARD_MARKERS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^-*\s*anfang der weitergeleiteten (?:nachricht|e-?mail)\s*:?\s*-*$",
        r"^-*\s*begin forwarded message\s*:?\s*-*$",
        r"^-{2,}\s*(?:forwarded message|weitergeleitete nachricht)\s*-{2,}$",
    )
)
_OUTLOOK_MARKERS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^-{2,}\s*ursprüngliche nachricht\s*-{2,}$",
        r"^-{2,}\s*original message\s*-{2,}$",
        r"^_{10,}$",
    )
)
_REPLY_INTRO = re.compile(r"^(?:am|on)\s.+\s(?:schrieb|wrote)\b.*:$", re.IGNORECASE)
_HEADER_LINE = re.compile(
    r"^(?P<name>von|from|datum|date|gesendet|sent|an|to|cc|betreff|subject|antwort an|reply-to)"
    r"\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_FORWARD_SUBJECT = re.compile(r"^\s*(?:wg|fw|fwd|wtr)\s*:", re.IGNORECASE)
_MOBILE_SIGNATURE = re.compile(
    r"^(?:von meinem \S+(?: \S+)? gesendet|sent from my \S+(?: \S+)?|"
    r"gesendet von .+ für (?:ios|android)|get outlook for (?:ios|android))\.?$",
    re.IGNORECASE,
)
_HTML_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class DecodedText:
    """Dekodierter Text mit tatsächlich verwendetem Zeichensatz."""

    text: str
    charset: str
    fallback_used: bool


def decode_payload(payload: bytes, declared: str | None) -> DecodedText:
    """Dekodiert Bytes; unbekannte oder falsche Zeichensätze führen nie zum Abbruch."""
    declared_name: str | None = None
    if declared:
        try:
            declared_name = codecs.lookup(declared.strip().strip('"')).name
        except LookupError:
            declared_name = None
    candidates = [declared_name] if declared_name else []
    candidates += [c for c in FALLBACK_CHARSETS if c != declared_name]
    for charset in candidates:
        try:
            return DecodedText(payload.decode(charset), charset, charset != declared_name)
        except UnicodeDecodeError:
            continue
    return DecodedText(payload.decode("latin-1", errors="replace"), "latin-1", True)


_SKIP_TAGS = frozenset({"script", "style", "head", "title", "template", "noscript"})
_PARAGRAPH_TAGS = frozenset({"p", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCK_TAGS = frozenset(
    {"div", "section", "article", "header", "footer", "pre", "address", "center", "tr", "li", "hr"}
)


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buffer: list[str] = []
        self._depth = 0
        self._line_depth = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "br":
            self._hard_break()
        elif tag == "blockquote":
            self._soft_break()
            self._depth += 1
        elif tag == "li":
            self._soft_break()
            self._write("- ")
        elif tag in ("td", "th"):
            if self._buffer:
                self._write("\t")
        elif tag in _PARAGRAPH_TAGS or tag in _BLOCK_TAGS:
            self._soft_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "blockquote":
            self._soft_break()
            self._depth = max(0, self._depth - 1)
        elif tag in _PARAGRAPH_TAGS:
            self._soft_break()
            self.lines.append(self._prefix(self._depth))
        elif tag in _BLOCK_TAGS:
            self._soft_break()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = _HTML_WHITESPACE.sub(" ", data)
        if not self._buffer:
            text = text.lstrip()
            if not text:
                return
        self._write(text)

    def _write(self, text: str) -> None:
        if not self._buffer:
            self._line_depth = self._depth
        self._buffer.append(text)

    def _soft_break(self) -> None:
        if self._buffer:
            self._flush()

    def _hard_break(self) -> None:
        if self._buffer:
            self._flush()
        else:
            self.lines.append(self._prefix(self._depth))

    def _flush(self) -> None:
        text = "".join(self._buffer).strip()
        self._buffer = []
        if text:
            self.lines.append(self._prefix(self._line_depth) + text)

    @staticmethod
    def _prefix(depth: int) -> str:
        return "> " * depth

    def text(self) -> str:
        self._soft_break()
        return "\n".join(self.lines)


def html_to_text(html: str) -> str:
    """Wandelt HTML in Text; Zitate werden mit ``>`` markiert, Tabellenzellen mit Tab."""
    parser = _HtmlText()
    parser.feed(html[: MAIL.max_html_bytes])
    parser.close()
    return parser.text()


class LineKind(StrEnum):
    """Art einer normalisierten Zeile."""

    BODY = "body"
    BLANK = "blank"
    SIGNATURE = "signature"
    FORWARD_MARKER = "forward_marker"
    FORWARD_HEADER = "forward_header"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class Line:
    """Eine nummerierte Zeile des normalisierten Texts."""

    no: int
    text: str
    kind: LineKind
    quote_depth: int = 0

    @property
    def is_content(self) -> bool:
        """True für Zeilen, die Parser lesen dürfen."""
        return self.kind in (LineKind.BODY, LineKind.SIGNATURE)

    def ref(self) -> SourceRef:
        """Fundstelle dieser Zeile."""
        return SourceRef(self.no, self.no, self.text[:MAX_EXCERPT])


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalisierter Mailtext mit Weiterleitungsinformationen."""

    lines: tuple[Line, ...]
    forwarded: bool = False
    original_sender: str = ""
    original_sender_name: str = ""
    truncated: bool = False

    def content(self) -> tuple[Line, ...]:
        """Inhalts- und Leerzeilen, ohne Verlauf, Kopfzeilen und Markierungen."""
        return tuple(line for line in self.lines if line.is_content or line.kind is LineKind.BLANK)

    def body_text(self) -> str:
        """Nur Bestelltext, etwa für den Inhaltsfingerabdruck."""
        return "\n".join(line.text for line in self.lines if line.kind is LineKind.BODY)

    def render(self) -> str:
        """Gesamter normalisierter Text; Zeile n entspricht ``Mailzeile n``."""
        return "\n".join(line.text for line in self.lines)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_LIKE.sub(" ", text)
    return _CONTROL.sub("", text)


def _unquote(raw: str) -> tuple[int, str]:
    match = _QUOTE_PREFIX.match(raw)
    if match is None:
        return 0, raw.strip()
    return match.group(1).count(">"), raw[match.end() :].strip()


def _collapse_blanks(pairs: list[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    previous_blank = True
    for depth, text in pairs:
        blank = not text
        if blank and previous_blank:
            continue
        previous_blank = blank
        yield depth, text


@dataclass
class _State:
    forwarded: bool = False
    in_history: bool = False
    in_signature: bool = False
    expect_headers: bool = False
    sender: str = ""
    sender_name: str = ""


def _marker_kind(text: str, subject: str, state: _State) -> LineKind | None:
    outlook = any(marker.match(text) for marker in _OUTLOOK_MARKERS)
    if any(marker.match(text) for marker in _FORWARD_MARKERS) or (
        outlook and _FORWARD_SUBJECT.match(subject)
    ):
        state.forwarded, state.in_history, state.in_signature = True, False, False
        state.expect_headers = True
        return LineKind.FORWARD_MARKER
    if outlook or _REPLY_INTRO.match(text):
        state.in_history = True
        return LineKind.HISTORY
    return None


def _header_kind(text: str, state: _State) -> LineKind | None:
    if not state.expect_headers:
        return None
    header = _HEADER_LINE.match(text)
    if header is None:
        state.expect_headers = False
        return None
    if header.group("name").casefold() in ("von", "from"):
        name, address = parseaddr(header.group("value"))
        state.sender, state.sender_name = address.casefold(), name
    return LineKind.FORWARD_HEADER


def _classify(depth: int, text: str, subject: str, state: _State) -> LineKind:
    if not text:
        return LineKind.BLANK
    special = _marker_kind(text, subject, state) or _header_kind(text, state)
    if special is not None:
        return special
    if state.in_history or (depth > 0 and not state.forwarded):
        return LineKind.HISTORY
    if text in ("--", "-- "):
        state.in_signature = True
    if state.in_signature or _MOBILE_SIGNATURE.match(text):
        return LineKind.SIGNATURE
    return LineKind.BODY


def normalize(text: str, subject: str = "") -> NormalizedText:
    """Bereinigt Zeichen, entfernt Zitatzeichen und ordnet jede Zeile einer Art zu."""
    cleaned = _clean(text[: PARSING.max_text_chars])
    raw_lines = [line[: PARSING.max_line_chars] for line in cleaned.split("\n")]
    too_long = len(text) > PARSING.max_text_chars or any(
        len(line) > PARSING.max_line_chars for line in cleaned.split("\n")
    )
    truncated = too_long or len(raw_lines) > MAX_LINES
    pairs = list(_collapse_blanks([_unquote(raw) for raw in raw_lines[:MAX_LINES]]))
    while pairs and not pairs[-1][1]:
        pairs.pop()
    state = _State()
    lines = []
    for index, (depth, content) in enumerate(pairs, start=1):
        kind = _classify(depth, content, subject, state)
        lines.append(Line(index, content, kind, depth))
    return NormalizedText(
        lines=tuple(lines),
        forwarded=state.forwarded,
        original_sender=state.sender,
        original_sender_name=state.sender_name,
        truncated=truncated,
    )
