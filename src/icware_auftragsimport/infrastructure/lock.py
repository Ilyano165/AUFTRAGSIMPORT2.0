"""Sperre gegen parallele Instanzen auf demselben Datenordner."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from ..domain.errors import InstanceLockedError, UserMessage


def _try_lock(handle: BinaryIO) -> bool:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl  # noqa: PLC0415

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class InstanceLock:
    """Exklusive Sperre; wird vom Betriebssystem auch bei einem Absturz freigegeben."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Sperrt oder wirft ``InstanceLockedError``, ohne zu warten."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")  # noqa: SIM115
        if not _try_lock(handle):
            handle.close()
            raise InstanceLockedError(
                "INSTANCE_LOCKED",
                UserMessage(
                    what="Der Auftrags-Import läuft bereits",
                    why="Eine andere Instanz arbeitet mit denselben Daten, etwa eine "
                    "geplante Aufgabe oder ein zweites Fenster",
                    unchanged="Diese Instanz hat nichts abgerufen oder exportiert",
                    action="Bitte die andere Instanz beenden oder abwarten und dann neu starten",
                ),
            )
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Gibt die Sperre frei; mehrfacher Aufruf ist unschädlich."""
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
