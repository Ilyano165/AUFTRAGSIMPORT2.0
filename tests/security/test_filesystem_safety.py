"""Dateisystem: Pfadangriffe, Symlinks, Systemordner, Mount-/Ordnerwechsel, Exportordner."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from icware_auftragsimport.domain.status import ExportMode
from icware_auftragsimport.infrastructure.atomic import atomic_create_in
from icware_auftragsimport.infrastructure.clock import FixedClock
from icware_auftragsimport.infrastructure.db import Database, transaction
from icware_auftragsimport.infrastructure.repositories import OrderRepository
from icware_auftragsimport.security.fs import (
    UnsafePathError,
    inspect_directory,
    safe_filename,
    safe_join,
)
from icware_auftragsimport.services.export_service import ExportResult, ExportService
from support import EXPORT_PROFILE, approved_order

posix_only = pytest.mark.skipif(os.name == "nt", reason="Symlink/Rechte-Tests für POSIX")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Windows\\evil.exe", "evil.exe"),
        ("rechnung\u202efdp.exe", "rechnungfdp.exe"),
        ("CON.pdf", "_CON.pdf"),
        ("lpt1", "_lpt1"),
        ("a<b>c:d|e?.pdf", "a_b_c_d_e_.pdf"),
        ("...", "anhang"),
        ("", "anhang"),
        (".exe", "anhang.exe"),
        ("Bestellung Nr. 5 .pdf ", "Bestellung Nr. 5.pdf"),
        (".bat", "anhang.bat"),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


@pytest.mark.parametrize(
    "name",
    [
        "..",
        "../x.xml",
        "a/b.xml",
        "a\\b.xml",
        "C:x.xml",
        "x.xml:stream",
        "NUL.xml",
        "x.xml.",
        " x.xml",
        "a\x00.xml",
        "",
    ],
)
def test_safe_join_rejects_everything_but_plain_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, name)


@posix_only
def test_directory_inspection(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    (real / "sub").mkdir()
    open_dir = tmp_path / "offen"
    open_dir.mkdir()
    open_dir.chmod(0o777)
    file = tmp_path / "datei"
    file.write_text("x")
    assert inspect_directory(str(real), purpose="Test").inode == real.stat().st_ino
    for path in ("link", "link/sub", "offen", "datei", "fehlt"):
        with pytest.raises(UnsafePathError):
            inspect_directory(str(tmp_path / path), purpose="Test")
    for path in ("relativ/pfad", "/", "/etc", sys.prefix, "//server/freigabe"):
        with pytest.raises(UnsafePathError):
            inspect_directory(path, purpose="Test")


@posix_only
def test_existing_symlink_at_target_name_is_never_followed(tmp_path: Path) -> None:
    victim = tmp_path / "opfer.txt"
    victim.write_text("original")
    export = tmp_path / "export"
    export.mkdir()
    (export / "AI-1.xml").symlink_to(victim)
    with pytest.raises(FileExistsError):
        atomic_create_in(export, "AI-1.xml", b"<x/>")
    assert victim.read_text() == "original"
    target = atomic_create_in(export, "AI-2.xml", b"<x/>")
    assert target.stat().st_mode & 0o077 == 0
    assert sorted(p.name for p in export.iterdir()) == ["AI-1.xml", "AI-2.xml"]


@posix_only
def test_replaced_or_symlinked_export_folder_blocks_production_export(tmp_path: Path) -> None:
    clock = FixedClock(datetime(2026, 9, 30, 8, 0, tzinfo=UTC))
    database = Database(tmp_path / "e.db")
    conn = database.connect()
    database.migrate(conn)
    with transaction(conn):
        for number in (1, 2, 3):
            order = approved_order(id=f"order-{number}", document_number=f"AI-2026-00000{number}")
            OrderRepository(conn).insert(order, "Test", clock.now())
    live, test = tmp_path / "lexware", tmp_path / "test"
    live.mkdir()
    test.mkdir()
    profile = replace(
        EXPORT_PROFILE,
        export_dir=str(live),
        test_export_dir=str(test),
        target_system="Lexware warenwirtschaft pro",
        target_validated_on="2026-10-15",
    )
    service = ExportService(conn, clock)
    assert service.run("order-1", profile, ExportMode.LIVE).result is ExportResult.SUCCESS
    live.rename(tmp_path / "alt")
    live.mkdir()
    blocked = service.run("order-2", profile, ExportMode.LIVE)
    assert blocked.result is ExportResult.BLOCKED and "verändert" in blocked.message
    service.reset_directory_pin(profile.id)
    assert service.run("order-2", profile, ExportMode.LIVE).result is ExportResult.SUCCESS
    live.rename(tmp_path / "alt2")
    live.symlink_to(tmp_path / "alt2", target_is_directory=True)
    symlinked = service.run("order-3", profile, ExportMode.LIVE)
    assert symlinked.result is ExportResult.BLOCKED and "Verknüpfung" in symlinked.message
