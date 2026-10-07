"""Atomares Schreiben: Ziel enthält entweder die alte oder die vollständige neue Datei."""

from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
from pathlib import Path

TEMP_PREFIX = ".icw-"
TEMP_SUFFIX = ".part"


def atomic_write_bytes(target: Path, data: bytes, *, staging_dir: Path | None = None) -> None:
    """Schreibt ``data`` über eine exklusiv angelegte Zwischendatei und benennt dann um.

    ``staging_dir`` muss auf demselben Laufwerk wie ``target`` liegen, damit das
    Umbenennen atomar bleibt. Ohne Angabe wird der Zielordner verwendet.
    """
    directory = staging_dir if staging_dir is not None else target.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=directory)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def atomic_write_text(target: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Textvariante von :func:`atomic_write_bytes`."""
    atomic_write_bytes(target, text.encode(encoding))


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish_without_replace(temp_path: Path, target: Path) -> None:
    if os.name == "nt":
        os.rename(temp_path, target)
        return
    os.link(temp_path, target)
    temp_path.unlink()


_DIR_FD_SAFE = (
    os.name != "nt"
    and os.open in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)


def _publish_at(directory_fd: int, temp_name: str, name: str) -> None:
    os.link(
        temp_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False
    )
    os.unlink(temp_name, dir_fd=directory_fd)


def _create_via_directory_handle(directory: Path, name: str, data: bytes) -> None:
    """POSIX: alle Schritte relativ zu einem geöffneten Ordner, ohne Symlinks zu folgen."""
    # O_DIRECTORY/O_NOFOLLOW gibt es nur unter POSIX; der Aufrufer prüft das (hasattr).
    # Die Typprüfung läuft auch für Windows, daher plattformneutrale Ignore-Codes.
    posix = os.O_DIRECTORY | os.O_NOFOLLOW  # type: ignore[attr-defined,unused-ignore]
    directory_fd = os.open(directory, os.O_RDONLY | posix)
    temp_name = f"{TEMP_PREFIX}{secrets.token_hex(8)}{TEMP_SUFFIX}"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW  # type: ignore[attr-defined,unused-ignore]
        fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _publish_at(directory_fd, temp_name, name)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name, dir_fd=directory_fd)
            raise
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_create_in(directory: Path, name: str, data: bytes) -> Path:
    """Legt ``name`` in ``directory`` atomar neu an; ``name`` muss ein einfacher Dateiname sein."""
    from ..security.fs import safe_join  # noqa: PLC0415

    target = safe_join(directory, name)
    if _DIR_FD_SAFE:
        _create_via_directory_handle(directory, name, data)
    else:
        atomic_create_bytes(target, data)
    return target


def atomic_create_bytes(target: Path, data: bytes) -> None:
    """Legt ``target`` atomar neu an und überschreibt nie eine vorhandene Datei.

    Ablauf: Zwischendatei im Zielordner, schreiben, flush, fsync, dann atomar
    veröffentlichen (Windows: rename, das bei vorhandenem Ziel fehlschlägt; sonst
    Hardlink). Bei jedem Fehler wird die Zwischendatei entfernt; ein vorhandenes Ziel
    führt zu ``FileExistsError``. Im Zielordner erscheint nie eine halbe ``.xml``-Datei.
    """
    directory = target.parent
    fd, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=directory)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _publish_without_replace(temp_path, target)
        _fsync_directory(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def leftover_temp_files(directory: Path) -> list[Path]:
    """Zwischendateien, die ein abgebrochener Schreibvorgang hinterlassen hat."""
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{TEMP_PREFIX}*{TEMP_SUFFIX}"))
