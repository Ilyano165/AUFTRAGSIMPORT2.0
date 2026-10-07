# IC-Ware Auftrags-Import 2.0

Herausgeber: **IC-Ware**

Der IC-Ware Auftrags-Import liest Bestellmails, erkennt Positionen, Anschriften und
Bestelldaten regelbasiert und bereitet sie geprüft für den Import in Lexware vor.
Es werden keine Daten an Dritte übertragen und keine KI-Dienste verwendet.

## Grundsätze

- **Nichts wird geraten.** Unsichere Werte werden als „prüfen“ oder „unbekannt“ markiert
  und erst nach Bestätigung exportiert.
- **Jeder Wert hat eine Herkunft.** Regel, Begründung, Sicherheit und Mailzeile.
- **Kein Auftrag wird doppelt exportiert.** Duplikatschutz über Message-ID, UID,
  Inhaltsfingerabdruck und Bestellnummer; die Datenbank erzwingt höchstens einen aktiven
  Live-Export je Auftrag.
- **Fehlermeldungen erklären** was passiert ist, warum, was unverändert blieb und was zu tun ist.

## Stand (Entwicklung)

| Iteration | Inhalt | Stand |
|---|---|---|
| 1 | Domain, Konfiguration, Anmeldedaten, Protokoll | fertig |
| 2 | Datenbank, Journal, Duplikatschutz, Nummernkreis, Instanzsperre | fertig |
| 3 | Normalisierung, Parser, Artikelzuordnung, Katalog, Validierung | fertig |
| 4 | Exportrepräsentation, LexwareExportAdapter, atomarer Export, Recovery | fertig |
| 5 | Mailabruf (IMAP, Testpostfach), Anhangsprüfung | fertig (lokal gegen Dovecot getestet; kein echter Anbieter getestet; OAuth2 offen) |
| 5b | Archivierung abgerufener Mails nach dem Export | offen |
| 6 | Oberfläche (PySide6) | fertig |
| 7 | Installer, Dokumentation, CI | vorbereitet (Signatur, EULA offen, siehe `RELEASE_CHECKLIST.md`) |

## Entwicklung

```bash
python -m pip install -e ".[dev,windows]"
python -m pytest
ruff check src tests
mypy
```

Python 3.11 oder neuer. Die Kernanwendung hat keine Laufzeitabhängigkeiten; Oberfläche,
Anmeldespeicher und Anhangsverarbeitung sind optionale Pakete.

## Aufbau

```
src/icware_auftragsimport/
  domain/          Fachmodelle, Zustände, Befunde (ohne GUI, Mail, Dateisystem)
  parsers/         regelbasierte Erkennung (Text, Positionen, Anschriften, …)
  services/        Zuordnung, Katalog, Validierung, Duplikatschutz, Nummernkreis
  infrastructure/  Datenbank, atomares Schreiben, Sperre, Protokoll
  config/          validierte Einstellungen ohne Geheimnisse
  security/        Anmeldedaten, Bereinigung sensibler Daten
  export/          Exportadapter (Iteration 4)
  app/             Anwendungsfälle (ab Iteration 4)
  gui/             Oberfläche (Iteration 6)
tests/
  unit/ integration/ regression/
```

## Lexware

Das Exportformat ist **nicht verifiziert**. Es wird keine XML-Struktur erfunden; der
LexwareExportAdapter (Iteration 4) verwendet nur Elemente aus der openTRANS-Spezifikation,
bestätigten Anforderungen und echten Referenzdateien. Alles andere ist als
Integrationsabhängigkeit markiert, siehe `docs/architektur.md`.

## Lexware-Export

**Status: Exportadapter implementiert – Zielsystemvalidierung ausstehend.** Das Produkt wird nicht als
„Lexware-kompatibel“ bezeichnet, bis der Integrationstest auf der Lexware-Version des Kunden bestanden ist.

- Spezifikation: `docs/lexware/export-spezifikation.md` (erzeugt aus der JSON-Spezifikation)
- Testplan: `docs/lexware/integrationstest.md`, Testdateien: `integration/lexware/cases/`
- Neu erzeugen: `python tools/lexware_testpaket.py`
- Produktivexport erst nach Eintrag von `target_system` und `target_validated_on` im Importprofil.

`integration/lexware/reference/248090.xml` enthält echte Firmendaten von Yellotools. Die Datei gehört
nicht in Installer oder Auslieferung an Dritte.

## Sicherheit

Jede Mail und jeder Anhang gilt als potenziell bösartig. Details: `docs/security/security-report.md`,
`docs/security/threat-model.md`, `docs/security/checklist.md`, `docs/security/dependencies.md`.
Release-Voraussetzung: CPython ≥ 3.12.6 mit expat ≥ 2.7.1 (`security/runtime.py` prüft das).

## Oberfläche

    pip install -e ".[gui]"
    python -m icware_auftragsimport --demo

Startet mit einem fiktiven Demobestand aus zwei Firmenprofilen. Gestaltung, Tastenkürzel und High-DPI-Verhalten:
`docs/gui/design.md`; Screenshots in `docs/gui/screenshots/` (neu erzeugen mit
`python tools/gui_screenshots.py`).

Firmenprofile und Artikelkatalog (Formate, Validierung, Versionen, Backup, Datentrennung):
`docs/stammdaten.md`.

## Mail-Abruf

Einstellungen → Postfächer einrichten, Postfach im Firmenprofil zuordnen, **Aufträge abrufen** (F5).
Das Postfach wird nie verändert (nichts als gelesen markiert, nichts verschoben). Ablauf,
Passwortverwaltung, Fehler und Testmodus: `docs/mail-abruf.md`. Teststatus je Anbieter:
`docs/mail-anbieter.md` (**Microsoft 365 nicht unterstützt**, OAuth2 fehlt).

    python -m icware_auftragsimport --demo --testpostfach ORDNER   # .eml-Dateien abrufen
    python tools/imap_testserver.py                                # lokaler IMAP-Testserver (Linux)

## Windows-Build und Auslieferung

    python tools/release/build.py all --release --build-number 57

Ablauf, Reproduzierbarkeit und Versionen: `docs/release/build.md`. Installation, Upgrade,
Reparatur, Deinstallation, Ablageorte und Protokolle: `docs/release/installer.md`. Code-Signatur:
`docs/release/signing.md`. Updates: `docs/release/updates.md`. Vor jedem Release:
`RELEASE_CHECKLIST.md`.
