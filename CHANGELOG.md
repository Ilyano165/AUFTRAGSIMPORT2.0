# Änderungsprotokoll

## [Unveröffentlicht] – Windows-Build vorbereitet

Windows-Build vorbereitet, echter Windows-Build noch ausstehend: Workflow und
`build-windows.ps1` liefen nie unter Windows. CODE SIGNING: NOT CONFIGURED.

### Hinzugefügt
- `tools/release/build-windows.ps1`: lokaler Windows-Build in einem Zug (Windows/x64, Python
  3.14.4, WiX 5.0.2, frische venv, Sperrdateien mit Prüfsummen, alle Prüfungen, MSI, SHA-256).
- `build.py check-env` (Python, 64 Bit, Pakete laut Sperrdatei) und `build.py verify`
  (MSI vorhanden, Größe, Dateityp, Produkt, Version, Hersteller, UpgradeCode, Signatur).
- Workflow: Auswahl `build_type` (test/release), Lint, Formatprüfung, `mypy --strict`,
  Produktprüfung, MSI-Prüfung, Build-Protokolle als Artefakt, Zusammenfassung.

### Geändert
- Testbuilds heißen `…-x64-TESTBUILD.msi`, das Manifest enthält `build_type`.
- `--release` verlangt immer eine Signatur; `msi` verweigert im Release die Platzhalter-Lizenz.
- Workflow-Actions auf Commit-SHAs festgelegt; Attestierung nur in öffentlichen Repositories
  oder mit `ATTESTATION_ENABLED`.

### Behoben
- Der Workflow war ungültiges YAML und wäre auf GitHub nie gestartet.
- Workflow-Schritte mit mehreren Befehlen konnten Fehler verschlucken; jetzt ein Befehl je Schritt.

## [Unveröffentlicht] – Prüfung der Windows-Auslieferung

Keine Windows-Tests durchgeführt (keine Windows-Umgebung verfügbar); siehe
`docs/release/windows-testmatrix.md`.

### Hinzugefügt
- Build-Schritt `audit`: bricht ab bei Tests, Testhilfen, Build-Werkzeugen, Datenbanken, `.env`,
  Protokollen, Einstellungen, privaten Schlüsseln oder Pfaden der Build-Maschine im Produkt.
- Selbsttest prüft zusätzlich Anmeldespeicher (speichern, lesen, löschen eines Testeintrags),
  TLS-Stammzertifikate und einen kompletten Abruf aus dem Demo-Testpostfach.
- `--fehlertest`: kontrollierter Fehler im Produktivbetrieb zur Prüfung von Fehlerdialog und
  Fehlerbericht.
- IMAP-Testserver für einen anderen Rechner nutzbar (`--listen`, `--name`, feste Ports).
- `docs/release/windows-testmatrix.md`: Matrix und Testprotokoll für echtes Windows.

### Geändert
- `setuptools`, `pkg_resources`, `distutils` und `unittest` werden nicht mehr mit ausgeliefert.
- Pipeline-Werkzeuge und Entwicklungsabhängigkeiten haben dieselben Versionen (`mypy` 2.4.0,
  `ruff` 0.16.10, dazu `types-defusedxml`); ein Test prüft den Gleichstand.

### Behoben
- `tools/release/lock.py` löst Extras von Abhängigkeiten auf; `filelock` fehlte deshalb in
  `requirements/ci-tools.lock`. Sperrdatei neu erzeugt.
- MIME: Teile einer mehrteiligen Mail werden nur verarbeitet, wenn sie tatsächlich Mails sind.

### Bekannte Probleme
- Installer lässt auch Windows 8.1 zu, auf dem das Programm nicht läuft (Befund W1).

## [Unveröffentlicht] – Mail-Abruf

### Hinzugefügt
- **Aufträge abrufen** (Knopf, F5): Abruf des Postfachs des aktiven Profils im Hintergrund-Thread
  mit Fortschritt, Abbrechen, Statusanzeige und Zusammenfassung ohne Mailinhalte. Ein Auftrag je
  Mail in einer Transaktion (Mail, Auftrag, Idempotenzschlüssel, Journal).
- Einstellungen → **Postfächer**: anlegen, bearbeiten, löschen; Passwort in der
  Windows-Anmeldeinformationsverwaltung (nie angezeigt, nie in `settings.json`);
  **Verbindung testen** (nur lesend, ein Versuch).
- Vorprüfung vor dem Download: Größenlimit, Spam-Markierungen des Mailservers, automatische
  Antworten und Zustellberichte, Absenderfilter von Postfach und Profil.
- IMAP-Client: Ordner schreibgeschützt öffnen (`EXAMINE`), UIDVALIDITY, Auflistung aller nicht
  gelöschten Mails, Kopfdaten in Paketen, Ordnernamen mit Umlauten (modifiziertes UTF-7),
  Fehlerarten Zeitüberschreitung und Ordner, Abbruch aus einem anderen Thread.
