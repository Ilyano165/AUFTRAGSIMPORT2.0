# Release-Checkliste: IC-Ware Auftrags-Import

Für jedes Release kopieren (etwa in das Release-Ticket) und vollständig abhaken. Ein Punkt gilt
nur als erledigt, wenn das Bestehenskriterium erfüllt ist. „(automatisch)“ prüft die Pipeline;
alles andere prüft ein Mensch auf echten Windows-Rechnern.

| Feld | Wert |
|---|---|
| Version / Build / Commit | |
| Pipeline-Lauf (Link) | |
| Geprüft von / am | |
| Freigabe (zweite Person) | |

## 0. Blocker vor dem ersten Kundenrelease (2.0.0)

- [ ] **Mailanbieter des Kunden geklärt** (`docs/mail-anbieter.md`). Microsoft 365 / Exchange Online
      ist ohne OAuth2 **nicht nutzbar**: Nutzt der Kunde Microsoft 365, ist das ein Release-Blocker.
- [ ] **Echter Abruf beim Kundenanbieter** erfolgreich (`tests/integration/test_imap_providers.py`
      mit Zugangsdaten des Kunden), Anbieter in `docs/mail-anbieter.md` auf TESTED gesetzt.
- [ ] **Adresserkennung „Person / Betrieb ohne Rechtsform“** behoben oder mit dem Kunden als bekannte
      Einschränkung abgestimmt (Firma und Name vertauscht; Test `test_signature_person_then_business…`
      steht auf xfail).
- [ ] **Lizenzvertrag (EULA)** ersetzt `packaging/wix/License.rtf` (der Release-Build bricht sonst ab).
- [ ] **Code-Signatur** eingerichtet (`docs/release/signing.md`); Identitätsvalidierung der GbR geklärt.
- [ ] **Support-Kontakt** bestätigt (`branding.SUPPORT_CONTACT`, derzeit `support@ic-ware.eu`).
- [ ] **Datenbanksicherung** entschieden: eingebaute Sicherung nachrüsten oder Sicherung des
      Datenordners verbindlich in der Kundendokumentation festlegen (siehe Abschnitt 16).
- [ ] **Lexware-Zielvalidierung** (OQ-01) mit der echten Lexware-Version des Kunden; Brutto→Netto mit
      4 Nachkommastellen von Lexware akzeptiert.
- [ ] **Belegpräfix** entschieden („AI“ oder anderes).
- [ ] **WiX-Lizenz** entschieden: WiX 5 (ohne Gebühr) oder WiX 6 mit Open Source Maintenance Fee.
- [ ] **GitHub-Actions auf Commit-SHA gepinnt** (`.github/workflows/windows-release.yml`; seit
      2026-10-02 der Fall, bei jeder Änderung neu prüfen) und `actionlint` ohne Befund.
- [ ] **Release-Lauf der Pipeline grün** mit `build_type: release` (Signatur eingerichtet; sonst
      bricht er mit „CODE SIGNING: NOT CONFIGURED“ ab). Ein Testbuild (`…-TESTBUILD.msi`) ist
      nie auslieferbar.

## 1. Tests

- [ ] ruff, mypy strict und alle Tests grün (automatisch, Schritt `test`).
- [ ] Oberflächentests grün (automatisch, offscreen).
- [ ] **Echte IMAP-Tests (Dovecot) auf Linux ausgeführt** und grün: `pytest tests/integration/test_imap_dovecot.py
      tests/gui/test_accounts_page.py -rs` als root mit installiertem `dovecot`, `doveadm`, `openssl`.
      Die Windows-Pipeline überspringt diese Tests („NOT TESTED – …“); ein grüner Pipeline-Lauf
      allein beweist den IMAP-Abruf nicht.
- [ ] Produktprüfung ohne Befund (automatisch, Schritt `audit`).
- [ ] Selbsttest der fertigen EXE: alle Zeilen „OK“, insbesondere Anmeldespeicher (WinVaultKeyring),
      TLS-Zertifikatsspeicher und Mail-Abruf (automatisch, Schritt `selftest`, `build/selftest.txt`).
- [ ] Windows-Testprotokoll `docs/release/windows-testmatrix.md` (Phasen 2–11) auf echtem Windows
      durchgeführt, Matrix mit Datum, Build und Prüfer auf TESTED gesetzt.
