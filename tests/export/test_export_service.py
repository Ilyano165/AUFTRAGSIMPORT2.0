"""Export-Engine: Modi, Ordnerschutz, atomares Schreiben, Bericht und Wiederherstellung."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from icware_auftragsimport.config.schema import ImportProfile
from icware_auftragsimport.domain.status import ExportJobState, ExportMode, OrderStatus
from icware_auftragsimport.infrastructure import atomic
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database, transaction
from icware_auftragsimport.infrastructure.repositories import ExportJobRepository, OrderRepository
from icware_auftragsimport.services import export_service
from icware_auftragsimport.services.export_recovery import ExportRecovery
from icware_auftragsimport.services.export_service import ExportResult, ExportService
from support import EXPORT_PROFILE, approved_order

ORDER_ID = "order-0001"


@pytest.fixture
def conn(tmp_path: Path, clock: FixedClock) -> sqlite3.Connection:
    database = Database(tmp_path / "e.db")
    connection = database.connect()
    database.migrate(connection)
    with transaction(connection):
        OrderRepository(connection).insert(approved_order(), "Test", clock.now())
    return connection


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    live, test = tmp_path / "lexware-import", tmp_path / "lexware-test"
    live.mkdir()
    test.mkdir()
    return live, test


def _profile(dirs: tuple[Path, Path], *, validated: bool = True) -> ImportProfile:
    live, test = dirs
    return replace(
        EXPORT_PROFILE,
        export_dir=str(live),
        test_export_dir=str(test),
        target_system="Lexware warenwirtschaft pro 2026" if validated else "",
        target_validated_on="2026-10-15" if validated else "",
    )


def _service(conn: sqlite3.Connection, clock: FixedClock) -> ExportService:
    counter = iter(range(1, 1000))
    return ExportService(conn, clock, id_factory=lambda: f"job-{next(counter)}")


def _files(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_preview_persists_nothing(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    preview = _service(conn, clock).preview(approved_order(), _profile(dirs))
    assert preview.xml is not None and "<ORDER_LIST>" in preview.xml
    assert preview.validation.errors() == ()
    assert ExportJobRepository(conn).for_order(ORDER_ID) == []
    assert _files(dirs[0]) == _files(dirs[1]) == []


def test_dry_run_records_report_but_writes_no_file(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    report = _service(conn, clock).run(ORDER_ID, _profile(dirs), ExportMode.DRY_RUN)
    assert report.result is ExportResult.SUCCESS and report.export_path is None
    [job] = ExportJobRepository(conn).for_order(ORDER_ID)
    assert (job.mode, job.state) == (ExportMode.DRY_RUN, ExportJobState.DONE)
    assert _files(dirs[0]) == _files(dirs[1]) == []


def test_test_export_only_touches_test_folder(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    service = _service(conn, clock)
    report = service.run(ORDER_ID, _profile(dirs, validated=False), ExportMode.TEST)
    assert report.result is ExportResult.SUCCESS
    assert _files(dirs[0]) == []
    assert _files(dirs[1]) == ["TEST_AI-2026-000123.xml"]
    again = service.run(ORDER_ID, _profile(dirs, validated=False), ExportMode.TEST)
    assert again.result is ExportResult.BLOCKED and "existiert bereits" in again.message
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.APPROVED


@pytest.mark.parametrize("layout", ["missing", "same", "inside", "parent"])
def test_test_export_never_uses_the_lexware_folder(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path], layout: str
) -> None:
    live, _ = dirs
    test_dir = {
        "missing": "",
        "same": str(live),
        "inside": str(live / "test"),
        "parent": str(live.parent),
    }[layout]
    (live / "test").mkdir()
    profile = replace(_profile(dirs), test_export_dir=test_dir)
    report = _service(conn, clock).run(ORDER_ID, profile, ExportMode.TEST)
    assert report.result is ExportResult.BLOCKED
    assert _files(live) == ["test"] and _files(live / "test") == []


def test_production_export_is_locked_until_target_validation(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    report = _service(conn, clock).run(ORDER_ID, _profile(dirs, validated=False), ExportMode.LIVE)
    assert report.result is ExportResult.BLOCKED
    assert "Zielsystemvalidierung ausstehend" in report.message
    assert _files(dirs[0]) == []


def test_production_export_writes_once_and_reports_everything(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    service = _service(conn, clock)
    report = service.run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    target = dirs[0] / "AI-2026-000123.xml"
    assert report.result is ExportResult.SUCCESS
    assert _files(dirs[0]) == ["AI-2026-000123.xml"]
    assert report.xml_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert (report.order_id, report.order_reference, report.export_path) == (
        ORDER_ID,
        "PO-4711",
        str(target),
    )
    assert (report.adapter_id, report.adapter_version, report.spec_version) == (
        "lexware_opentrans",
        "1.0.0",
        "1.0.0",
    )
    assert report.exported_at == "2026-09-30T08:00:00+00:00"
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.EXPORTED
    [job] = ExportJobRepository(conn).for_order(ORDER_ID)
    assert (job.state, job.result, job.report["xml_sha256"]) == (
        ExportJobState.DONE,
        "success",
        report.xml_sha256,
    )
    second = service.run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    assert second.result is ExportResult.BLOCKED
    assert _files(dirs[0]) == ["AI-2026-000123.xml"]


def test_unapproved_order_is_not_exported(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    orders = OrderRepository(conn)
    with transaction(conn):
        orders.save(
            replace(orders.get(ORDER_ID), status=OrderStatus.READY, revision=2), "x", clock.now()
        )
    report = _service(conn, clock).run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    assert report.result is ExportResult.BLOCKED and "nicht freigegeben" in report.message


def test_existing_target_file_is_never_overwritten(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    target = dirs[0] / "AI-2026-000123.xml"
    target.write_bytes(b"alt")
    report = _service(conn, clock).run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    assert report.result is ExportResult.FAILED and "existiert bereits" in report.message
    assert target.read_bytes() == b"alt"
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.FAILED


def test_write_failure_leaves_no_partial_file(
    conn: sqlite3.Connection,
    clock: FixedClock,
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_publish(temp: Path, target: Path) -> None:
        raise OSError(28, "Kein Speicherplatz")

    monkeypatch.setattr(atomic, "_publish_without_replace", broken_publish)
    monkeypatch.setattr(atomic, "_publish_at", lambda *args: broken_publish(Path(), Path()))
    report = _service(conn, clock).run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    assert report.result is ExportResult.FAILED
    assert (
        "Kein Speicherplatz" in report.message and "Zwischendatei wurde entfernt" in report.message
    )
    assert _files(dirs[0]) == []


def test_blocked_export_explains_and_records(
    conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path]
) -> None:
    profile = replace(_profile(dirs), supplier=replace(EXPORT_PROFILE.supplier, country=""))
    report = _service(conn, clock).run(ORDER_ID, profile, ExportMode.DRY_RUN)
    assert report.result is ExportResult.BLOCKED
    assert any("Land des Lieferanten" in f for f in report.findings)
    assert "Es wurde keine Datei geschrieben" in report.message


def _crash_after_writing(conn: sqlite3.Connection, clock: FixedClock, dirs: tuple[Path, Path],
                         monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    """Simuliert einen Absturz nach dem Schreiben, bevor der Erfolg gespeichert wird."""

    def crash(*args: object, **kwargs: object) -> None:
        raise SystemExit("Absturz")

    monkeypatch.setattr(ExportService, "_finalize", crash)
    with pytest.raises(SystemExit):
        _service(conn, clock).run(ORDER_ID, _profile(dirs), ExportMode.LIVE)
    monkeypatch.undo()


def test_recovery_completes_export_when_file_is_intact(
    conn: sqlite3.Connection,
    clock: FixedClock,
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _crash_after_writing(conn, clock, dirs, monkeypatch)
    [job] = ExportJobRepository(conn).for_order(ORDER_ID)
    assert job.state is ExportJobState.WRITING
    [action] = ExportRecovery(conn, clock).run()
    assert action.new_state is ExportJobState.DONE
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.EXPORTED


def test_recovery_marks_altered_file_unclear(
    conn: sqlite3.Connection,
    clock: FixedClock,
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _crash_after_writing(conn, clock, dirs, monkeypatch)
    (dirs[0] / "AI-2026-000123.xml").write_bytes(b"veraendert")
    [action] = ExportRecovery(conn, clock).run()
    assert action.new_state is ExportJobState.UNCLEAR
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.UNCLEAR


def test_recovery_never_reexports_missing_file(
    conn: sqlite3.Connection,
    clock: FixedClock,
    dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _crash_after_writing(conn, clock, dirs, monkeypatch)
    (dirs[0] / "AI-2026-000123.xml").unlink()
    (dirs[0] / ".icw-abc.part").write_bytes(b"rest")
    [action] = ExportRecovery(conn, clock).run()
    assert action.new_state is ExportJobState.FAILED
    assert OrderRepository(conn).get(ORDER_ID).status is OrderStatus.FAILED
    assert _files(dirs[0]) == []


def test_atomic_create_never_replaces(tmp_path: Path) -> None:
    target = tmp_path / "a.xml"
    atomic.atomic_create_bytes(target, b"eins")
    with pytest.raises(FileExistsError):
        atomic.atomic_create_bytes(target, b"zwei")
    assert target.read_bytes() == b"eins"
    assert sorted(os.listdir(tmp_path)) == ["a.xml"]


def test_separation_rules(tmp_path: Path) -> None:
    live = tmp_path / "live"
    assert export_service.separated(live, tmp_path / "test")
    assert not export_service.separated(live, live)
    assert not export_service.separated(live, live / "sub")
    assert not export_service.separated(live, tmp_path)