- Testmodus: `--demo` mit Testpostfach, `--demo --testpostfach ORDNER` für eigene `.eml`-Dateien.
- Testsystem: lokaler Dovecot-IMAP-Server mit Test-CA (`tests/imapserver.py`,
  `tools/imap_testserver.py`), 16 reproduzierbare Testmails (`tests/mailcorpus.py`), vorbereitete
  Live-Tests je Anbieter (`tests/integration/test_imap_providers.py`).
- Dokumentation: `docs/mail-abruf.md`, `docs/mail-anbieter.md`.

### Geändert
- F5 und der Knopf oben rechts rufen jetzt **Aufträge ab** statt die Liste neu zu laden
  („Aktualisieren“ entfällt; die Liste aktualisiert sich nach jedem Abruf).
- Die Prüfungen „Bestellnummer bereits erfasst“ und „Mail mit identischem Inhalt bereits
  verarbeitet“ sind jetzt aktiv (vorher vorhanden, aber nie mit Daten versorgt).

### Behoben
- IMAP: Nach einem Verbindungsabbruch wurde der Ordner nicht erneut ausgewählt; der nächste
  Befehl wäre fehlgeschlagen.

### Bekannte Probleme
- Microsoft 365 / Exchange Online nicht nutzbar (OAuth2 fehlt).
- Signatur „Person / Betrieb ohne Rechtsform“: Firma und Name werden vertauscht erkannt.
- HTML-Tabellen mit reiner Mengenspalte werden nicht als Positionen erkannt.
- Kein echter Mail-Anbieter getestet; Abruf unter Windows nicht getestet.

## [Unveröffentlicht] – Release-Vorbereitung

### Hinzugefügt
- Reproduzierbarer Windows-Build mit einem Befehl (`tools/release/build.py`): Release-Prüfungen,
  Tests, PyInstaller (onedir, ohne UPX), Selbsttest der fertigen EXE, Signatur-Schnittstelle,
  MSI, Prüfsummen und Build-Manifest; Pipeline `.github/workflows/windows-release.yml` mit
  Abhängigkeitsaudit, Signatur über Azure Artifact Signing, Build-Provenienz und
  Reproduzierbarkeitsprüfung.
- Windows-Installer (WiX 5): Installation pro Rechner, Major Upgrade, Reparatur, Deinstallation
  ohne Berührung der Benutzerdaten, deutsche Oberfläche, Startmenü-Eintrag mit App-ID.
- Abhängigkeiten mit SHA-256 gesperrt (`requirements/*.lock`, `tools/release/lock.py`).
- SemVer-Werkzeug (`tools/release/version.py`), Windows-Datei- und MSI-Versionen.
- Build-Infos (Version, Build, Commit, Build-Zeitpunkt) in F1 → Info, `--version`, Protokoll und
  Fehlerberichten.
- Fehlerdialog bei unerwarteten Fehlern mit Fehler-ID, Zeitpunkt und Support-Hinweis; Berichte
  ohne Geheimnisse und Benutzerpfade; Erfassung nativer Abstürze; Fehler aus Hintergrund-Threads.
- Richtlinie der IT in `%PROGRAMDATA%` (Support-Kontakt, Protokollstufe, Update-Einstellungen).
- Vorbereitung für Updates (Manifest-Prüfung, Versionsvergleich, Kanäle) ohne Downloader.
- Produktivstart: Ablageorte, Altdaten-Umzug, Einzelinstanz, Laufzeitprüfung, Einstellungen,
  Datenbankmigration; `--self-test` für die Pipeline.

### Geändert
- Datenbank, Katalog-Backups, Protokolle und Fehlerberichte liegen in `%LOCALAPPDATA%` statt im
  wandernden Profil; Einstellungen bleiben in `%APPDATA%`. Vorhandene Daten werden einmalig verschoben.
- Build-Interpreter Python 3.14.4 (3.12 erhält keine Windows-Installer mehr).
- Nur noch `PySide6-Essentials` statt PySide6 mit Addons.
- Protokollrotation: 10 statt 5 ältere Dateien; Benutzername in Pfaden wird ersetzt.

### Behoben
- Qt-Standardknöpfe in Rückfragen und Dateidialogen waren englisch („Save“, „Cancel“).

## [Unveröffentlicht] – Stammdaten

### Hinzugefügt
- Mehrere Firmenprofile mit Lieferantenadresse, Mailkonto, Absenderregeln, Lexware-Importpfad,
  Katalog, Mailvorlage, Versand- und Zahlungsmethoden, Steuersätzen und Exportregeln;
  Einstellungen → Firmenprofile mit Live-Test der Absenderregeln und Vorschau der Mailvorlage;
  Profilwechsel in der Titelleiste (Strg+, öffnet die Einstellungen).
