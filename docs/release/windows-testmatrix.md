# Windows-Testmatrix und Testprotokoll

Stand: 2. Oktober 2026, Version 2.0.0 (unveröffentlicht).

**Bisher wurde kein einziger Windows-Test durchgeführt.** Entwicklung und alle automatischen
Tests liefen unter Linux. Unter Linux wurde der Build-Prozess statisch geprüft und das Programm
einmal *zur Analyse* eingefroren; das ist kein Windows-Build und ersetzt keinen Windows-Test.

## Statuswerte

| Status | Bedeutung |
|---|---|
| TESTED | auf echtem Windows durchgeführt, Ergebnis mit Datum, Build und Prüfer dokumentiert |
| NOT TESTED | noch nicht durchgeführt |
| WINDOWS REQUIRED | nur auf echtem Windows prüfbar |
| UNSUPPORTED | in dieser Version nicht vorgesehen |
| RELEASE BLOCKER | ohne Erledigung kein kommerzielles Release |

## Matrix

| Bereich | Status | Vorbereitet / unter Linux geprüft (kein Windows-Nachweis) | Blocker |
|---|---|---|---|
| Windows-Build (PyInstaller + MSI) | NOT TESTED · WINDOWS REQUIRED | Spec und WiX statisch geprüft; Linux-Analyse-Build eingefroren, Produktprüfung sauber | RELEASE BLOCKER |
| GitHub-Workflow `windows-release.yml` | NOT TESTED · nie gelaufen | actionlint 1.7.12 ohne Befund; Tests für Aufbau, Ein-Befehl-Regel, SHA-Festlegung, Test/Release-Trennung | RELEASE BLOCKER |
| `build-windows.ps1` | NOT TESTED ON WINDOWS | PowerShell-7.5.11-Parser: keine Syntaxfehler; Abbruch auf Nicht-Windows geprüft | – |
| Abhängigkeiten (Sperrdateien) | NOT TESTED · WINDOWS REQUIRED (Installation) | alle Windows-Wheels (win_amd64, cp314) beider Sperrdateien von PyPI geladen, jede Prüfsumme stimmt (15 Build-, 42 Werkzeug-Pakete) | – |
| PyInstaller-Paketinhalt | NOT TESTED · WINDOWS REQUIRED | Linux-Analyse: keine Tests/Secrets/DB/Build-Pfade; `setuptools` ausgeschlossen; Produktprüfung `audit` im Build | – |
| MSI | NOT TESTED · WINDOWS REQUIRED | WiX statisch geprüft (Befund W1) | RELEASE BLOCKER |
| Clean-Machine-Installation | NOT TESTED · WINDOWS REQUIRED | Protokoll unten | RELEASE BLOCKER |
| Start, keine Konsole, Version | NOT TESTED · WINDOWS REQUIRED | `console=False`; Selbsttest prüft Version im Build | RELEASE BLOCKER |
| Credential Manager | NOT TESTED · WINDOWS REQUIRED | PyInstaller-Hook sammelt keyring-Backends und Metadaten; Selbsttest prüft speichern/lesen/löschen; Windows-Test `test_windows_credential_manager_round_trip` übersprungen | RELEASE BLOCKER |
| IMAP aus installierter Version | NOT TESTED · WINDOWS REQUIRED | gegen Dovecot unter Linux getestet; Testserver für Windows-VM vorbereitet | RELEASE BLOCKER |
| TLS (Windows-Zertifikatsspeicher) | NOT TESTED · WINDOWS REQUIRED | Selbsttest zählt Stammzertifikate; TLS-Fehlerfälle unter Linux getestet | RELEASE BLOCKER |
| Datenbank, Ablageorte, Schreibrechte | NOT TESTED · WINDOWS REQUIRED | Ablageorte in `installer.md`; Pfadlogik unter Linux getestet | RELEASE BLOCKER |
| Upgrade | NOT TESTED · WINDOWS REQUIRED | keine ältere veröffentlichte MSI vorhanden (siehe Phase 7) | RELEASE BLOCKER |
| Repair | NOT TESTED · WINDOWS REQUIRED | – | – |
| Uninstall | NOT TESTED · WINDOWS REQUIRED | Policy in `installer.md` | RELEASE BLOCKER |
| Fehlerdialog / Fehlerbericht | NOT TESTED · WINDOWS REQUIRED | `--fehlertest`; Bereinigung des Berichts unter Linux getestet | – |
| Oberfläche (Windows, High-DPI) | NOT TESTED · WINDOWS REQUIRED | offscreen unter Linux getestet | – |
| Code Signing | **CODE SIGNING: NOT CONFIGURED** · RELEASE BLOCKER | Pipeline vorbereitet (Variablen/Secrets, `signing.md`); Release-Lauf bricht ohne Einrichtung ab | RELEASE BLOCKER |
| SmartScreen | NOT TESTED · WINDOWS REQUIRED | ohne Signatur ist eine Warnung zu erwarten | RELEASE BLOCKER |
| Windows Defender | NOT TESTED · WINDOWS REQUIRED | onedir, kein UPX (weniger Fehlalarme) | – |
| Windows 8.1 | UNSUPPORTED | Befund W1 | – |

