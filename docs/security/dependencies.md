# Abhängigkeiten, Sicherheit und Lizenzen

Geprüft am 2026-10-01 mit `pip-audit 2.10.1` gegen die PyPI-Schwachstellendatenbank; Lizenzen aus den PyPI-Metadaten.

## Laufzeit (Windows-Build)

| Paket | Version | Zweck | Lizenz | Audit |
|---|---|---|---|---|
| defusedxml | 0.7.1 | sicheres XML-Parsen (Pflicht) | PSF-2.0 | keine bekannte Lücke |
| keyring | 25.7.0 | Windows-Anmeldeinformationsverwaltung | MIT | keine bekannte Lücke |
| pywin32-ctypes | 0.2.3 | Windows-Backend für keyring | BSD-3-Clause | keine bekannte Lücke |
| jaraco.classes / .functools / .context | 3.4.0 / 4.6.0 / 6.1.2 | Abhängigkeiten von keyring | MIT | keine bekannte Lücke |
| more-itertools | 11.1.0 | Abhängigkeit von jaraco.functools | MIT | keine bekannte Lücke |
| PySide6 (+ Essentials, Addons, shiboken6) | 6.11.2 | Oberfläche (Iteration 6) | LGPL-3.0-only oder GPL-2.0/3.0 | keine bekannte Lücke |

Exakte Pins: `requirements/runtime-windows.txt`.

## Entwicklung

| Paket | Version | Lizenz |
|---|---|---|
| pytest / pytest-cov | 9.1.1 / 7.1.0 | MIT |
| coverage | 7.16.2 | Apache-2.0 |
| mypy | 2.3.1 | MIT |
| ruff | 0.16.9 | MIT |
| types-defusedxml | 0.7.0.20260504 | Apache-2.0 |
| pip-audit | 2.10.1 | Apache-2.0 |

## Entfernt

| Paket | Grund |
|---|---|
| pypdf | Nicht verwendet; die erlaubte Untergrenze 6.7.0 hatte 15 bekannte Lücken (behoben erst ab 6.16.1). Ein PDF-Parser für fremde Dateien ist reine Angriffsfläche, solange keine Funktion ihn braucht. |
| openpyxl | Nicht verwendet; XLSX wird strukturell mit der Standardbibliothek geprüft. |

Wird PDF- oder XLSX-Inhalt später ausgewertet, ist das Paket mit aktueller Version, Hash und den Grenzen aus `security/limits.py` aufzunehmen. Ausgewertet wird dann nur, was `inspect_attachment` als „auswertbar“ einstuft.

## Lizenzpflichten

- **PySide6 (LGPL-3.0):** Kommerzielle Einmallizenz ist zulässig, wenn die Qt-/PySide-Bibliotheken dynamisch gelinkt und austauschbar bleiben. Daraus folgt: Build als Ordner (kein „onefile“-Bündel), Lizenztexte beilegen, Hinweis auf Bezugsquelle des Quellcodes, Reverse Engineering zum Austausch der Bibliotheken nicht vertraglich verbieten. Endgültig vor Release juristisch prüfen.
- **MIT, BSD-3-Clause, Apache-2.0, PSF-2.0:** Lizenz- und Copyright-Hinweise in die Drittanbieter-Hinweise des Installers aufnehmen.
