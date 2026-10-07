"""Erzeugt packaging/windows/AuftragsImport.ico aus dem Markenzeichen (alle Windows-Größen)."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def main() -> None:
    """Rendert jede Größe einzeln (scharf statt skaliert) und schreibt eine ICO-Datei."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import QBuffer, QIODevice  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    from icware_auftragsimport.gui.main_window import brand_pixmap  # noqa: PLC0415

    _app = QApplication([])
    images = []
    for size in SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        brand_pixmap(size).toImage().save(buffer, "PNG")
        images.append(bytes(buffer.data()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in zip(SIZES, images, strict=True):
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset + len(payload)
        )
        payload += data
    target = ROOT / "packaging" / "windows" / "AuftragsImport.ico"
    target.write_bytes(header + entries + payload)
    print(f"{target.relative_to(ROOT)}: {len(SIZES)} Größen, {target.stat().st_size} Byte")


if __name__ == "__main__":
    main()