## Befunde der statischen Prüfung (Phase 1)

| Nr. | Befund | Stand |
|---|---|---|
| P1 | `setuptools` (~150 Module, u. a. MSVC-Compiler-Anbindung) kam über PyInstallers Laufzeit-Hook ins Produkt | behoben: in der Spec ausgeschlossen; Linux-Analyse-Build danach mit vollem Selbsttest lauffähig |
| P2 | Der Build prüfte das fertige Paket nicht auf Entwicklungsdateien | behoben: Schritt `audit` (`tools/release/audit.py`) bricht den Build ab |
| P3 | Der Selbsttest prüfte weder Anmeldespeicher noch TLS noch Mail-Abruf | behoben: drei neue Prüfungen; unter Windows ist ein fehlender Anmeldespeicher ein Fehler |
| P4 | Kein Weg, den Fehlerdialog im installierten Programm gezielt auszulösen | behoben: `--fehlertest` |
| W1 | `VersionNT >= 603` lässt auch Windows 8.1 zu (Windows meldet dem Installer für 8.1, 10 und 11 denselben Wert); Python 3.14 läuft dort nicht | offen; WiX-Änderung erst mit Windows-Build prüfbar. Vorschlag: Registry-Suche `CurrentMajorVersionNumber` (HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion) ≥ 10 als Bedingung |
| W2 | Release-Laufzeit braucht expat ≥ 2.7.1 | laut Quellcode erfüllt: CPython `v3.14.4` liefert expat 2.7.5 mit (`Modules/expat/expat.h`), der Windows-Build übersetzt diese Fassung (`PCbuild/pyexpat.vcxproj`). Bestätigung durch den Selbsttest „Laufzeit“ im Windows-Build steht aus |
| W3 | Entwicklerpfade in Fehlerberichten | Linux-Analyse: alle 611 Code-Objekte tragen relative Dateinamen; `audit` prüft das bei jedem Build |
| W4 | Lizenztext `packaging/wix/License.rtf` ist Platzhalter | offen; Release-Build bricht ab (gewollt) |
| L1 | `tools/release/lock.py` wertete Extras nie aus: `filelock` (über `CacheControl[filecache]`, gebraucht von pip-audit) fehlte in `ci-tools.lock` | behoben, mit Tests; `ci-tools.lock` neu erzeugt; `build-windows.lock` war nicht betroffen (Neuerzeugung identisch) |
| W5 | Der ausgelieferte Workflow war ungültiges YAML (`--only-binary=:all: -r` ungequotet); GitHub hätte keinen Lauf gestartet | behoben; actionlint ohne Befund; Test erkennt die Fehlerklasse |
| W6 | Schritte mit mehreren Befehlen: PowerShell wertet nur den letzten Exit-Code aus, frühere Fehler gingen verloren | behoben: ein Befehl je Schritt, Test prüft das |
| W7 | Workflow baute immer mit `--release` (mit Platzhalter-Lizenz nie lauffähig); keine Lint-/Format-/mypy-Schritte, keine Produktprüfung, keine MSI-Prüfung; Attestierung hätte private Repositories ohne Enterprise Cloud rot gemacht | behoben: `build_type` test/release, alle Prüfungen, `verify`, Attestierung nur öffentlich oder per Variable |
| L2 | Werkzeuge der Pipeline und Entwicklungsabhängigkeiten liefen auseinander (`mypy` 2.3.1 ↔ 2.4.0, `ruff` 0.16.9 ↔ 0.16.10); `types-defusedxml` fehlte in der Pipeline, `mypy --strict` wäre dort gescheitert | behoben: eine Versionsliste, Test prüft Gleichstand von `pyproject.toml`, `ci-tools.in` und `ci-tools.lock` |

## Testprotokoll für echtes Windows

Für jeden Lauf festhalten: Windows-Edition, Version und Build (`winver`), Architektur
(`systeminfo`), physisch oder VM (Typ), Build-Nummer und SHA-256 der MSI (aus dem Manifest),
Datum, Prüfer. Ergebnis je Schritt: bestanden / nicht bestanden mit Beleg (Screenshot, Logdatei).

### Phase 2 – Release-Build

