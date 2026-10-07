"""Realistischer, reproduzierbarer Mailbestand für IMAP-Integrationstests und Testmodus.

Jede Mail hat feste Daten (Datum, Message-ID, Anhänge) und ein erwartetes Ergebnis. Der
Bestand wird in einen echten IMAP-Server (Dovecot) geladen, kann aber auch als Ordner mit
.eml-Dateien geschrieben werden (``write_corpus``) und läuft dann über ``--testpostfach``.

Erwartete Ergebnisse: ``order`` (Auftrag angelegt), ``spam``, ``auto_reply``, ``filtered``
(Absenderfilter), ``failed`` (nicht lesbar, gespeichert), ``known`` (gleiche Message-ID).
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

DATE = "Fri, 02 Oct 2026 08:{minute:02d}:00 +0200"
TO = "bestellung@muster-getraenke.example"


@dataclass(frozen=True)
class CorpusMail:
    """Eine Testmail mit erwartetem Ergebnis und optionalen IMAP-Markierungen."""

    name: str
    raw: bytes
    expect: str
    flags: tuple[str, ...] = ()
    note: str = ""


def _message(minute: int, sender: str, subject: str, message_id: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = TO
    message["Subject"] = subject
    message["Date"] = DATE.format(minute=minute)
    message["Message-ID"] = message_id
    return message


def _fixed_boundaries(message: EmailMessage, prefix: str) -> bytes:
    """Feste MIME-Grenzen statt zufälliger: gleiche Bytes bei jedem Aufruf."""
    for number, part in enumerate(p for p in message.walk() if p.is_multipart()):
        part.set_boundary(f"=_grenze_{prefix}_{number}")
    return message.as_bytes()


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


PLAIN = """Guten Morgen,

bitte liefern Sie zur nächsten Tour:

6 x MW-0710 Mineralwasser still 12 × 0,7 l
4 x AS-1000 Apfelschorle 12 × 1,0 l

Unsere Bestellnummer: GH-2026-2001
Zahlung auf Rechnung.

Viele Grüße
Martin Vogt
Gasthaus Lindenhof
Lindenstraße 12
53783 Eitorf
"""

HTML_ONLY = """<html><body><p>Sehr geehrte Damen und Herren,</p>
<p>wir bestellen für das Hotel:</p>
<table border="1"><tr><th>Menge</th><th>Artikel</th><th>Bezeichnung</th></tr>
<tr><td>10 Kisten</td><td>MW-0720</td><td>Mineralwasser medium</td></tr>
<tr><td>3 Kisten</td><td>OS-1030</td><td>Orangensaft Direktsaft</td></tr></table>
<p>Bestellnummer: HS-0977</p>
<p>Mit freundlichen Grüßen<br>Sabine Roth<br>Hotel am Stadtpark GmbH<br>Parkallee 3<br>
53721 Siegburg</p></body></html>"""

ALT_PLAIN = """Hallo,

für die Weinbar bitte:

2 x KF-1000 Kaffee Crema ganze Bohne 1 kg
12 x MW-0710 Mineralwasser still 12 × 0,7 l

Bestellnummer WB-118

Gruß
Jonas Berg
Weinbar Berg
Marktplatz 4
53773 Hennef
"""

IPHONE = """From: Emre Yilmaz <emre.yilmaz@icloud.com>
Content-Type: text/plain;
\tcharset=utf-8
Content-Transfer-Encoding: quoted-printable
Mime-Version: 1.0 (1.0)
Subject: Bestellung
Message-Id: <5E1C0A44-9D2B-4C11-8F0E-1D2A3B4C5D6E@icloud.com>
Date: {date}
To: {to}
X-Mailer: iPhone Mail (21G93)

Hallo, bitte f=C3=BCr Samstag liefern:

10 x MW-0710 Wasser still
5 x AS-1000 Apfelschorle

Kiosk Yilmaz
Hauptstra=C3=9Fe 5
45127 Essen

Von meinem iPhone gesendet=
"""

TWO_ADDRESSES = """Guten Tag,

bitte liefern:

8 x MW-0720 Mineralwasser medium 12 × 0,7 l
2 x SV-0500 Servietten

Bestellnummer: RB-5521

Rechnungsanschrift:
Hotel Rheinblick GmbH
Uferstraße 1
53604 Bad Honnef

Lieferanschrift:
Hotel Rheinblick Restaurant
Rheinweg 7
53604 Bad Honnef

Mit freundlichen Grüßen
Petra Lange
"""

UMLAUTS = """Grüß Gott,

