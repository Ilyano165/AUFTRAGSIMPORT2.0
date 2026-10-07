from __future__ import annotations

import pytest

from icware_auftragsimport.domain.errors import TransitionError, UserMessage
from icware_auftragsimport.domain.findings import Finding, Severity, ValidationResult
from icware_auftragsimport.domain.models import COMPANY_OR_NAME, Address
from icware_auftragsimport.domain.provenance import (
    Confidence,
    Evidence,
    Field,
    FieldState,
    SourceRef,
)
from icware_auftragsimport.domain.status import OrderStatus, can_transition, ensure_transition


def test_user_message_has_all_four_parts_in_order() -> None:
    message = UserMessage(
        what="Export wurde abgebrochen",
        why="Die E-Mail konnte nicht archiviert werden",
        unchanged="Die XML-Datei wurde deshalb nicht erzeugt",
        action="Prüfen Sie die IMAP-Verbindung",
    )
    assert message.render() == (
        "Export wurde abgebrochen. Die E-Mail konnte nicht archiviert werden. "
        "Die XML-Datei wurde deshalb nicht erzeugt. Prüfen Sie die IMAP-Verbindung."
    )


def test_uncertain_evidence_always_needs_review() -> None:
    evidence = Evidence("x", "Vermutung", Confidence.UNCERTAIN, SourceRef(3, 3, "text"))
    field: Field[str] = Field.found("wert", evidence)
    assert field.state is FieldState.NEEDS_REVIEW
    assert field.needs_attention
    assert evidence.describe() == "Vermutung (Mailzeile 3)"


def test_likely_evidence_counts_as_recognized() -> None:
    field: Field[str] = Field.found("wert", Evidence("x", "Regel", Confidence.LIKELY))
    assert field.state is FieldState.RECOGNIZED
    assert not field.needs_attention


def test_unknown_and_manual_fields() -> None:
    unknown: Field[str] = Field.unknown("nicht gefunden")
    manual: Field[str] = Field.manual("gesetzt")
    assert unknown.needs_attention and not unknown.has_value
    assert manual.state is FieldState.MANUAL and not manual.needs_attention


def test_source_ref_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError):
        SourceRef(0, 1)
    with pytest.raises(ValueError):
        SourceRef(5, 4)
    assert SourceRef(18, 20).label == "Mailzeilen 18–20"


def test_company_only_address_is_incomplete() -> None:
    address = Address(company="Nur Firma GmbH")
    assert address.missing_fields() == ("street", "house_number", "postal_code", "city", "country")


def test_address_without_company_or_name_reports_it_first() -> None:
    address = Address(
        street="Weg", house_number="1", postal_code="50667", city="Köln", country="DE"
    )
    assert address.missing_fields() == (COMPANY_OR_NAME,)


@pytest.mark.parametrize(
    ("country", "postal_code", "ok"),
    [
        ("DE", "50667", True),
        ("DE", "5066", False),
        ("AT", "6020", True),
        ("AT", "60200", False),
        ("NL", "1234 AB", True),
        ("PL", "00-950", True),
        ("SE", "anything", True),
    ],
)
def test_postal_code_rules(country: str, postal_code: str, ok: bool) -> None:
    address = Address(postal_code=postal_code, country=country)
    assert (address.postal_code_problem() is None) is ok


def test_po_box_detection() -> None:
    assert Address(street="Postfach", house_number="12 34").is_po_box()
    assert not Address(street="Postweg", house_number="3").is_po_box()


def test_exported_is_terminal_and_bad_transition_explains_itself() -> None:
    assert not any(can_transition(OrderStatus.EXPORTED, target) for target in OrderStatus)
    with pytest.raises(TransitionError) as info:
        ensure_transition(OrderStatus.NEW, OrderStatus.EXPORTED)
    text = str(info.value)
    assert "„Neu“" in text and "nicht verändert" in text


def test_approved_order_can_start_export() -> None:
    ensure_transition(OrderStatus.APPROVED, OrderStatus.EXPORTING)


def test_acknowledged_findings_only_release_acknowledgeable_errors() -> None:
    duplicate = Finding("DUPLICATE", Severity.ERROR, "Duplikat?", "customer_reference", "", True)
    hard = Finding("NO_LINES", Severity.ERROR, "Keine Positionen")
    result = ValidationResult((duplicate, hard))
    ack = frozenset({duplicate.key, hard.key})
    assert result.errors(ack) == (hard,)
    assert not result.is_exportable(ack)
    assert ValidationResult((duplicate,)).is_exportable(ack)