- [ ] Fehlerdialog mit `AuftragsImport.exe --fehlertest` geprüft (Bericht ohne Geheimnis, Mailadresse,
      Benutzername).
- [ ] Demo-Durchlauf auf Windows: `AuftragsImport.exe --demo`, Auftrag prüfen, korrigieren, freigeben,
      exportieren, Katalog importieren, Profil wechseln.
- [ ] Neue Funktionen dieses Releases einzeln nach CHANGELOG geprüft.

## 2. Security

- [ ] `pip-audit` ohne Befund (automatisch).
- [ ] Selbsttest „Laufzeit“ OK: Python ≥ 3.12.6, expat ≥ 2.7.1, OpenSSL 3 (automatisch).
- [ ] Sperrdateien aktuell (`tools/release/lock.py`), Installation nur mit `--require-hashes` (automatisch).
- [ ] Nach einem Testlauf mit Testpasswort, Test-IBAN und Test-Mailadresse:
      `findstr /s /i "<Testpasswort>" "%LOCALAPPDATA%\IC-Ware\Auftrags-Import\*"` → kein Treffer;
      ebenso IBAN und Mailadresse in `logs` und `fehlerberichte`.
- [ ] `settings.json` enthält kein Passwort; Passwort steht in der Anmeldeinformationsverwaltung.
- [ ] `icacls "C:\Program Files\IC-Ware\Auftrags-Import"`: Benutzer haben nur Lesen/Ausführen.
- [ ] Richtlinie mit einem Schlüssel „password“ wird abgelehnt (Protokoll), Programm startet trotzdem.

## 3. Build

- [ ] Tag `vX.Y.Z` entspricht `__version__`; CHANGELOG hat den Abschnitt `## [X.Y.Z]` (automatisch).
- [ ] Ausgelieferte Dateien stammen aus der Pipeline, nie aus einem lokalen Build.
- [ ] Job `reproducibility`: 0 unterschiedliche Dateien.
- [ ] `SHA256SUMS.txt`, `build-manifest.json` und Provenienz-Attestierung archiviert.
- [ ] EXE → Eigenschaften → Details: Dateiversion `X.Y.Z.<Build>`, Produktversion `X.Y.Z`, Herausgeber IC-Ware.
- [ ] F1 → Info: Version, Build, Commit und Build-Zeitpunkt stimmen mit `build-manifest.json` überein;
      Commit ohne „(geändert)“.

## 4. Signatur

- [ ] `signtool verify /pa /all /v` für das MSI und die installierte `AuftragsImport.exe`: gültig,
      Herausgeber IC-Ware, Zeitstempel vorhanden.
- [ ] Download über einen Browser auf einem frischen Rechner: SmartScreen-Verhalten notiert.
- [ ] Zertifikat noch mindestens 30 Tage gültig.

## 5. Installer

- [ ] Neuinstallation interaktiv (Windows 10 und 11): Lizenzseite, Zielordner, Fortschritt, Abschluss
      auf Deutsch; Startmenü-Eintrag startet das Programm.
- [ ] Stille Installation `msiexec /i <msi> /qn /l*v install.log`: Rückgabewert 0.
- [ ] Taskleiste: ein Symbol für Startmenü-Eintrag und laufendes Programm; Anheften funktioniert.
- [ ] Einstellungen → Apps: Name, Herausgeber, Version, Symbol, Support-Kontakt korrekt; „Ändern“ fehlt,
      „Reparieren“ vorhanden.
- [ ] Reparatur: eine Datei im Programmordner löschen → Reparieren → Datei wieder da, Daten unverändert.
- [ ] Installation als Standardbenutzer: UAC-Abfrage, ohne Adminrechte sauberer Abbruch.

## 6. Upgrade

- [ ] Vorversion mit Testdaten (mehrere Profile, Aufträge in allen Status, Katalog mit Versionen,
      gespeichertes Passwort) → neue Version installieren → alle Daten vorhanden, Version neu,
      nur ein Eintrag in Apps, Datenbankmigration im Protokoll.
- [ ] Upgrade bei geöffnetem Programm: Windows Installer fordert zum Schließen auf; danach Start ohne Fehler.
- [ ] Downgrade wird mit deutscher Meldung verweigert.
- [ ] Vorabversion (`-rc.N`) → finale Version derselben Nummer funktioniert.
- [ ] Stilles Upgrade über die Softwareverteilung des Kunden (falls genutzt).