- Datentrennung in der Datenbank: Profil je Auftrag (Migration v4), Nummernkreis je Profil,
  Katalogversionen je Profil, Prüfung auf gemeinsame Ordner und Postfächer.
- Artikelkatalog: Import aus CSV/TXT (auch Lexware-ASCII ohne Kopfzeile), XLSX und JSON;
  Zuordnungsassistent mit Brutto/Netto-Umrechnung; Validierung; atomarer Import mit Backup;
  Versionen mit Änderungsstatistik und Rücksprung; Backups mit Prüfsumme und Wiederherstellung;
  Export als XLSX, CSV (Formelschutz) und JSON; Neuzuordnung offener Aufträge mit Vorschau.
- Zuordnungsdetails je Position (Quelle, Artikelnummer, Name, Methode, Sicherheit, Status) mit
  „Vorschlag übernehmen“.
- Eigener, gehärteter XLSX-Leser und -Schreiber (ohne openpyxl).

### Behoben
- Dateiendungen wurden bei Katalogquellen nie erkannt (Endung kommt ohne Punkt).
- Demo vergab Belegnummern am Zähler vorbei; Zuordnungen und Kataloge im Demo laufen jetzt über
  Matcher und Import.

## [Unveröffentlicht] – Oberfläche

### Hinzugefügt
- Neue Oberfläche (PySide6): Titelleiste „IC-Ware | Auftrags-Import“, Bereiche Posteingang /
  Bereit / Prüfung erforderlich / Erledigt / Fehler mit Zählern, Auftragsliste mit sieben Spalten,
  Detailansicht mit acht Bereichen, Exportdialog mit Zusammenfassung und Ergebnisübersicht.
- Befunde mit Ort und nötiger Aktion („Artikelnummer fehlt → Position 4 → Benutzeraktion
  erforderlich“), Sprung zur Stelle, rote/gelbe Feldmarkierung mit Begründung.
- Tastatursteuerung (Strg+F/S/E, Enter, Esc, Pfeile, Tab, Strg+1–5, F1, F2, F5).
- High-DPI ohne Rundung, adaptive Spalten, Bereichsnamen und Kopfzeile, gestapeltes Layout
  unter 980 px; Fenstergröße und Teilung werden gespeichert.
- Anwendungsschicht `app/workbench.py`, Darstellungslogik `app/presentation.py` (Qt-frei),
  Demobestand `app/demo.py`, Start mit `python -m icware_auftragsimport --demo`.
- `tools/gui_screenshots.py` rendert 100/125/150/200 % für Review und Doku; `docs/gui/design.md`.

### Behoben
- Zahlungsart ging beim Speichern aus der Oberfläche verloren (String-Enum über Qt), gefunden
  durch den Ende-zu-Ende-Test.

### Geändert
- Security-Scan erkennt `exec(`/`eval(` nur noch als freistehende Aufrufe; Qt-Methoden wie
  `dialog.exec()` sind erlaubt. Selbstprüfung im Test.

## 2.0.0 (in Entwicklung)

Vollständiger Neuaufbau als kommerzielles Produkt „IC-Ware Auftrags-Import“.

### Behobene Befunde aus dem Audit von 1.1.0 (mit Regressionstest)

- C-03 Unbekannter Zeichensatz bricht den Abruf nicht mehr ab.
- C-09 Belegnummern aus Nummernkreis statt Zeitstempel; keine Kollisionen.
- C-10 Duplikatschutz über mehrere unabhängige Merkmale.
- C-11 Instanzsperre gegen parallele Läufe.
- C-14 Anschrift nur mit Firmenname gilt als unvollständig.
- C-15 Artikelnummern gehören nur zur Position direkt darüber.
- C-16 Unbekannte Artikelnummern gelten nie als zugeordnet.
- C-17 Zahlungsart nur aus eindeutigen Angaben.
- C-18 Weitergeleitete Mails werden entzitiert; Kundenkennung aus dem Originalabsender.
- C-19 Gleiche Positionen werden nicht zusammengeführt.
- C-20 Sätze mit Zahl am Anfang werden keine Positionen; Grenzfälle erscheinen als Hinweis.
- C-21 „Rechnung an:“ im Satz eröffnet keine Anschrift.
- C-22 USt-IdNr. wird nicht zur Artikelnummer.
- C-23 „Versand am …“ ist keine Versandart.
- C-24 „1.234“ wird als Tausender gelesen und zur Prüfung vorgelegt.
- C-25 Vierstellige PLZ ohne Land werden zur Prüfung vorgelegt.
- C-29 Steuerzeichen werden vor der Erkennung entfernt.
