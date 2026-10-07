"""Bereinigung nicht vertrauenswürdiger Texte für Anzeige, Protokoll und Dateinamen.

Entfernt Steuerzeichen, unsichtbare Formatzeichen (etwa Bidi-Override U+202E, das
„rechnung\\u202efdp.exe“ als „rechnungexe.pdf“ erscheinen lässt) und Surrogate.
"""

from __future__ import annotations

import unicodedata

_STRIP = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def clean_untrusted(text: str, *, max_chars: int, keep_newlines: bool = False) -> str:
    """Bereinigter, gekürzter Text; Zeilenumbrüche nur auf Wunsch erhalten."""
    out: list[str] = []
    for ch in text[: max_chars * 4]:
        if ch in "\n\t" and keep_newlines:
            out.append(ch)
        elif ch in "\r\n\t\x0b\x0c":
            out.append(" ")
        elif unicodedata.category(ch) not in _STRIP:
            out.append(ch)
    result = "".join(out)
    if not keep_newlines:
        result = " ".join(result.split())
    return result[:max_chars]


def has_hidden_characters(text: str) -> bool:
    """True bei Bidi-, Nullbreiten- oder Steuerzeichen (Hinweis auf Täuschung)."""
    return any(unicodedata.category(ch) in _STRIP and ch not in "\n\t\r" for ch in text)
