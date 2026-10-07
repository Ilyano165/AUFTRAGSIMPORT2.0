"""Support-Diagnosebericht ohne Bestell- und Personendaten.

Enthält nur Versionen, Laufzeitprüfung, Zählwerte je Zustand und eine strukturelle
Zusammenfassung der Einstellungen. Ausgeschlossen sind Mailtexte, Betreffs, Absender,
Anschriften, Namen, Bestell- und Belegnummern, Artikelnummern, Dateinamen, Pfade,
Hostnamen, Benutzernamen und Protokollzeilen.
"""

from __future__ import annotations

import platform
import sqlite3
from datetime import datetime

from ..branding import PRODUCT_NAME, VERSION
from ..config.schema import Settings
from ..security.runtime import RuntimeReport, check_runtime

REPORT_VERSION = 1
_COUNTED = (("orders", "status"), ("mails", "state"), ("journal", "event"))
EXCLUDED = (
    "Mailtexte, Betreffs, Absender, Anschriften, Namen, Bestell-, Beleg- und Artikelnummern, "
    "Dateinamen, Pfade, Hostnamen, Benutzernamen und Protokollzeilen sind nicht enthalten"
)


def _counts(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    if (table, column) not in _COUNTED:
        raise ValueError("Tabelle nicht freigegeben")
    rows = conn.execute(
        f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column} ORDER BY {column}"
    )
    return {str(value): int(count) for value, count in rows}


def _export_jobs(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT mode, state, result, COUNT(*) FROM export_jobs GROUP BY mode, state, result "
        "ORDER BY mode, state, result"
    )
    return [{"mode": m, "state": s, "result": r, "count": int(c)} for m, s, r, c in rows]


def _settings_summary(settings: Settings) -> dict[str, object]:
    return {
        "accounts": [
            {
                "enabled": a.enabled,
                "port": a.port,
                "tls_mode": a.tls_mode.value,
                "auth_method": a.auth_method.value,
                "credential_source": a.credential_source.value,
                "allowed_sender_rules": len(a.allowed_senders),
            }
            for a in settings.accounts
        ],
        "profiles": [
            {
                "price_mode": p.price_mode.value,
                "export_encoding": p.export_encoding,
                "export_dir_configured": bool(p.export_dir),
                "test_export_dir_configured": bool(p.test_export_dir),
                "export_dir_network_allowed": p.export_dir_network_allowed,
                "target_validated": p.target_validated,
            }
            for p in settings.profiles
        ],
    }


def build_support_report(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    now: datetime,
    runtime: RuntimeReport | None = None,
) -> dict[str, object]:
    """Diagnosebericht als Dictionary (für JSON)."""
    runtime = runtime or check_runtime()
    return {
        "report_version": REPORT_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "product": {"name": PRODUCT_NAME, "version": VERSION},
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "runtime": {
            "python": runtime.python,
            "expat": runtime.expat,
            "openssl": runtime.openssl,
            "secure": runtime.secure,
            "problems": list(runtime.problems),
        },
        "database": {
            "schema_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "orders_by_status": _counts(conn, "orders", "status"),
            "mails_by_state": _counts(conn, "mails", "state"),
            "export_jobs": _export_jobs(conn),
            "journal_events": _counts(conn, "journal", "event"),
        },
        "settings": _settings_summary(settings),
        "excluded": EXCLUDED,
    }