Auf einer Windows-Build-Maschine **oder** über `.github/workflows/windows-release.yml`
(GitHub-Runner: frische Windows-Server-VM je Lauf; Windows Server, kein Windows 10/11).

1. Repository frisch klonen (kein Kopieren einer Entwicklerarbeitskopie).
2. Python 3.14.4 von python.org (64 Bit) installieren, Version mit `py -3.14 --version` belegen.
3. `py -3.14 -m venv .venv` und
   `.venv\Scripts\python -m pip install --require-hashes -r requirements\build-windows.lock`.
4. WiX 5 installieren (`docs/release/build.md`), Version festhalten.
5. `.venv\Scripts\python tools\release\build.py all --build-number <N>`
   (mit `--release` erst, wenn EULA und Signatur vorhanden sind).
6. Bestanden, wenn: `audit` meldet keine Entwicklungsdateien; `build\selftest.txt` hat nur
   „OK“-Zeilen, insbesondere **Laufzeit**, **Anmeldespeicher (WinVaultKeyring)**,
   **TLS-Zertifikatsspeicher**, **Mail-Abruf (Testpostfach)**; MSI und Manifest liegen in `dist`.

### Phase 3 – Clean-Machine-Installation

Frische Windows-11-VM (zusätzlich Windows 10 22H2, da zugelassen), Snapshot vor der Installation,
IC-Ware nie installiert. Als Standardbenutzer anmelden.

1. MSI per Doppelklick starten; UAC-Abfrage erscheint; Installation ohne Fehler.
   Zusätzlich protokolliert: `msiexec /i AuftragsImport-2.0.0.msi /l*v install.log`.
2. Programm liegt in `C:\Program Files\IC-Ware\Auftrags-Import`; Startmenü-Eintrag
   „IC-Ware Auftrags-Import“ vorhanden; keine Desktop-Verknüpfung (nicht vorgesehen).
3. Start über das Startmenü: kein Konsolenfenster, keine Meldung über fehlende DLLs,
   Taskleiste zeigt ein Symbol (nicht zwei).
4. F1 bzw. Klick auf die Versionsangabe unten rechts zeigt Version und Build;
   `AuftragsImport.exe --version` ebenso.
5. `AuftragsImport.exe --self-test --output %TEMP%\selbsttest.txt`: nur „OK“-Zeilen.

### Phase 4 – Erststart und Ablageorte

1. Einstellungen → Postfächer: Postfach anlegen, Passwort eingeben, speichern.
2. `cmdkey /list` zeigt einen Eintrag „IC-Ware/Auftrags-Import/<Kennung>“ (erwartet; Format
   unter Windows unbestätigt).
3. Programm schließen, neu starten: Seite zeigt „Passwort ist gespeichert“, Verbindungstest geht.
4. Passwort nicht im Klartext: `findstr /S /I "<Passwort>" "%APPDATA%\IC-Ware\*" "%LOCALAPPDATA%\IC-Ware\*"` findet nichts.
5. Ablageorte wie in `installer.md`: `settings.json` in `%APPDATA%\IC-Ware\Auftrags-Import`;
   Datenbank, `logs`, `fehlerberichte` in `%LOCALAPPDATA%\IC-Ware\Auftrags-Import`; nichts Neues
   in `C:\Program Files\IC-Ware` (Änderungsdatum der Dateien unverändert).

### Phase 5 – Echter IMAP-Abruf aus der installierten Version

Dovecot läuft nicht unter Windows. Zwei Wege:

- **A (empfohlen): Testpostfach beim Anbieter des Kunden.** Eigene Adresse nur für Tests,
  Testmails aus `tests/mailcorpus.py` hineinsenden (`python tools/imap_testserver.py --eml DIR`
  schreibt sie als Dateien; mit einem Mailprogramm in das Testpostfach legen).
- **B: Testserver auf einem Linux-Rechner im selben Netz** (oder WSL2):
  `python tools/imap_testserver.py --listen <IP> --name <IP> --imaps-port 10993 --imap-port 10143`,
  dann `ca.crt` auf den Windows-Rechner kopieren und `certutil -user -addstore Root ca.crt`.
  Nach dem Test: `certutil -user -delstore Root "IC-Ware IMAP Test-CA"`.
  Dieser Weg ist unter Windows **nicht erprobt** (Firewall, WSL2-Netzwerk).

Prüfen: Verbindung testen (Nachrichtenzahl); Aufträge abrufen (Fortschritt, Oberfläche bleibt
bedienbar); Aufträge erscheinen in der Liste; einen öffnen; zweiter Abruf: 0 neue Aufträge;
im Webmail bzw. per `doveadm fetch` (Weg B) sind alle Mails weiterhin ungelesen und nicht
verschoben. Erwartung mit dem Testbestand und einem Profil **ohne** Absenderregeln (aus dem
Bestand abgeleitet, unter Windows nicht gemessen): 16 geprüft, 11 Bestellungen, 2 Spam,
1 automatische Antwort, 1 nicht lesbar, 1 bereits bekannt.

