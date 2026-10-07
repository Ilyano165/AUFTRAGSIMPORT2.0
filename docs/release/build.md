# Windows-Build

> **STAND: Windows-Build vorbereitet, echter Windows-Build noch ausstehend.**
> Weder der Workflow `.github/workflows/windows-release.yml` noch `tools/release/build-windows.ps1`
> wurden je unter Windows ausgeführt. Es existiert keine geprüfte MSI.
> **CODE SIGNING: NOT CONFIGURED.** Der Lizenztext `packaging/wix/License.rtf` ist ein Platzhalter.

Ein Befehl erzeugt Programm, Installer, Prüfsummen und Build-Manifest. Es gibt keine
Handgriffe wie „Datei irgendwo hinkopieren“.

    python tools/release/build.py all --build-number 57            # Testbuild
    python tools/release/build.py all --release --build-number 57  # Release

Die Pipeline `.github/workflows/windows-release.yml` führt dieselben Schritte einzeln aus
(Abschnitt „GitHub Actions“), `tools/release/build-windows.ps1` lokal auf einem Windows-Rechner
(Abschnitt „Lokaler Windows-Build“). Lokal ohne Installer (etwa zum Testen der EXE):
`build.py all --skip-msi`.

## Ablauf

| Schritt | Inhalt |
|---|---|
| `check-env` | Python exakt laut `.python-version`, 64 Bit, alle Pakete exakt laut `build-windows.lock` |
| `prepare` | Version prüfen, Build-Art festhalten (`build/build_type`: `test` oder `release`), `_build_info.py` und Windows-Versionsressource erzeugen. Mit `--release` zusätzlich: Git-Stand sauber, Tag = Version, CHANGELOG-Abschnitt vorhanden, Python und alle Pakete exakt wie gesperrt, Lizenzvertrag kein Platzhalter, numerische Build-Nummer |
| `test` | `ruff check`, `ruff format --check`, `mypy --strict`, pytest |
| `freeze` | PyInstaller (onedir, ohne Konsole, ohne UPX) mit `PYTHONHASHSEED=0` und `SOURCE_DATE_EPOCH`; Lizenztexte aller ausgelieferten Pakete und Richtlinien-Beispiel nach `docs/` |
| `audit` | prüft den Programmordner: keine Tests, Testhilfen, Build-Werkzeuge (`setuptools`), Datenbanken, `.env`, Protokolle, Einstellungen, private Schlüssel, `.git`; keine Pfade der Build-Maschine in Code-Objekten oder Textdateien (`tools/release/audit.py`). Befund bricht den Build ab |
| `selftest` | startet die fertige EXE: `--version` und `--self-test` (Laufzeit-Sicherheit, Lexware-Spezifikation, Datenbankmigration, Anmeldespeicher speichern/lesen/löschen, TLS-Stammzertifikate, kompletter Abruf aus dem Demo-Testpostfach, deutsche Qt-Texte, Oberfläche). Fehler bricht den Build ab; unter Windows ist ein fehlender Anmeldespeicher ein Fehler |
| `sign-exe` | signiert die EXE **vor** dem Verpacken (siehe `signing.md`) |
| `msi` | WiX 5 baut `IC-Ware-AuftragsImport-X.Y.Z-x64.msi` (Release) bzw. `…-x64-TESTBUILD.msi` (Testbuild); ein Release mit Platzhalter-Lizenz wird verweigert |
| `sign-msi` | signiert das MSI |
| `verify` | nur Windows: MSI vorhanden, Größe plausibel (15–400 MB, nicht gemessene Grenzen), Dateityp (OLE-Verbunddokument), ProductName, ProductVersion, Manufacturer und UpgradeCode aus der Property-Tabelle (Windows-Installer-Schnittstelle), Authenticode-Status; Release verlangt „Valid“. Ergebnis in `build/msi-verify.txt` |
| `manifest` | `SHA256SUMS.txt` und `build-manifest.json` (Build-Art, Versionen, Commit, Werkzeuge, Prüfsummen der Sperrdateien und Artefakte, Signaturstatus) |

## Was festgelegt ist

- Python: `.python-version` (3.14.4). 3.12 erhält seit 3.12.10 nur noch Quellcode-Sicherheitsupdates
  ohne Windows-Installer; ein 3.12-Build würde einen veralteten Interpreter ausliefern.
- Pakete: `requirements/build-windows.lock` und `ci-tools.lock`, jede Datei mit SHA-256
  (`pip install --require-hashes --no-deps --only-binary=:all:`). Neu erzeugen mit
  `python tools/release/lock.py requirements/build-windows.in`, danach `pip-audit`.
- Nur `PySide6-Essentials`: die Addons (u. a. Qt WebEngine) werden nicht gebraucht.
- WiX 5.0.2 (Variable `WIX_VERSION` in der Pipeline). WiX v6 verlangt für die Nutzung eine
  Open Source Maintenance Fee; der Installer ist ohne Änderung mit v6 baubar, falls IC-Ware
  diese Gebühr zahlen möchte.