## 7. Deinstallation

- [ ] Programmordner, Startmenü-Eintrag und `HKLM\Software\IC-Ware\Auftrags-Import` entfernt; Eintrag in Apps weg.
- [ ] Daten in `%APPDATA%` und `%LOCALAPPDATA%` sowie Passwörter bleiben erhalten.
- [ ] Neuinstallation danach findet alle Daten wieder.
- [ ] Kein Neustart nötig.

## 8. Windows 10/11

- [ ] Windows 10 22H2 x64 und Windows 11 (aktuelle Version) x64, je auf einer echten oder virtuellen
      Maschine; Betrieb als Standardbenutzer ohne Adminrechte.
- [ ] Windows mit englischer Anzeigesprache: Programm und Qt-Standardknöpfe bleiben deutsch.
- [ ] Microsoft Defender (und der Virenscanner des Kunden): keine Fehlalarme bei Installation und Start.
- [ ] Servergespeicherte Profile oder Terminalserver, falls beim Kunden vorhanden: Start, Arbeit,
      Abmelden, erneute Anmeldung ohne Datenverlust.

## 9. High DPI

- [ ] Je ein Durchgang bei 100 %, 125 %, 150 %, 175 % und 200 %: Hauptfenster, Detailbereich, Einstellungen,
      Katalog-Import, Fehlerdialog, Info-Dialog ohne abgeschnittene Texte oder unscharfe Symbole.
- [ ] Fenster zwischen Monitoren mit unterschiedlicher Skalierung verschieben.
- [ ] Mindestauflösung 1366 × 768 bei 100 %: alle Bedienelemente erreichbar.

## 10. Offlinebetrieb

- [ ] Start ohne Netzwerk (Netzwerkkabel/WLAN aus): keine Fehlermeldung, kein Netzwerkzugriff beim Start.
- [ ] Prüfen, Korrigieren, Freigeben und Export in einen lokalen Ordner ohne Netzwerk.
- [ ] Exportziel auf nicht erreichbarem Netzlaufwerk: verständliche Meldung, Auftrag bleibt freigegeben,
      nach Wiederverbindung erneut exportierbar, keine halbe Datei.
- [ ] Mail-Abruf ohne Netzwerk: verständliche Meldung, Programm bleibt bedienbar.

## 11. Mail-Abruf auf Windows (`docs/mail-abruf.md`)

Automatisch gegen Dovecot getestet; auf echten Windows-Rechnern mit dem Postfach des Kunden prüfen.

- [ ] Postfach in Einstellungen → Postfächer anlegen, Passwort speichern: Eintrag
      `IC-Ware/Auftrags-Import/<Kennung>` in der Windows-Anmeldeinformationsverwaltung vorhanden,
      Passwort nicht in `settings.json`, nicht in `logs`.
- [ ] Verbindung testen: Erfolgsmeldung mit Nachrichtenzahl; in Outlook/Webmail danach keine Mail
      als gelesen markiert.
- [ ] Aufträge abrufen: Oberfläche bleibt bedienbar, Fortschritt sichtbar, Zusammenfassung plausibel,
      Mails im Postfach weiterhin ungelesen und nicht verschoben.
- [ ] Zweiter Abruf: keine neuen Aufträge aus denselben Mails.
- [ ] Abbrechen während des Abrufs; danach erneuter Abruf vollständig, keine Duplikate.
- [ ] Mit aktivem Virenscanner-Mailschutz bzw. Firmen-Proxy: Abruf funktioniert oder verständliche
      TLS-Meldung (kein Rückfall auf unverschlüsselt).

- [ ] Falsches Passwort: Meldung „Anmeldung abgelehnt“, kein Passwort im Protokoll.
- [ ] Server nicht erreichbar, DNS-Fehler, Zeitüberschreitung: Meldung, Programm bleibt bedienbar.
- [ ] Ungültiges oder abgelaufenes TLS-Zertifikat: Abbruch, kein Rückfall auf unverschlüsselt.
- [ ] Ordner fehlt, Postfach leer: verständliche Meldung bzw. kein Fehler.
- [ ] Sehr große Mail oder Anhang über der Grenze: übersprungen und gemeldet, nicht abgestürzt.
- [ ] Verbindungsabbruch mitten im Abruf: erneuter Abruf erzeugt keine doppelten Aufträge.

