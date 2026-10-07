from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from icware_auftragsimport.domain.errors import InstanceLockedError
from icware_auftragsimport.infrastructure.lock import InstanceLock

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    script = f"import sys; sys.path.insert(0, {SRC!r})\n" + textwrap.dedent(code)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )


def test_second_process_is_refused(tmp_path: Path) -> None:
    lock_file = tmp_path / "instance.lock"
    with InstanceLock(lock_file):
        result = _run(
            f"""
            from pathlib import Path
            from icware_auftragsimport.infrastructure.lock import InstanceLock
            from icware_auftragsimport.domain.errors import InstanceLockedError
            try:
                InstanceLock(Path({str(lock_file)!r})).acquire()
                print("ERWORBEN")
            except InstanceLockedError as exc:
                print(exc.code)
            """
        )
    assert result.stdout.strip() == "INSTANCE_LOCKED"


def test_lock_is_released_when_holder_crashes(tmp_path: Path) -> None:
    lock_file = tmp_path / "instance.lock"
    result = _run(
        f"""
        import os
        from pathlib import Path
        from icware_auftragsimport.infrastructure.lock import InstanceLock
        InstanceLock(Path({str(lock_file)!r})).acquire()
        os._exit(3)
        """
    )
    assert result.returncode == 3
    with InstanceLock(lock_file):
        pass


def test_same_process_second_lock_is_refused(tmp_path: Path) -> None:
    lock_file = tmp_path / "instance.lock"
    first = InstanceLock(lock_file)
    first.acquire()
    try:
        if sys.platform != "win32":
            with pytest.raises(InstanceLockedError):
                InstanceLock(lock_file).acquire()
    finally:
        first.release()
    first.release()