- GitHub-Actions: auf Commit-SHAs festgelegt (Tag im Kommentar), aufgelöst am 2. Oktober 2026.

## Was reproduzierbar ist und was nicht

Gleicher Commit, gleiche Werkzeuge und Sperrdateien ergeben denselben Programmordner; der Job
`reproducibility` baut zweimal und vergleicht jede Datei (`build.py compare`). Nicht bitgleich
sind absichtlich:

- das MSI: Windows Installer verlangt je Paket einen neuen PackageCode;
- signierte Dateien: der Zeitstempel der Signatur ist Teil der Datei.

Der Build-Zeitpunkt in den Build-Infos ist deshalb der Commit-Zeitpunkt, nicht die Uhrzeit des Builds.

## Versionen

SemVer: `2.0.0`, `2.0.1`, `2.1.0`, Vorabversionen `2.1.0-rc.1`.

    python tools/release/version.py bump patch     # 2.0.0 -> 2.0.1, CHANGELOG-Abschnitt anlegen
    git commit -am "Version 2.0.1" && git tag v2.0.1 && git push --tags

Windows Installer vergleicht nur die ersten drei Stellen (Major ≤ 255, Minor ≤ 255, Patch ≤ 65535).
Die Build-Nummer steht deshalb in der Dateiversion der EXE (`2.0.1.57`), nicht in der MSI-Version.
Eine Vorabversion und die finale Version haben dieselbe MSI-Version; das Upgrade von `-rc` auf final
ist über `AllowSameVersionUpgrades` erlaubt.

## Build-Infos

Version, Build, Commit und Build-Zeitpunkt stehen in F1 → Info, in `AuftragsImport.exe --version`,
in der ersten Zeile jedes Programmstarts im Protokoll und im Kopf jedes Fehlerberichts.

## Lizenzpflichten im Paket

- Qt/PySide6: LGPL v3. onedir hält die Bibliotheken als austauschbare Dateien; Lizenztexte liegen
  unter `docs\drittanbieter`, die Übersicht in `docs\DRITTANBIETER.txt`.
- PyInstaller: GPL mit Bootloader-Ausnahme; die erzeugte Anwendung darf proprietär sein.

## Testbuild und Release-Build

Es gibt genau einen Schalter: `--release` (in `build-windows.ps1`: `-Release`, im Workflow:
`build_type`).

| | Testbuild (Standard) | Release-Build |
|---|---|---|
| Zweck | nur interne Tests | Auslieferung an Kunden |
| MSI-Name | `IC-Ware-AuftragsImport-X.Y.Z-x64-TESTBUILD.msi` | `IC-Ware-AuftragsImport-X.Y.Z-x64.msi` |
| Signatur | nicht verlangt | Pflicht (`--release` schaltet `--require-signature` ein; `verify` verlangt Authenticode „Valid“) |
| Lizenztext | Platzhalter zulässig | Platzhalter wird in `prepare` und `msi` verweigert |
| Git, Tag, CHANGELOG | nicht verlangt | sauberer Stand, Tag = Version, Abschnitt `## [X.Y.Z]` |
| Manifest | `"build_type": "test"` | `"build_type": "release"` |

Ein Testbuild darf nie an Kunden gehen; der Name der MSI macht ihn erkennbar.

## GitHub Actions

Voraussetzungen:

- **Ein GitHub-Repository mit diesem Projekt.** Das ausgelieferte ZIP enthält kein `.git`; das
  Repository wird von IC-Ware angelegt und befüllt. Der Workflow liegt dann unter
  `.github/workflows/windows-release.yml`.
- GitHub Actions im Repository erlaubt; der Build läuft auf einem GitHub-gehosteten Runner
  `windows-2022` (Python laut `.python-version`, WiX 5.0.2 wird im Lauf installiert).
- Nur für Release-Builds: Variable `SIGNING_ENABLED=true` sowie die Variablen
  `ARTIFACT_SIGNING_ENDPOINT`, `ARTIFACT_SIGNING_ACCOUNT`, `ARTIFACT_SIGNING_PROFILE` und die
  Secrets `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` (`signing.md`). Ohne diese Einrichtung bricht ein
  Release-Lauf sofort mit „CODE SIGNING: NOT CONFIGURED“ ab. Ins Repository gehören keine
  Zertifikate, Schlüssel oder Passwörter.
- Build-Provenienz (Attestierung) läuft nur in öffentlichen Repositories oder mit Variable
  `ATTESTATION_ENABLED=true`: Mit GitHub Free, Pro oder Team sind Attestierungen nur für
  öffentliche Repositories verfügbar, für private ist GitHub Enterprise Cloud nötig.

Starten:

- **Testbuild:** Actions → „Windows-Build“ → „Run workflow“ → `build_type: test`.
- **Release:** Tag `vX.Y.Z` pushen oder manuell mit `build_type: release`.