### Phase 6 – Windows-spezifisch

| Prüfung | Vorgehen | Erwartung |
|---|---|---|
| Passwort ändern | neues Passwort eintragen, speichern | Verbindungstest mit neuem Passwort ok |
| Passwort löschen | Postfach löschen | Eintrag in `cmdkey /list` verschwunden |
| falsche Zugangsdaten | falsches Passwort, Verbindung testen | „Anmeldung am Mailserver fehlgeschlagen“ |
| fehlende Zugangsdaten | Eintrag mit `cmdkey /delete:<Ziel>` löschen, abrufen | „Kein Passwort für das Postfach verfügbar“ |
| gültiges Zertifikat | Weg A oder B mit importierter Test-CA | Verbindung ok |
| ungültiges Zertifikat | Test-CA entfernen (Weg B) | „Sichere Verbindung (TLS) fehlgeschlagen“ |
| Hostname-Fehler | Server per Name/IP eintragen, die nicht im Zertifikat steht | TLS-Fehler |
| Virenscanner/Proxy mit TLS-Prüfung | falls beim Kunden vorhanden | Abruf ok oder TLS-Fehler, nie unverschlüsselt |
| Schreibrechte | als Standardbenutzer arbeiten; danach `C:\Program Files\IC-Ware` prüfen | keine neuen oder geänderten Dateien |

### Phase 7 – Update

Eine ältere veröffentlichte Version von IC-Ware 2.x **gibt es nicht**. Für den Test zwei Builds
erzeugen (etwa `2.0.0-rc.1` und `2.0.0`), ältere installieren, Postfach anlegen, Aufträge abrufen
und bearbeiten, dann die neuere MSI installieren. Prüfen: genau ein Eintrag unter „Apps“, neue
Version unter F1, Datenbank, Einstellungen, Postfach, Passwort und Aufträge unverändert.
Eine Schemamigration ist zwischen diesen Builds nicht nötig (Schema 4); Migrationen sind
automatisch getestet, ein Windows-Upgrade mit Migration ist erst bei einer Schemaänderung möglich.

### Phase 8 – Repair

`AuftragsImport.exe` umbenennen, dann „Apps → Ändern/Reparieren“ bzw. `msiexec /fa <msi>`.
Prüfen: Programm wieder vorhanden und startfähig; Datenbank, Einstellungen, Passwort unverändert.

### Phase 9 – Uninstall

Über „Apps“ deinstallieren. Prüfen: `C:\Program Files\IC-Ware\Auftrags-Import` entfernt,
Startmenü-Eintrag entfernt; `%APPDATA%`, `%LOCALAPPDATA%`, `%PROGRAMDATA%` und die Einträge in der
Anmeldeinformationsverwaltung bleiben (dokumentierte Policy, `installer.md`). Danach neu
installieren: vorhandene Aufträge, Einstellungen und Passwort sind wieder da.

### Phase 10 – Signatur, SmartScreen, Defender

- `Get-AuthenticodeSignature "C:\Program Files\IC-Ware\Auftrags-Import\AuftragsImport.exe"` und
  für die MSI: erwartet **NotSigned** (SIGNING NOT YET AVAILABLE).
- MSI aus dem Internet laden (Mark of the Web) und starten: SmartScreen-Warnung festhalten.
- `"%ProgramFiles%\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File <Ordner>`: Ergebnis festhalten.

Für einen kommerziellen Release fehlt: ein Code-Signing-Zertifikat (Azure Artifact Signing oder
OV, `signing.md`) auf den validierten Namen der IC Ware GbR, Signatur von EXE und MSI mit
Zeitstempel, danach erneute Prüfung von SmartScreen und Defender.

### Phase 11 – Fehlerdialog

`AuftragsImport.exe --fehlertest` (Produktivbetrieb, nicht mit `--demo`): Nach etwa 1,5 Sekunden
erscheint der Fehlerdialog. Prüfen: Fehler-ID, Zeitpunkt, Support-Hinweis, kein Python-Stacktrace
im Dialog; der Fehlerbericht in `%LOCALAPPDATA%\IC-Ware\Auftrags-Import\fehlerberichte` enthält
weder `Fehlertest-Geheimnis-7Q2x` noch `test.kunde@example.de` noch den Benutzernamen; das
Programm bleibt danach bedienbar. Unter Linux geprüft: Inhalt des Berichts (nicht der Dialog).
