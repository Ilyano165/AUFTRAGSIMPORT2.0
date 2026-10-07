"""Abrufdienst: Mail → Auftrag mit echter Datei-Datenbank und Attrappen-Postfach."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fetchkit import (
    FakeMessage,
    FakeSource,
    Harness,
    build_mail,
    captured_logs,
    database_error,
    imap_error,
    messages,
    order_text,
)
from icware_auftragsimport.app.fetching import imap_sources
from icware_auftragsimport.app.workbench import Workbench
from icware_auftragsimport.config.schema import AuthMethod, SenderAction, SenderRule
from icware_auftragsimport.domain.status import OrderStatus
from icware_auftragsimport.ingest.imap import ImapErrorKind
from icware_auftragsimport.ingest.mime import ParsedMail
from icware_auftragsimport.security.credentials import MemoryCredentialStore
from icware_auftragsimport.security.limits import MailLimits
from icware_auftragsimport.services import mail_fetch
from icware_auftragsimport.services.mail_fetch import (
    CancelToken,
    FetchErrorKind,
    FetchFailure,
    FetchProgress,
    FetchStage,
    FetchTarget,
    fetch_target,
    select_body,
)


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path / "bestand")


def new_orders(h: Harness) -> int:
    return h.count("orders", "mail_id IN (SELECT id FROM mails WHERE uidvalidity != 1)")


# ---------- Erfolg ----------


def test_order_mail_becomes_order_in_one_transaction(harness: Harness) -> None:
    source = FakeSource(messages(build_mail()))
    summary = harness.run(source)
    assert summary.ok and (summary.checked, summary.orders) == (1, 1)
    [order_id] = summary.new_order_ids
    order = Workbench(harness.conn, harness.settings, harness.clock).snapshot(order_id).order
    assert order.profile_id == "standard" and len(order.lines) == 2
    assert order.customer_reference.value == "GH-1"
    assert order.status in (OrderStatus.READY, OrderStatus.NEEDS_REVIEW)
    assert harness.mail_states() == [(1, "extracted")]
    assert (
        harness.count(
            "idempotency_keys", "mail_id = (SELECT mail_id FROM orders WHERE id = ?)", order_id
        )
        == 3
    )
    [payload] = harness.conn.execute(
        "SELECT detail FROM journal WHERE event = 'mail_fetched'"
    ).fetchone()
    assert "gasthaus" not in payload and "Bestellung" not in payload
    assert set(json.loads(payload)) == {"state", "account", "uid", "order"}
    assert source.closed == 1


def test_empty_mailbox(harness: Harness) -> None:
    summary = harness.run(FakeSource({}))
    assert summary.ok and summary.checked == 0 and summary.lines() == ["0 Nachrichten geprüft"]


def test_multiple_mails_in_uid_order(harness: Harness) -> None:
    raws = [build_mail(order_text(f"GH-{i}"), message_id=f"<m{i}@x>") for i in range(1, 4)]
    source = FakeSource(messages(*raws))
    summary = harness.run(source)
    assert summary.orders == 3 and source.fetched == [1, 2, 3]
    assert new_orders(harness) == 3


def test_fetch_never_changes_flags_or_moves_mails(harness: Harness) -> None:
    source = FakeSource(messages(build_mail()))
    harness.run(source)
    assert not hasattr(source, "store") and source.messages[1].flags == frozenset()


# ---------- Duplikate ----------


def test_rerun_skips_known_uids_without_download(harness: Harness) -> None:
    source = FakeSource(messages(build_mail(), build_mail(order_text("GH-2"), message_id="<m2@x>")))
    harness.run(source)
    source.fetched.clear()
    again = harness.run(source)
    assert (again.checked, again.known, again.orders) == (2, 2, 0)
    assert source.fetched == [] and new_orders(harness) == 2


def test_new_uidvalidity_does_not_duplicate_orders(harness: Harness) -> None:
    raw = build_mail()
    harness.run(FakeSource(messages(raw)))
    rebuilt = FakeSource(messages(raw), uidvalidity=99)
    summary = harness.run(rebuilt)
    assert (summary.orders, summary.known) == (0, 1)
    assert new_orders(harness) == 1
    assert harness.mail_states() == [(1, "extracted"), (1, "duplicate")]


def test_same_message_id_in_other_folder_is_known(harness: Harness) -> None:
    raw = build_mail()
    harness.run(FakeSource(messages(raw)))
    moved = FakeSource(messages(raw), folder="Bestellungen/2026")
    assert harness.run(moved).known == 1 and new_orders(harness) == 1


def test_same_content_new_message_id_is_flagged_not_dropped(harness: Harness) -> None:
    harness.run(FakeSource(messages(build_mail(message_id="<a@x>"))))
    again = harness.run(FakeSource(messages(build_mail(message_id="<b@x>")), uidvalidity=8))
    assert (again.orders, again.possible_duplicates, again.review) == (1, 1, 1)
    snapshot = Workbench(harness.conn, harness.settings, harness.clock).snapshot(
        again.new_order_ids[0]
    )
    assert "DUPLICATE_MAIL_CONTENT" in {f.code for f in snapshot.validation.findings}
    assert snapshot.order.status is OrderStatus.NEEDS_REVIEW


def test_known_reference_is_reported_on_new_order(harness: Harness) -> None:
    harness.run(FakeSource(messages(build_mail(order_text("GH-77"), message_id="<a@x>"))))
    second = build_mail(order_text("GH-77") + "\nNachtrag: bitte früh liefern", message_id="<b@x>")
    summary = harness.run(FakeSource(messages(second), uidvalidity=8))
    snapshot = Workbench(harness.conn, harness.settings, harness.clock).snapshot(
        summary.new_order_ids[0]
    )
    assert "DUPLICATE_REFERENCE" in {f.code for f in snapshot.validation.findings}


# ---------- Vorprüfung ----------


@pytest.mark.parametrize(
    ("headers", "flags"),
    [
        ({"X-Spam-Flag": "YES"}, frozenset()),
        ({"X-Spam-Status": "Yes, score=9.1"}, frozenset()),
        ({"X-Forefront-Antispam-Report": "CIP:1.2.3.4;SFV:SPM;SCL:9"}, frozenset()),
        ({}, frozenset({"$Junk"})),
    ],
)
def test_spam_is_skipped_without_download(
    harness: Harness, headers: dict[str, str], flags: frozenset[str]
) -> None:
    source = FakeSource({1: FakeMessage(build_mail(headers=headers), flags)})
    summary = harness.run(source)
    assert (summary.spam, summary.orders) == (1, 0) and source.fetched == []
    assert harness.mail_states() == []


def test_not_junk_marking_overrides_spam_header(harness: Harness) -> None:
    source = FakeSource(
        {1: FakeMessage(build_mail(headers={"X-Spam-Flag": "YES"}), frozenset({"$NotJunk"}))}
    )
    assert harness.run(source).orders == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Auto-Submitted": "auto-replied"},
        {"X-Autoreply": "yes"},
    ],
)
def test_auto_replies_are_skipped(harness: Harness, headers: dict[str, str]) -> None:
    summary = harness.run(FakeSource(messages(build_mail("Bin im Urlaub", headers=headers))))
    assert (summary.auto_replies, summary.orders) == (1, 0)


def test_bounce_from_mailer_daemon_is_skipped(harness: Harness) -> None:
    raw = build_mail("Delivery failed", sender="MAILER-DAEMON@mx.example")
    assert harness.run(FakeSource(messages(raw))).auto_replies == 1


def test_webshop_notifications_are_not_treated_as_auto_replies(harness: Harness) -> None:
    raw = build_mail(headers={"Auto-Submitted": "auto-generated", "Precedence": "bulk"})
    assert harness.run(FakeSource(messages(raw))).orders == 1


def test_profile_rule_ignore_filters_sender(harness: Harness) -> None:
    raw = build_mail(sender="kasse@turnverein.example")
    source = FakeSource(messages(raw))
    summary = harness.run(source)
    assert (summary.sender_filtered, summary.orders) == (1, 0) and source.fetched == []


def test_account_allowlist_filters_other_senders(harness: Harness) -> None:
    account = replace(harness.account, allowed_senders=("@hotel.example",))
    source = FakeSource(
        messages(build_mail(), build_mail(sender="a@hotel.example", message_id="<h@x>"))
    )
    summary = harness.run(source, target=harness.target(account=account))
    assert (summary.sender_filtered, summary.orders) == (1, 1) and source.fetched == [2]


def test_review_rule_forces_review_status(harness: Harness) -> None:
    raw = build_mail(sender="lena.kraemer@baeckerei.example")
    summary = harness.run(FakeSource(messages(raw)))
    assert summary.review == 1


def test_profile_default_ignore_drops_unknown_senders(harness: Harness) -> None:
    target = harness.target(
        sender_default=SenderAction.IGNORE, sender_rules=(SenderRule("@hotel.example"),)
    )
    summary = harness.run(FakeSource(messages(build_mail())), target=target)
    assert summary.sender_filtered == 1


def test_oversized_mail_is_not_downloaded(harness: Harness) -> None:
    source = FakeSource({1: FakeMessage(build_mail(), size=30 * 1024 * 1024)})
    summary = harness.run(source)
    assert (summary.oversized, summary.orders) == (1, 0) and source.fetched == []


def test_mail_larger_than_announced_is_stopped_at_limit(harness: Harness) -> None:
    raw = build_mail(order_text() + "x" * 5000)
    source = FakeSource({1: FakeMessage(raw, size=100)})
    summary = harness.run(source, limits=MailLimits(max_message_bytes=2000))
    assert (summary.oversized, summary.orders) == (1, 0)


def test_limit_per_run_defers_the_rest(harness: Harness) -> None:
    raws = [build_mail(order_text(f"GH-{i}"), message_id=f"<m{i}@x>") for i in range(1, 6)]
    account = replace(harness.account, max_messages_per_run=2)
    summary = harness.run(FakeSource(messages(*raws)), target=harness.target(account=account))
    assert (summary.orders, summary.deferred, summary.checked) == (2, 3, 2)
    assert "3 weitere beim nächsten Abruf" in summary.lines()


# ---------- Fehler einzelner Mails ----------


def test_unreadable_mail_is_stored_failed_and_run_continues(harness: Harness) -> None:
    broken = build_mail("\n--x" * 1200, message_id="<kaputt@x>")
    source = FakeSource(messages(broken, build_mail(message_id="<ok@x>")))
    summary = harness.run(source)
    assert (summary.failed, summary.orders) == (1, 1) and summary.ok
    assert [p.kind for p in summary.problems] == [FetchErrorKind.MESSAGE]
    assert harness.mail_states() == [(1, "failed"), (2, "extracted")]
    row = harness.conn.execute(
        "SELECT error, raw FROM mails WHERE uid = 1 AND uidvalidity = 7"
    ).fetchone()
    assert row["error"] == "MAIL_TOO_MANY_PARTS" and row["raw"]
    assert harness.run(source).known == 2


def test_message_the_server_cannot_deliver_is_reported(harness: Harness) -> None:
    source = FakeSource(
        messages(build_mail(), build_mail(message_id="<b@x>", body=order_text("GH-9"))),
        fail_fetch={1: imap_error(ImapErrorKind.PROTOCOL)},
    )
    summary = harness.run(source)
    assert (summary.failed, summary.orders) == (1, 1) and summary.ok
    assert harness.mail_states() == [(2, "extracted")]


def test_blocked_attachment_is_reported_order_still_created(harness: Harness) -> None:
    raw = build_mail(attachment=("liste.exe", b"MZ\x90\x00", "application/octet-stream"))
    summary = harness.run(FakeSource(messages(raw)))
    assert summary.orders == 1
    assert [(p.kind, p.uid) for p in summary.problems] == [(FetchErrorKind.ATTACHMENT, 1)]


def test_parser_crash_is_contained(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object) -> None:
        raise RuntimeError("Fehler in der Erkennung")

    monkeypatch.setattr(mail_fetch.ExtractionEngine, "extract", explode)
    summary = harness.run(FakeSource(messages(build_mail())))
    assert summary.ok and (summary.failed, summary.orders) == (1, 0)
    assert [p.kind for p in summary.problems] == [FetchErrorKind.PARSER]
    assert harness.mail_states() == [(1, "failed")]


# ---------- Globale Fehler ----------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ImapErrorKind.TIMEOUT, FetchErrorKind.TIMEOUT),
        (ImapErrorKind.AUTHENTICATION, FetchErrorKind.AUTHENTICATION),
        (ImapErrorKind.TLS, FetchErrorKind.TLS),
        (ImapErrorKind.MAILBOX, FetchErrorKind.MAILBOX),
        (ImapErrorKind.NETWORK, FetchErrorKind.CONNECTION),
        (ImapErrorKind.PROTOCOL, FetchErrorKind.SERVER),
    ],
)
def test_connection_level_errors_end_the_run_cleanly(
    harness: Harness, kind: ImapErrorKind, expected: FetchErrorKind
) -> None:
    source = FakeSource(messages(build_mail()), fail={"open": imap_error(kind)})
    summary = harness.run(source)
    assert summary.failure == FetchFailure(expected, "bestellungen")
    assert not summary.ok and summary.orders == 0 and source.closed == 1
    assert summary.failure.action and "imaplib" not in summary.failure.what


def test_connection_lost_mid_run_keeps_completed_orders(harness: Harness) -> None:
    raws = [build_mail(order_text(f"GH-{i}"), message_id=f"<m{i}@x>") for i in range(1, 4)]
    source = FakeSource(messages(*raws), fail_fetch={2: imap_error(ImapErrorKind.NETWORK)})
    summary = harness.run(source)
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.CONNECTION
    assert summary.orders == 1 and harness.mail_states() == [(1, "extracted")]
    retry = harness.run(FakeSource(messages(*raws)))
    assert (retry.known, retry.orders) == (1, 2)


def test_database_error_rolls_back_and_ends_run(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys_before = harness.count("idempotency_keys")

    def broken(*_args: object, **_kwargs: object) -> None:
        raise database_error()

    monkeypatch.setattr(mail_fetch.OrderRepository, "insert", broken)
    source = FakeSource(messages(build_mail(), build_mail(message_id="<b@x>")))
    summary = harness.run(source)
    assert summary.failure is not None and summary.failure.kind is FetchErrorKind.DATABASE
    assert harness.mail_states() == [] and harness.count("idempotency_keys") == keys_before
    assert source.fetched == [1]


def test_missing_password_is_a_credentials_failure(harness: Harness) -> None:
    service = harness.service(imap_sources(MemoryCredentialStore()))
    summary = service.run([harness.target()], CancelToken())
    assert summary.failure == FetchFailure(FetchErrorKind.CREDENTIALS, "bestellungen")


def test_fetch_target_rejects_unusable_accounts(harness: Harness) -> None:
    oauth = replace(harness.account, auth_method=AuthMethod.OAUTH2)
    settings = harness.settings_with(
        accounts=tuple(oauth if a.id == oauth.id else a for a in harness.settings.accounts)
    )
    assert fetch_target(settings) == FetchFailure(FetchErrorKind.UNSUPPORTED_AUTH, "bestellungen")
    disabled = replace(harness.account, enabled=False)
    settings = harness.settings_with(
        accounts=tuple(disabled if a.id == disabled.id else a for a in harness.settings.accounts)
    )
    assert fetch_target(settings) == FetchFailure(FetchErrorKind.CONFIGURATION, "bestellungen")
    without = harness.settings_with(
        profiles=tuple(replace(p, mail_account_id="") for p in harness.settings.profiles)
    )
    result = fetch_target(without)
    assert isinstance(result, FetchFailure) and result.kind is FetchErrorKind.CONFIGURATION
    assert isinstance(fetch_target(harness.settings), FetchTarget)


# ---------- Abbruch und Fortschritt ----------


def test_cancel_between_mails_leaves_no_partial_data(harness: Harness) -> None:
    token = CancelToken()
    raws = [build_mail(order_text(f"GH-{i}"), message_id=f"<m{i}@x>") for i in range(1, 4)]

    def cancel_on_second(uid: int) -> None:
        if uid == 2:
            token.cancel()

    source = FakeSource(messages(*raws), on_fetch=cancel_on_second)
    summary = harness.run(source, token=token)
    assert summary.cancelled and not summary.ok and summary.failure is None
    assert summary.orders == 1 and harness.mail_states() == [(1, "extracted")]
    assert source.fetched == [1, 2] and source.closed == 1


def test_cancel_token_interrupts_waiting() -> None:
    token = CancelToken()
    token.cancel()
    with pytest.raises(mail_fetch.FetchCancelled):
        token.sleep(30)


def test_progress_reports_counts_without_content(harness: Harness) -> None:
    events: list[FetchProgress] = []
    raws = [
        build_mail(order_text(f"GH-{i}"), subject="Geheimprojekt", message_id=f"<m{i}@x>")
        for i in (1, 2)
    ]
    harness.run(FakeSource(messages(*raws)), progress=events.append)
    stages = [e.stage for e in events]
    assert stages[0] is FetchStage.CONNECTING and stages[-1] is FetchStage.DONE
    assert [(e.current, e.total) for e in events if e.stage is FetchStage.LOADING] == [
        (1, 2),
        (2, 2),
    ]
    assert not any("Geheimprojekt" in e.text or "gasthaus" in e.text for e in events)


def test_logs_contain_no_mail_content_or_sender(harness: Harness) -> None:
    raws = [
        build_mail(subject="Geheimprojekt Phoenix", message_id="<a@x>"),
        build_mail(
            headers={"X-Spam-Flag": "YES"}, subject="Geheimprojekt Spam", message_id="<b@x>"
        ),
        build_mail("\n--x" * 1200, subject="Geheimprojekt kaputt", message_id="<c@x>"),
    ]
    with captured_logs() as lines:
        harness.run(FakeSource(messages(*raws)))
    text = "\n".join(lines)
    assert lines and "Abruf beendet" in text
    for secret in ("Geheimprojekt", "m.vogt", "gasthaus", "Mineralwasser", "Lindenstraße"):
        assert secret not in text


# ---------- Textauswahl ----------


def _parsed(text: str, html: str) -> ParsedMail:
    return ParsedMail("h", "<m@x>", "a@b.de", "", "", None, text, html, (), (), False)


def test_select_body_prefers_text_part() -> None:
    assert select_body(_parsed("10 x Wasser", "10 x Wasser")) == "10 x Wasser"


def test_select_body_uses_html_when_text_is_a_placeholder() -> None:
    html = "Bestellung\n" + "10 x MW-0710 Mineralwasser still\n" * 5
    assert select_body(_parsed("Bitte HTML-Ansicht verwenden.", html)) == html.strip()
    assert select_body(_parsed("", html)) == html.strip()