Ablauf: Prüfsummen-gesicherte Installation beider Sperrdateien, `check-env`, `pip-audit`,
Lint, Formatprüfung, `mypy --strict`, komplette Testsuite, WiX, `prepare`, `freeze`, `audit`,
`selftest`, (Signatur), `msi`, (Signatur), `verify`, `manifest`. Jeder Schritt führt genau einen
Befehl aus, damit kein Fehler verloren geht (PowerShell wertet in einem Schritt nur den letzten
Exit-Code aus). Es gibt kein `continue-on-error`.

Ergebnis (nur bei erfolgreichem Lauf), im Lauf unter „Artifacts“:

- `IC-Ware-AuftragsImport-<test|release>-<Laufnummer>`: MSI, `SHA256SUMS.txt`,
  `build-manifest.json` (Upload schlägt fehl, wenn keine MSI existiert).
- `Build-Protokolle-<test|release>-<Laufnummer>`: `build_type`, `version.txt`, `selftest.txt`,
  `msi-verify.txt`, PyInstaller-Warnungen, Manifest. Wird auch bei Fehlern hochgeladen; der Lauf
  bleibt dann rot.
- Die Zusammenfassung des Laufs zeigt das Ergebnis von `verify` und die Prüfsummen.

## Lokaler Windows-Build

**NOT TESTED ON WINDOWS:** `tools/release/build-windows.ps1` wurde noch nie unter Windows
ausgeführt. Unter Linux geprüft sind nur die Syntax (PowerShell-Parser) und der Abbruch auf
einem Nicht-Windows-System.

Voraussetzungen (aus Skript und Workflow abgeleitet):

- Windows, 64 Bit, Architektur x64 (AMD64); ARM64 wird abgelehnt.
- Python **3.14.4**, 64 Bit, von python.org, mit Python-Launcher `py`.
- .NET SDK für `dotnet tool`, dann WiX **5.0.2** mit UI-Erweiterung:

      dotnet tool install --global wix --version 5.0.2
      wix extension add --global WixToolset.UI.wixext/5.0.2

- Für einen Release zusätzlich: Git-Repository, endgültiger Lizenztext, `ICW_SIGN_COMMAND`
  (`signing.md`).

Aufruf im Projektordner:

    powershell -ExecutionPolicy Bypass -File tools\release\build-windows.ps1
    powershell -ExecutionPolicy Bypass -File tools\release\build-windows.ps1 -Release -BuildNumber 57

Das Skript

1. zeigt Windows-Version und Architektur und bricht ab, wenn es nicht x64 ist;
2. prüft WiX 5.0.2 samt UI-Erweiterung und Python laut `.python-version` (64 Bit);
3. **löscht `build`, `dist` und `.venv-build`** und legt `.venv-build` neu an;
4. aktualisiert pip **nicht**: ein ungesperrtes Update lädt eine beliebige Version von PyPI und
   unterläuft den Prüfsummen-Prozess; verwendet wird das mitgelieferte pip (Version wird
   ausgegeben);
5. installiert `requirements\build-windows.lock` und `requirements\ci-tools.lock` nur mit
   geprüften Prüfsummen;
6. führt `check-env`, `test`, `prepare`, `freeze`, `audit`, `selftest`, (Signatur), `msi`,
   (Signatur), `verify`, `manifest` aus;
7. bricht bei jedem Fehler sofort mit Exit-Code 1 und „ABBRUCH: …“ ab.

Erwartetes Ergebnis am Ende („=== Ergebnis ===“): Pfad der MSI, Größe, SHA-256 (mit
`Get-FileHash` berechnet und gegen `dist\SHA256SUMS.txt` geprüft), Version, Build-Zeile und
Build-Art. Die MSI liegt in `dist\`:

- Testbuild: `dist\IC-Ware-AuftragsImport-2.0.0-x64-TESTBUILD.msi`
- Release: `dist\IC-Ware-AuftragsImport-2.0.0-x64.msi`

Daneben `dist\SHA256SUMS.txt`, `dist\build-manifest.json`, `build\msi-verify.txt`,
`build\selftest.txt`. Prüfsumme selbst nachrechnen:

    Get-FileHash -Algorithm SHA256 dist\IC-Ware-AuftragsImport-2.0.0-x64-TESTBUILD.msi

## Statische Prüfung ohne Windows

Unter Linux geprüft (belegt **nicht**, dass der Windows-Build funktioniert):

- Workflow mit actionlint 1.7.12: keine Befunde. Dabei aufgefallen: Die bis dahin ausgelieferte
  Fassung war ungültiges YAML (`--only-binary=:all: -r` in einem ungequoteten Wert); GitHub
  hätte keinen Lauf gestartet.
- `build-windows.ps1` mit dem Parser von PowerShell 7.5.11: keine Syntaxfehler; auf Linux bricht
  das Skript wie vorgesehen mit Exit-Code 1 ab.
- `tests/unit/test_release_windows.py`: Aufbau von Workflow und Skript, Trennung von Testbuild
  und Release, Prüflogik von `verify`.

Vor Änderungen am Workflow: `actionlint .github/workflows/windows-release.yml`.
