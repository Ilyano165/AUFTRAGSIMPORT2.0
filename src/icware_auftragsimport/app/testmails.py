"""Testpostfach: realistische .eml-Dateien für Demo und Testmodus.

Die Dateien laufen über ``FolderMailSource`` durch denselben Abrufweg wie echte Mails
(Vorprüfung, MIME, Erkennung, Duplikatschutz, Speicherung). Echte Postfächer werden dabei
nie berührt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

TEST_MAILBOX = "testpostfach"


def test_mailbox_dir(data_dir: Path, account_id: str) -> Path:
    """Ordner des Testpostfachs eines Kontos."""
    return data_dir / TEST_MAILBOX / account_id


def _mail(
    sender: str,
    subject: str,
    body: str,
    when: datetime,
    message_id: str,
    *,
    html: bool = False,
    headers: dict[str, str] | None = None,
    attachment: tuple[str, bytes, str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "bestellung@muster-getraenke.example"
    message["Subject"] = subject
    message["Date"] = format_datetime(when)
    message["Message-ID"] = message_id
    for name, value in (headers or {}).items():
        message[name] = value
    if html:
        message.set_content(body, subtype="html")
    else:
        message.set_content(body)
    if attachment is not None:
        name, data, mime = attachment
        main, sub = mime.split("/", 1)
        message.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return message.as_bytes()


GASTHAUS = """Guten Morgen,

bitte liefern Sie uns zur nächsten Tour:

6 x MW-0710 Mineralwasser still 12 × 0,7 l
4 x AS-1000 Apfelschorle 12 × 1,0 l
2 x KF-1000 Kaffee Crema ganze Bohne 1 kg

Unsere Bestellnummer: GH-2026-1041
Lieferung wie gewohnt, Zahlung auf Rechnung.

Viele Grüße
Martin Vogt
Gasthaus Lindenhof
Lindenstraße 12
53783 Eitorf
"""

HOTEL = """<html><body><p>Sehr geehrte Damen und Herren,</p>
<p>wir bestellen für das Hotel:</p>
<table><tr><td>10 Kisten</td><td>MW-0720</td><td>Mineralwasser medium</td></tr>
<tr><td>3 Kisten</td><td>OS-1030</td><td>Orangensaft Direktsaft</td></tr></table>
<p>Bestellnummer: HS-0977</p>
<p>Mit freundlichen Grüßen<br>Sabine Roth<br>Hotel am Stadtpark GmbH<br>Parkallee 3<br>
53721 Siegburg</p></body></html>"""


def standard_mails(now: datetime, known: bytes | None = None) -> dict[str, bytes]:
    """Testmails für das Profil „Muster Getränke“ (Postfach ``bestellungen``)."""
    at = [now - timedelta(minutes=m) for m in range(80, 0, -10)]
    mails = {
        "01-bestellung-gasthaus.eml": _mail(
            "Martin Vogt <m.vogt@gasthaus.example>",
            "Bestellung KW 41",
            GASTHAUS,
            at[0],
            "<t-001@gasthaus.example>",
        ),
        "02-bestellung-html.eml": _mail(
            "Sabine Roth <einkauf@hotel-stadtpark.example>",
            "Bestellung Hotel am Stadtpark",
            HOTEL,
            at[1],
            "<t-002@hotel-stadtpark.example>",
            html=True,
        ),
        "03-rueckfrage.eml": _mail(
            "Lena Krämer <lena.kraemer@baeckerei.example>",
            "Kurze Frage",
            "Hallo,\n\nhaben Sie den Kaffee Crema wieder vorrätig?\n\nGruß\nLena Krämer\n",
            at[2],
            "<t-003@baeckerei.example>",
        ),
        "04-spam.eml": _mail(
            "Gewinnspiel <info@gewinn.example>",
            "Sie haben gewonnen",
            "Klicken Sie hier.",
            at[3],
            "<t-004@gewinn.example>",
            headers={"X-Spam-Flag": "YES"},
        ),
        "05-abwesend.eml": _mail(
            "Café Blum <info@cafe-blum.example>",
            "Abwesenheitsnotiz",
            "Ich bin bis zum 14.10. nicht im Büro.",
            at[4],
            "<t-005@cafe-blum.example>",
            headers={"Auto-Submitted": "auto-replied"},
        ),
        "06-turnverein.eml": _mail(
            "Kasse <kasse@turnverein.example>",
            "Einladung Vereinsfest",
            "Liebe Freunde des Vereins, ...",
            at[5],
            "<t-006@turnverein.example>",
        ),
        "07-anhang-gesperrt.eml": _mail(
            "Martin Vogt <m.vogt@gasthaus.example>",
            "Nachbestellung",
            "Bitte zusätzlich liefern:\n\n2 x SV-0500 Servietten\n\nUnsere Bestellnummer: "
            "GH-2026-1042\n\nMartin Vogt\nGasthaus Lindenhof\nLindenstraße 12\n53783 Eitorf\n",
            at[6],
            "<t-007@gasthaus.example>",
            attachment=("liste.exe", b"MZ\x90\x00 nicht ausfuehren", "application/octet-stream"),
        ),
    }
    if known is not None:
        mails["08-bereits-bekannt.eml"] = known
    return mails


def write_test_mailbox(directory: Path, mails: dict[str, bytes]) -> list[Path]:
    """Schreibt die Testmails; vorhandene Dateien bleiben unverändert."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in mails.items():
        path = directory / name
        if not path.exists():
            path.write_bytes(data)
            written.append(path)
    return written
