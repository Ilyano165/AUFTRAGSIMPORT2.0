# Security-Checkliste

Jeder Punkt ist entweder automatisiert geprüft (Test genannt) oder manuell beim Release abzuhaken.

## Automatisiert (läuft mit `pytest`)

- [x] Mail: Größe, Kopfbereich, Header-Anzahl, MIME-Teile, Tiefe – `tests/security/test_mail_hardening.py`
- [x] Mail: Formfehler, ungültige Zeichensätze, kaputtes Base64, 400 Mutationen ohne Absturz
- [x] Header-Injection und Bidi-Zeichen neutralisiert; mehrdeutige Absender abgelehnt
- [x] Anhänge: Programme, Skripte, Makro-Dokumente, Archive, Tarnungen gesperrt – `test_attachment_limits.py`
- [x] PDF: aktive Inhalte (auch verschleiert), Seitenlimit, Größe
- [x] XLSX/ZIP: Bombe, Pfade, Duplikate, Verschlüsselung, Makros, verschachtelte Archive, gestreamtes Lesen
- [x] CSV: Größe, Zeilen, Spalten, Feldlänge, Nullbytes, Formelzeichen
- [x] XML: XXE, externe DTD, Parameter-Entitäten, Billion Laughs, UTF-16-Umgehung, Größe – `test_xml_safety.py`
- [x] Export: Markup in Auftragsdaten bleibt Daten (CDATA und Entitäten)
- [x] IMAP: TLS-Kontext, kein Login ohne TLS, kein Retry bei Zertifikat/Anmeldung, begrenzter Backoff, Größenlimits – `test_imap_security.py`
- [x] Konfiguration: Port/TLS-Kombination, kein „none“, keine Geheimnisse in JSON
- [x] Quelltext enthält kein `CERT_NONE`, `eval`, `pickle`, `shell=True`, unsicheres XML-Parsen
- [x] Dateisystem: Dateinamen, Pfadverknüpfung, Symlink/Junction, Systemordner, welt-beschreibbar, Netzlaufwerk – `test_filesystem_safety.py`
- [x] Exportordner-Austausch und Symlink werden erkannt; Zielname-Symlink wird nicht verfolgt
- [x] Redaktion, Protokoll-Längenbegrenzung, Absturzbericht ohne Variablen, Diagnosebericht ohne PII – `test_privacy_and_credentials.py`
- [x] Laufzeitprüfung erkennt verwundbare Python/expat-Stände
- [x] Alle regulären Ausdrücke unter Angriffseingaben gemessen – `test_regex_dos.py`

## Manuell vor jedem Release

- [ ] `check_runtime()` im fertigen Installer: keine Probleme (Python, expat, OpenSSL)
- [ ] `pip-audit -r requirements/runtime-windows.txt` ohne Befund; Ergebnis im Release-Protokoll
- [ ] Abhängigkeiten mit Hashes installiert (`--require-hashes`)
- [ ] Installer signiert; Programmordner nur für Administratoren beschreibbar
- [ ] Lexware-Importordner: Rechte nur für Anwender und Lexware; kein „Jeder: Schreiben“
- [ ] Test- und Produktivordner getrennt; Netzlaufwerk nur, wenn bewusst freigegeben
- [ ] Mailkonto: Port 993 (implizit) oder 143 mit STARTTLS; Passwort in der Windows-Anmeldeinformationsverwaltung
- [ ] Umgebungsvariable für Passwörter nur bei dokumentierter Entscheidung
- [ ] Diagnosebericht stichprobenartig gesichtet: keine Bestelldaten
- [ ] Referenzdatei 248090.xml (Kundendaten) nicht im Installer
- [ ] PySide6-LGPL-Pflichten erfüllt (siehe `dependencies.md`)
