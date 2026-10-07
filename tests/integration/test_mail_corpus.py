"""Realistischer Mailbestand über das Testpostfach (``--testpostfach``) und bekannte Mängel."""

from __future__ import annotations

from pathlib import Path

import pytest

from fetchkit import Harness
from icware_auftragsimport.app.fetching import folder_sources
from icware_auftragsimport.infrastructure.repositories import OrderRepository
from icware_auftragsimport.services.mail_fetch import CancelToken
from mailcorpus import corpus, write_corpus


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "bestand")


def _run(harness: Harness, directory: Path):  # type: ignore[no-untyped-def]
    service = harness.service(folder_sources(directory, shared=True))
    return service.run([harness.target()], CancelToken())


def test_corpus_through_test_mailbox_folder(harness: Harness, tmp_path: Path) -> None:
    directory = tmp_path / "eml"
    write_corpus(directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    summary = _run(harness, directory)
    # Dateien tragen keine IMAP-Markierungen: Die $Junk-Mail (08) wird hier zum Auftrag.
    # Gegen den echten Server ist sie Spam (siehe test_imap_dovecot).
    counts = (
        summary.checked,
        summary.orders,
        summary.spam,
        summary.auto_replies,
        summary.sender_filtered,
        summary.failed,
        summary.known,
    )
    assert counts == (16, 11, 1, 1, 1, 1, 1)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    again = _run(harness, directory)
    assert (again.orders, again.known) == (0, 13)


def test_corpus_is_deterministic() -> None:
    assert [m.raw for m in corpus()] == [m.raw for m in corpus()]
    assert len({m.name for m in corpus()}) == 16


@pytest.mark.xfail(
    strict=True,
    reason="BEKANNTER FEHLER Adresserkennung: Signatur „Person / Betrieb ohne Rechtsform“ "
    "landet vertauscht (Firma = Person). Korrektur braucht den Absendernamen im Adressparser.",
)
def test_signature_person_then_business_is_assigned_correctly(
    harness: Harness, tmp_path: Path
) -> None:
    directory = tmp_path / "eml"
    directory.mkdir()
    plain = next(m for m in corpus() if m.name == "01-plain")
    (directory / "01.eml").write_bytes(plain.raw)
    summary = _run(harness, directory)
    order = OrderRepository(harness.conn).get(summary.new_order_ids[0])
    address = order.invoice_address.value
    assert address is not None
    assert (address.company, address.name) == ("Gasthaus Lindenhof", "Martin Vogt")
