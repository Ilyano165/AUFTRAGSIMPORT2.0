"""Erzeugt Testmails, auch bösartige."""

from __future__ import annotations

import base64

HEADERS = {
    "From": "Einkauf <einkauf@kunde.de>",
    "To": "auftrag@lieferant.de",
    "Subject": "Bestellung",
    "Message-ID": "<abc123@kunde.de>",
    "Date": "Tue, 29 Sep 2026 10:00:00 +0200",
    "MIME-Version": "1.0",
}


def simple(body: str = "Bitte liefern: 5 x YT11YBOR01", **headers: str) -> bytes:
    merged = {**HEADERS, "Content-Type": 'text/plain; charset="utf-8"', **headers}
    head = "".join(f"{k}: {v}\r\n" for k, v in merged.items() if v is not None)
    return head.encode() + b"\r\n" + body.encode()


def with_attachment(
    filename: str, data: bytes, content_type: str = "application/octet-stream"
) -> bytes:
    head = "".join(f"{k}: {v}\r\n" for k, v in HEADERS.items())
    encoded = base64.encodebytes(data).decode()
    return (
        head + 'Content-Type: multipart/mixed; boundary="XYZ"\r\n\r\n'
        "--XYZ\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSiehe Anhang\r\n"
        f"--XYZ\r\nContent-Type: {content_type}\r\n"
        f'Content-Disposition: attachment; filename="{filename}"\r\n'
        f"Content-Transfer-Encoding: base64\r\n\r\n{encoded}\r\n--XYZ--\r\n"
    ).encode()


def nested(depth: int) -> bytes:
    inner = b"Content-Type: text/plain\r\n\r\nx\r\n"
    for level in range(depth):
        boundary = f"b{level}".encode()
        inner = (
            b'Content-Type: multipart/mixed; boundary="'
            + boundary
            + b'"\r\n\r\n--'
            + boundary
            + b"\r\n"
            + inner
            + b"\r\n--"
            + boundary
            + b"--\r\n"
        )
    return b"From: a@kunde.de\r\nSubject: x\r\nMIME-Version: 1.0\r\n" + inner