## 12. Lexware-Export

- [ ] `python tools/lexware_testpaket.py` erzeugt das Testpaket; Import in die Lexware-Version des
      Kunden erfolgreich (Version notieren).
- [ ] Umlaute, ß, Sonderzeichen und lange Bezeichnungen kommen in Lexware unverändert an.
- [ ] Steuersätze 19 %, 7 %, 0 %; Netto- und Bruttopreise; Summen identisch mit dem Auftrag.
- [ ] Export auf Netzlaufwerk: eindeutige Dateinamen, keine halbe Datei bei Abbruch, kein doppelter Export.

## 13. Encoding

- [ ] Katalogimport: CSV UTF-8 mit und ohne BOM, Windows-1252, Lexware-ASCII ohne Kopfzeile, XLSX.
- [ ] Mails: UTF-8, ISO-8859-1/15, Windows-1252, quoted-printable, base64, kodierte Betreffzeilen
      (automatisch über `tests/mailcorpus.py`; zusätzlich echte Kundenmails im Testmodus).
- [ ] Pfade mit Umlauten und Leerzeichen: Windows-Benutzer „Jürgen Müller“, Exportordner „Aufträge 2026“.
- [ ] Exportzeichensatz laut Profil kommt in Lexware korrekt an.

## 14. Recovery

- [ ] Programm während eines Exports im Task-Manager beenden: nach Neustart eindeutiger Status, kein
      doppelter Export.
- [ ] VM während eines Katalogimports hart ausschalten: alter Katalog aktiv, keine halbe Version.
- [ ] `settings.json` absichtlich beschädigen: Meldung mit Pfad zu den Sicherungen; Sicherung
      zurückspielen funktioniert.
- [ ] Zweiten Programmstart versuchen: Hinweis „bereits geöffnet“.
- [ ] Nach einem Fehlerdialog: Fehler-ID im Dialog = Dateiname in `fehlerberichte`; „Weiterarbeiten“
      und „Programm beenden“ funktionieren.

## 15. Logging

- [ ] Protokoll unter `%LOCALAPPDATA%\IC-Ware\Auftrags-Import\logs`, erste Zeile je Start mit Build-Infos.
- [ ] Rotation: bei 5 MB entsteht `.1`; nie mehr als 10 ältere Dateien.
- [ ] Protokollstufe über die Richtlinie umstellbar.
- [ ] Benutzername in Pfaden ersetzt (`%USERPROFILE%`); Redaktion siehe Abschnitt 2.
- [ ] Höchstens 50 Fehlerberichte.

## 16. Backup

- [ ] Jede Änderung der Einstellungen erzeugt eine Sicherung in `%APPDATA%\IC-Ware\Auftrags-Import\backups`.
- [ ] Katalog-Backups (30 je Profil) mit Prüfsumme; Wiederherstellung einer Version getestet.
- [ ] Datenbank: Es gibt **keine eingebaute Sicherung** (Abschnitt 0). Bis zur Entscheidung: Sicherung von
      `%LOCALAPPDATA%\IC-Ware\Auftrags-Import` bei geschlossenem Programm dokumentiert und eine
      Rücksicherung getestet.
- [ ] Umzug auf neuen Rechner: Ordner kopieren, Passwörter neu eingeben; Ablauf getestet und dokumentiert.

## 17. Recht, Lizenzen, Support

- [ ] EULA im Installer; Datenschutz-Hinweis für Kunden (Daten bleiben lokal, keine Übertragung an IC-Ware).
- [ ] `docs\DRITTANBIETER.txt` und `docs\drittanbieter` vollständig (Qt/PySide6 LGPL, shiboken6, keyring, defusedxml).
- [ ] Support-Ablauf mit Fehler-ID vorbereitet: Wer nimmt die ID an, wo liegt der Bericht, welche Daten
      dürfen angefordert werden.

## 18. Freigabe

- [ ] CHANGELOG und Versionshinweise für Kunden.
- [ ] Installationsanleitung für die Kunden-IT (`docs/release/installer.md`) aktuell.
- [ ] SHA-256 des MSI mit der Auslieferung veröffentlicht.
- [ ] Freigabe durch eine zweite Person (Vier-Augen-Prinzip).
