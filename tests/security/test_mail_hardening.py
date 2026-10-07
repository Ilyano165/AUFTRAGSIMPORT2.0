"""Mail-Eingang: malformed MIME, Header-Injection, Größen, Verschachtelung, Dateinamen,
Encodings, HTML und Robustheit gegen beliebige Eingaben."""

from __future__ import annotations

import base64
import contextlib
import random

import pytest
from mailfactory import nested, simple, with_attachment

from icware_auftragsimport.ingest.attachments import Verdict
from icware_auftragsimport.ingest.mime import MailRejected, ParsedMail, parse_mail
from icware_auftragsimport.parsers.text import html_to_text
from icware_auftragsimport.security.limits import MailLimits


def test_regular_mail() -> None:
    mail = parse_mail(simple())
    assert (mail.sender, mail.sender_name, mail.subject) == (
        "einkauf@kunde.de",
        "Einkauf",
        "Bestellung",
    )
    assert "YT11YBOR01" in mail.text and mail.message_id == "<abc123@kunde.de>"
    assert mail.defects == ()


@pytest.mark.parametrize(
    ("raw", "limits", "code"),
    [
        (simple("x" * 5000), MailLimits(max_message_bytes=1000), "MAIL_TOO_LARGE"),
        (
            simple(**{"X-Pad": "y" * 3000}),
            MailLimits(max_header_block_bytes=1000),
            "MAIL_HEADER_TOO_LARGE",
        ),
        (
            simple(**{f"X-H{i}": "1" for i in range(60)}),
            MailLimits(max_headers=50),
            "MAIL_TOO_MANY_HEADERS",
        ),
        (nested(15), MailLimits(), "MAIL_TOO_DEEP"),
        (nested(8), MailLimits(max_parts=5), "MAIL_TOO_MANY_PARTS"),
        (simple("\n--x" * 2000), MailLimits(), "MAIL_TOO_MANY_PARTS"),
    ],
)
def test_resource_limits(raw: bytes, limits: MailLimits, code: str) -> None:
    with pytest.raises(MailRejected) as info:
        parse_mail(raw, limits)
    assert info.value.code == code


def test_deep_nesting_does_not_hit_recursion_limit() -> None:
    with pytest.raises(MailRejected) as info:
        parse_mail(nested(400))
    assert info.value.code in ("MAIL_TOO_DEEP", "MAIL_UNPARSEABLE")


def test_header_injection_and_hidden_characters_are_neutralised() -> None:
    evil = base64.b64encode("Bestellung\r\nBcc: angreifer@evil.de\u202e".encode()).decode()
    mail = parse_mail(simple(Subject=f"=?utf-8?b?{evil}?="))
    assert "\r" not in mail.subject and "\n" not in mail.subject and "\u202e" not in mail.subject
    assert mail.subject == "Bestellung Bcc: angreifer@evil.de"


@pytest.mark.parametrize(
    "sender",
    ["a@kunde.de, b@evil.de", "", "kein-absender", "alice@example.org)<bob@example.org>"],
)
def test_ambiguous_or_malformed_sender_is_rejected(sender: str) -> None:
    mail = parse_mail(simple(From=sender))
    assert mail.sender in ("", "bob@example.org", "alice@example.org")
    if sender != "alice@example.org)<bob@example.org>":
        assert mail.sender == ""
        assert any("Absender" in d for d in mail.defects)


def test_invalid_message_id_is_replaced_deterministically() -> None:
    first = parse_mail(simple(**{"Message-ID": "<a b>\r\nX: y"}))
    second = parse_mail(simple(**{"Message-ID": "<a b>\r\nX: y"}))
    assert first.message_id == second.message_id and first.message_id.endswith("@icware.invalid>")


@pytest.mark.parametrize("charset", ["x-unknown", "rot13", "base64", "utf-7"])
def test_unknown_or_dangerous_charsets_fall_back(charset: str) -> None:
    mail = parse_mail(simple("Grüße", **{"Content-Type": f"text/plain; charset={charset}"}))
    assert "Gr" in mail.text
    assert any("Zeichensatz" in d for d in mail.defects)


def test_broken_base64_body_does_not_crash() -> None:
    raw = simple("@@@ kein base64 @@@", **{"Content-Transfer-Encoding": "base64"})
    assert isinstance(parse_mail(raw), ParsedMail)


def test_html_is_reduced_to_text() -> None:
    html = "<script>alert('x')</script><style>p{}</style><p>Bestellung 5x</p><img src='http://evil/t.gif'>"
    text = html_to_text(html)
    assert "Bestellung 5x" in text and "alert" not in text and "evil" not in text


@pytest.mark.parametrize(
    ("filename", "verdict"),
    [
        ("../../etc/passwd", Verdict.STORED),
        ("C:\\Windows\\System32\\evil.exe", Verdict.BLOCKED),
        ("rechnung\u202efdp.exe", Verdict.BLOCKED),
        ("bestellung.pdf.exe", Verdict.BLOCKED),
        ("CON.txt", Verdict.STORED),
        ("A" * 500 + ".txt", Verdict.STORED),
    ],
)
def test_malicious_filenames(filename: str, verdict: Verdict) -> None:
    [attachment] = parse_mail(with_attachment(filename, b"harmlos")).attachments
    name = attachment.report.safe_name
    assert "/" not in name and "\\" not in name and ".." not in name and len(name) <= 120
    assert not name.upper().startswith("CON.")
    assert attachment.report.verdict is verdict
    if verdict is Verdict.BLOCKED:
        assert attachment.data == b""


def test_oversized_attachment_is_not_decoded() -> None:
    raw = with_attachment("gross.pdf", b"%PDF-1.4" + b"0" * 4000)
    [attachment] = parse_mail(raw, MailLimits(max_attachment_bytes=1000)).attachments
    assert attachment.report.verdict is Verdict.BLOCKED and attachment.data == b""


def test_random_garbage_and_mutations_never_crash() -> None:
    rng = random.Random(4711)
    seeds = [simple(), with_attachment("a.pdf", b"%PDF-1.4\n%%EOF"), nested(5)]
    for _ in range(400):
        data = bytearray(rng.choice(seeds))
        for _ in range(rng.randint(1, 30)):
            position = rng.randrange(len(data))
            data[position] = rng.randrange(256)
        if rng.random() < 0.3:
            data = data[: rng.randrange(len(data))]
        try:
            result = parse_mail(bytes(data))
        except MailRejected:
            continue
        assert isinstance(result, ParsedMail)
    for size in (0, 1, 10, 1000):
        with contextlib.suppress(MailRejected):
            parse_mail(bytes(rng.randrange(256) for _ in range(size)))
