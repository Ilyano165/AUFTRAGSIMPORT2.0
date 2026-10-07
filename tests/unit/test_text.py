from __future__ import annotations

from decimal import Decimal

import pytest

from icware_auftragsimport.parsers import text as text_module
from icware_auftragsimport.parsers.numbers import parse_decimal_de
from icware_auftragsimport.parsers.text import (
    LineKind,
    decode_payload,
    html_to_text,
    normalize,
)


@pytest.mark.parametrize(
    ("payload", "declared", "expected_charset"),
    [
        ("Grüße".encode(), "utf-8", "utf-8"),
        ("Grüße".encode("cp1252"), "unknown-8bit", "cp1252"),
        ("Grüße".encode("cp1252"), "utf-8", "cp1252"),
        ("Grüße".encode("cp1252"), None, "cp1252"),
        ("Grüße".encode("iso-8859-1"), '"iso-8859-1"', "iso8859-1"),
    ],
)
def test_decoding_never_fails(payload: bytes, declared: str | None, expected_charset: str) -> None:
    decoded = decode_payload(payload, declared)
    assert decoded.text == "Grüße"
    assert decoded.charset == expected_charset


def test_html_divs_become_single_lines_and_br_divs_blank_lines() -> None:
    html = "<div>Hallo,</div><div><br></div><div>5 x Rakel</div><div>3 x Filz</div>"
    assert html_to_text(html) == "Hallo,\n\n5 x Rakel\n3 x Filz"


def test_html_tables_scripts_entities_and_quotes() -> None:
    html = (
        "<head><title>x</title><style>p{}</style></head><script>alert(1)</script>"
        "<table><tr><td>A1</td>\n<td>3</td></tr></table>"
        "<p>M&uuml;ller &amp; Co.</p><blockquote><div>alt</div></blockquote>"
    )
    lines = html_to_text(html).split("\n")
    assert lines[0].split("\t") == ["A1 ", "3"]
    assert "Müller & Co." in lines
    assert lines[-1] == "> alt"
    assert "alert" not in html_to_text(html)


def test_control_and_zero_width_characters_are_removed() -> None:
    normalized = normalize("Rakel\u200b Gold\x00\x07\u00a0x\ufeff")
    assert normalized.lines[0].text == "Rakel Gold x"


def test_reply_history_is_excluded() -> None:
    normalized = normalize("5 x Rakel\n\nAm 28.09.2026 schrieb Lieferant:\n> 3 x Filz\n> Alt")
    kinds = [line.kind for line in normalized.lines]
    assert kinds == [
        LineKind.BODY,
        LineKind.BLANK,
        LineKind.HISTORY,
        LineKind.HISTORY,
        LineKind.HISTORY,
    ]
    assert normalized.body_text() == "5 x Rakel"


def test_iphone_forward_is_unquoted_and_original_sender_known() -> None:
    mail = (
        "Bitte erfassen\n\nAnfang der weitergeleiteten Nachricht:\n\n"
        "> Von: Thomas Müller <T.Mueller@Kunde.de>\n> Datum: 29. September 2026\n"
        "> Betreff: Bestellung\n>\n> 5 x Rakel"
    )
    normalized = normalize(mail, "WG: Bestellung")
    assert normalized.forwarded
    assert normalized.original_sender == "t.mueller@kunde.de"
    assert normalized.original_sender_name == "Thomas Müller"
    last = normalized.lines[-1]
    assert (last.text, last.kind, last.quote_depth) == ("5 x Rakel", LineKind.BODY, 1)


def test_outlook_marker_is_forward_only_with_forward_subject() -> None:
    mail = "Siehe unten\n-----Ursprüngliche Nachricht-----\nVon: a@b.de\nBetreff: x\n5 x Rakel"
    forwarded = normalize(mail, "WG: Bestellung")
    reply = normalize(mail, "AW: Bestellung")
    assert forwarded.lines[-1].kind is LineKind.BODY
    assert reply.lines[-1].kind is LineKind.HISTORY


def test_signature_lines_are_marked() -> None:
    normalized = normalize("5 x Rakel\n-- \nMuster GmbH\nVon meinem iPhone gesendet")
    assert [line.kind for line in normalized.lines] == [
        LineKind.BODY,
        LineKind.SIGNATURE,
        LineKind.SIGNATURE,
        LineKind.SIGNATURE,
    ]


def test_blank_lines_are_collapsed_and_numbered_consistently() -> None:
    normalized = normalize("\n\nA\n\n\n\nB\n\n")
    assert [(line.no, line.text) for line in normalized.lines] == [(1, "A"), (2, ""), (3, "B")]
    assert normalized.render() == "A\n\nB"


def test_very_long_mails_are_truncated_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_module, "MAX_LINES", 3)
    normalized = normalize("1\n2\n3\n4\n5")
    assert normalized.truncated and len(normalized.lines) == 3


@pytest.mark.parametrize(
    ("raw", "value", "ambiguous"),
    [
        ("5", "5", False),
        ("12,5", "12.5", False),
        ("1.234,56", "1234.56", False),
        ("1.234", "1234", True),
        ("1.000.000", "1000000", True),
        ("12.50", "12.50", True),
        ("1,234.56", "1234.56", True),
        ("8,90 €", "8.90", False),
        ("1'234.50", "1234.50", True),
    ],
)
def test_german_numbers(raw: str, value: str, ambiguous: bool) -> None:
    parsed = parse_decimal_de(raw)
    assert parsed is not None
    assert (parsed.value, parsed.ambiguous) == (Decimal(value), ambiguous)


@pytest.mark.parametrize("raw", ["", "abc", "1,2,3", "12.", "1.23.4", "1.2345,6"])
def test_invalid_numbers(raw: str) -> None:
    assert parse_decimal_de(raw) is None