für unser Café bitte:

3 x KF-1000 Kaffee Crema ganze Bohne 1 kg
6 x AS-1000 Apfelschorle 12 × 1,0 l

Bestellnummer: CM-2026-12
Bitte Anlieferung über den Hof (Tür „Süd“).

Mit freundlichen Grüßen
Jürgen Müller
Café Müller & Söhne
Königstraße 9
50668 Köln
"""

UNUSUAL = """From: Bistro Nord <info@bistro-nord.example>
To: {to}
Subject: =?x-unbekannt?Q?Bestellung_Bistro?=
Date: {date}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="GRENZE"

Dies ist eine MIME-Nachricht.
--GRENZE
Content-Type: text/plain; charset="x-unbekannt-8bit"
Content-Transfer-Encoding: 8bit

Bitte liefern:

3 x MW-0710 Mineralwasser still 12 × 0,7 l

Bistro Nord
Nordring 2
45127 Essen
"""

AUTO_REPLY = """From: Café Blum <info@cafe-blum.example>
To: {to}
Subject: Abwesenheitsnotiz: Bestellung
Date: {date}
Message-ID: <abwesend-1@cafe-blum.example>
Auto-Submitted: auto-replied
X-Autoreply: yes
Content-Type: text/plain; charset=utf-8

Ich bin bis zum 14.10. nicht im Büro.
"""


def _minimal_pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _minimal_xlsx() -> bytes:
    """Gültige, makrofreie Arbeitsmappe mit festen Zeitstempeln (reproduzierbar)."""
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats'
            '.org/package/2006/content-types"><Default Extension="rels" ContentType="application'
            '/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type='
            '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="xl/workbook.xml"/></Relationships>'
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.'
            'openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats'
            '.org/officeDocument/2006/relationships"><sheets><sheet name="Bestellung" '
            'sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type='
            '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": (
            '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.'
            'openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" '
            't="inlineStr"><is><t>Artikel</t></is></c><c r="B1" t="inlineStr"><is><t>Menge'
            '</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>MW-0710</t></is>'
            '</c><c r="B2"><v>4</v></c></row></sheetData></worksheet>'
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 2, 8, 0, 0))
            archive.writestr(info, text)
    return buffer.getvalue()


def corpus() -> list[CorpusMail]:
    """Alle Testmails in Ablagereihenfolge (UID 1, 2, …)."""
    mails: list[CorpusMail] = []

    plain = _message(
        1, "Martin Vogt <m.vogt@gasthaus.example>", "Bestellung KW 41", "<k01@gasthaus.example>"
    )
    plain.set_content(PLAIN)
    mails.append(CorpusMail("01-plain", plain.as_bytes(), "order"))

    html = _message(
        2,
        "Sabine Roth <einkauf@hotel-stadtpark.example>",
        "Bestellung Hotel",
        "<k02@hotel-stadtpark.example>",
    )
    html.set_content(HTML_ONLY, subtype="html")
    mails.append(CorpusMail("02-html", html.as_bytes(), "order"))

    alternative = _message(
        3,
        "Jonas Berg <jonas@weinbar-berg.example>",
        "Bestellung Weinbar",
        "<k03@weinbar-berg.example>",
    )
    alternative.set_content(ALT_PLAIN)
    alternative.add_alternative(
        "<html><body><p>" + ALT_PLAIN.replace("\n", "<br>\n") + "</p></body></html>", subtype="html"
    )
    mails.append(
        CorpusMail("03-multipart-alternative", _fixed_boundaries(alternative, "k03"), "order")
    )

    mails.append(
        CorpusMail("04-iphone", _crlf(IPHONE.format(date=DATE.format(minute=4), to=TO)), "order")
    )

    pdf = _message(
        5,
        "Martin Vogt <m.vogt@gasthaus.example>",
        "Bestellung mit Lieferschein",
        "<k05@gasthaus.example>",
    )
    pdf.set_content(PLAIN.replace("GH-2026-2001", "GH-2026-2005"))
    pdf.add_attachment(
        _minimal_pdf(), maintype="application", subtype="pdf", filename="Bestellung.pdf"
    )
    mails.append(CorpusMail("05-pdf", _fixed_boundaries(pdf, "k05"), "order"))

    sheet = _message(
        6,
        "Sabine Roth <einkauf@hotel-stadtpark.example>",
        "Bestellliste",
        "<k06@hotel-stadtpark.example>",
    )
    sheet.set_content(
        "Bestellung anbei, zusätzlich:\n\n2 x MW-0710 Mineralwasser still\n\n"
        "Sabine Roth\nHotel am Stadtpark GmbH\nParkallee 3\n53721 Siegburg\n"
    )
    sheet.add_attachment(
        _minimal_xlsx(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="Bestellung.xlsx",
    )
    sheet.add_attachment(
        b"Artikel;Menge\r\nMW-0710;4\r\n", maintype="text", subtype="csv", filename="Bestellung.csv"
    )
    mails.append(CorpusMail("06-xlsx-csv", _fixed_boundaries(sheet, "k06"), "order"))

    spam = _message(
        7, "Gewinnspiel <info@gewinn.example>", "Sie haben gewonnen", "<k07@gewinn.example>"
    )
    spam["X-Spam-Flag"] = "YES"
    spam["X-Spam-Status"] = "Yes, score=14.2 required=5.0"
    spam.set_content("Klicken Sie hier.")
    mails.append(CorpusMail("07-spam-header", spam.as_bytes(), "spam"))

    junk = _message(8, "Angebote <news@angebot.example>", "Nur heute", "<k08@angebot.example>")
    junk.set_content("Rabatt!")
    mails.append(CorpusMail("08-spam-junk-flag", junk.as_bytes(), "spam", flags=("$Junk",)))

    mails.append(
        CorpusMail(
            "09-auto-reply",
            _crlf(AUTO_REPLY.format(date=DATE.format(minute=9), to=TO)),
            "auto_reply",
        )
    )

    unknown = _message(
        10, "Anna Neu <anfrage@neukunde.example>", "Erstbestellung", "<k10@neukunde.example>"
    )
    unknown.set_content(
        "Hallo,\n\nwir möchten bestellen:\n\n5 x MW-0710 Mineralwasser still\n\n"
        "Anna Neu\nNeukunde GmbH\nNeustraße 1\n10115 Berlin\n"
    )
    mails.append(
        CorpusMail(
            "10-unbekannter-absender", unknown.as_bytes(), "order", note="Profilstandard: annehmen"
        )
    )

    filtered = _message(
        11, "Kasse <kasse@turnverein.example>", "Einladung Vereinsfest", "<k11@turnverein.example>"
    )
    filtered.set_content("Liebe Freunde des Vereins ...")
    mails.append(CorpusMail("11-absenderfilter", filtered.as_bytes(), "filtered"))

    mails.append(
        CorpusMail(
            "12-ungewoehnliches-mime",
            _crlf(UNUSUAL.format(date=DATE.format(minute=12), to=TO)),
            "order",
            note="unbekannter Zeichensatz, fehlende Schlussgrenze, keine Message-ID",
        )
    )

    broken = _message(13, "Defekt <defekt@kunde.example>", "Kaputt", "<k13@kunde.example>")
    broken.set_content("\n--x" * 1200)
    mails.append(
        CorpusMail("13-beschaedigt", broken.as_bytes(), "failed", note="zu viele MIME-Grenzen")
    )

    two = _message(
        14,
        "Petra Lange <p.lange@hotel-rheinblick.example>",
        "Bestellung Rheinblick",
        "<k14@hotel-rheinblick.example>",
    )
    two.set_content(TWO_ADDRESSES)
    mails.append(CorpusMail("14-zwei-adressen", two.as_bytes(), "order"))

    umlaut = EmailMessage()
    umlaut["From"] = "Jürgen Müller <j.mueller@cafe-mueller.example>"
    umlaut["To"] = TO
    umlaut["Subject"] = "Bestellung für Café Müller & Söhne"
    umlaut["Date"] = DATE.format(minute=15)
    umlaut["Message-ID"] = "<k15@cafe-mueller.example>"
    umlaut.set_content(UMLAUTS, charset="windows-1252", cte="quoted-printable")
    mails.append(CorpusMail("15-umlaute", umlaut.as_bytes(), "order"))

    mails.append(
        CorpusMail("16-gleiche-message-id", plain.as_bytes(), "known", note="Kopie von 01")
    )
    return mails


def expected(kind: str) -> list[str]:
    """Namen der Mails mit diesem erwarteten Ergebnis."""
    return [m.name for m in corpus() if m.expect == kind]


def write_corpus(directory: Path) -> list[Path]:
    """Schreibt den Bestand als .eml-Dateien (für ``--testpostfach``)."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for mail in corpus():
        path = directory / f"{mail.name}.eml"
        path.write_bytes(mail.raw)
        paths.append(path)
    return paths
