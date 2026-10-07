# Threat Model – IC-Ware Auftrags-Import 2.0

Stand: 2026-10-01 · Methode: Datenflüsse und Vertrauensgrenzen, Bedrohungen nach STRIDE, Maßnahme mit Testnachweis.

## System und Schutzgüter

Die Anwendung läuft lokal auf einem Windows-Arbeitsplatz. Sie holt Bestellmails per IMAP, erkennt Aufträge regelbasiert, lässt sie prüfen und freigeben und schreibt Lexware-openTRANS-Dateien in einen Importordner.

| Schutzgut | Warum kritisch |
|---|---|
| Mail-Zugangsdaten | Zugriff auf das Bestellpostfach des Kunden |
| Personenbezogene Daten in Mails und Aufträgen | DSGVO; Namen, Anschriften, Mail, Telefon, ggf. Bankdaten |
| Integrität der Exportdateien | Falsche Aufträge, Preise, Lieferadressen in der Warenwirtschaft |
| Lexware-Importordner | Jede dort abgelegte XML-Datei kann als Auftrag importiert werden |
| Verfügbarkeit des Abrufs | Hängender Abruf verzögert Bestellungen |
| Arbeitsplatz des Anwenders | Kein Anhang darf Code ausführen |

## Akteure

1. **Externer Absender (nicht vertrauenswürdig):** Jeder kann eine Mail an das Postfach schicken – mit beliebigem MIME, Headern, Anhängen, Zeichensätzen und Inhalten.
2. **Gefälschter oder kompromittierter Mailserver bzw. Netzwerkangreifer:** Man-in-the-Middle, manipulierte IMAP-Antworten.
3. **Lokaler Nutzer ohne Adminrechte bzw. Schadsoftware im Benutzerkontext:** Kann Ordner austauschen, Verknüpfungen setzen, Dateien ablegen.
4. **Administrator (vertrauenswürdig, fehleranfällig):** Konfiguriert Ordner, Katalog und Konten; kann sich vertun.
5. **Support (berechtigt, aber nicht für Bestelldaten):** Erhält Diagnoseberichte.

## Vertrauensgrenzen und Datenflüsse

```
[Internet/Absender] --Mail--> [Mailserver] --IMAP/TLS--> (G1) [ingest.imap]
   --Rohbytes--> (G2) [ingest.mime + ingest.attachments] --bereinigter Text--> [parsers]
   --> [Auftrag in SQLite] --Freigabe--> [export.adapter/validator]
   --Datei--> (G3) [Lexware-Importordner] --manueller Import--> [Lexware]
[Admin] --Einstellungen/Katalog/Referenz-XML--> (G4) [config, catalog, safe_xml]
[App] --Protokoll/Absturz/Diagnose--> (G5) [Logdateien, Support]
[App] --Passwort--> (G6) [Windows-Anmeldeinformationsverwaltung]
```

## Bedrohungen und Maßnahmen

| ID | Grenze | STRIDE | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|---|---|
| T-01 | G1 | S, I | MitM liest Passwort/Mails | Nur implizites TLS oder STARTTLS vor Login; TLS ≥ 1.2; Zertifikats- und Hostnamenprüfung fest, nicht abschaltbar; Login nur über TLS-Socket | `test_imap_security.py` |
| T-02 | G1 | D | Hängende Verbindung, Server sendet Riesenmail | Timeouts; Größe vor Download; Teilabruf bis Limit; begrenzter Backoff | `test_oversized_mail_is_never_downloaded`, `test_retries_are_bounded` |
| T-03 | G1 | I | Passwort in Fehlermeldung/Log | Exakte Entfernung registrierter Geheimnisse; Redaktion; keine Wiederholung bei Anmeldefehlern | `test_authentication_failure_hides_password…` |
| T-04 | G2 | D | MIME-Bombe (Tiefe, Teile, Header), Riesenmail | Vorprüfung auf Bytes; Grenzen für Größe, Kopfbereich, Header, Teile, Tiefe; iterativer Durchlauf | `test_resource_limits`, `test_deep_nesting…` |
| T-05 | G2 | T, D | Malformed MIME, ungültige Zeichensätze, Base64-Fehler bringen Parser zum Absturz | `compat32`-Parser; jeder Fehler wird zu `MailRejected`; Zeichensatz-Allowlist | Mutations-Fuzzing (400 Varianten) |
| T-06 | G2 | T, S | Header-Injection (CR/LF in Werten), gefälschte/mehrdeutige Absender (CVE-2023-27043-Klasse) | Header bereinigt (CR/LF, Steuer-, Bidi-Zeichen), gekürzt; Absender strikt, mehrdeutig → leer + Prüfbefund | `test_header_injection…`, `test_ambiguous…` |
| T-07 | G2 | E | Anhang führt Code aus (Programm, Makro, Skript, LNK) | Nie öffnen; Magic Bytes + Endung; Sperrlisten; Archive nie entpacken | `test_dangerous_attachments_are_blocked` |
| T-08 | G2 | D | ZIP-Bombe, Pfad im Archiv, verschachtelte Archive, gefälschte Größen | Prüfung nur des Verzeichnisses; Einträge, Größe, Kompressionsrate, Pfade, Duplikate, Verschlüsselung; gestreamtes Lesen mit Limit | `bombe.xlsx`, `pfad.xlsx`, `test_streaming_read…` |
| T-09 | G2 | E, I | PDF mit JavaScript, Launch, eingebetteten Dateien (auch verschleiert `#61`) | Namensnormalisierung, Sperre aktiver Inhalte, Seitenlimit; PDFs werden nie gerendert | PDF-Fälle |
| T-10 | G2 | S | Täuschende Dateinamen (`rechnung\u202efdp.exe`, `../`, `CON`) | Name nie als Pfad; Bereinigung; Bidi → gesperrt; Ablage nur per Hash | `test_malicious_filenames`, `test_safe_filename` |
| T-11 | G2 | D | Regex-Überlast durch lange Zeilen | Text- und Zeilenlimits; Messung aller ≥ 90 Muster; quadratische Muster entschärft | `test_regex_dos.py` |
| T-12 | G2 | I | HTML-Mail lädt Inhalte nach, Skripte | HTML wird nur in Text gewandelt; Script/Style verworfen; nie gerendert (GUI-Pflicht) | `test_html_is_reduced_to_text` |
| T-13 | G3 | T | Exportordner per Symlink/Junction umgeleitet, ausgetauscht, anderes Laufwerk | Ordnerprüfung (Verknüpfung, Systemordner, welt-beschreibbar, Netz nur nach Freigabe); Pinning von Gerät und Inode; POSIX-Schreiben über Ordner-Handle ohne Symlink-Folge | `test_replaced_or_symlinked_export_folder…` |
| T-14 | G3 | T | Vorhandene Datei oder Symlink unter Zielnamen wird überschrieben bzw. verfolgt | Neu anlegen ohne Ersetzen (Hardlink/rename), `O_EXCL`, `O_NOFOLLOW` | `test_existing_symlink_at_target_name…` |
| T-15 | G3 | T | Testexport landet im echten Importordner | Getrennte Ordner erzwungen (gleich, darin, darüber) | `test_test_export_never_uses_the_lexware_folder` |
| T-16 | G3 | T | XML-Injection über Auftragsdaten | CDATA mit `]]>`-Aufteilung bzw. Entitäten; Strukturprüfung; Rückabgleich | `test_injected_markup_stays_data…` |
| T-17 | G4 | D, I | XXE, Billion Laughs, externe DTD, Umgehung per UTF-16 | `defusedxml` mit verbotener DTD; Prolog-Prüfung; nur 8-Bit-Kodierungen; Größenlimit | `test_xml_safety.py` |
| T-18 | G4 | D | Riesiger/tief verschachtelter Katalog | Größe, Zeilen, Feldlänge, JSON-Rekursion | Katalogtests |
| T-19 | G5 | I | PII oder Geheimnisse in Logs, Absturzberichten, Support-Berichten | Redaktion (Mail, Telefon, IBAN, Konto, Karte, Anschrift, PLZ/Ort); Längenbegrenzung; Absturzbericht ohne Variablen; Diagnosebericht nur Zählwerte | `test_privacy_and_credentials.py` |
| T-20 | G6 | I | Passwort im Klartext in Dateien oder Umgebung | Windows-Anmeldeinformationsverwaltung; unsichere Backends abgelehnt; Umgebungsvariable nur nach ausdrücklicher Wahl; Konfiguration lehnt Geheimnisse ab | `test_environment_fallback…`, `test_configuration_never_accepts_secrets` |
| T-21 | alle | E | Bekannte Lücken in Interpreter, expat, Paketen | Laufzeitprüfung (Python ≥ 3.12.6, expat ≥ 2.7.1); pip-audit; ungenutzte Parser entfernt | `test_runtime_check…`, Audit |

## Restrisiken (bewusst akzeptiert oder offen)

- **Windows ohne Ordner-Handles:** Zwischen Prüfung und `rename` könnte ein lokaler Angreifer im Benutzerkontext eine Junction einsetzen. Gemindert durch Prüfung unmittelbar davor und Pinning; vollständig nur über Handle-basierte Windows-APIs lösbar.
- **Personennamen** sind per Muster nicht erkennbar. Mailinhalte werden deshalb grundsätzlich nicht protokolliert; Protokollzeilen sind längenbegrenzt.
- **Datenbank** enthält Rohmails und Aufträge unverschlüsselt im Benutzerprofil. Löschkonzept und Speicherfristen stehen aus (Iteration 5/6); Empfehlung: BitLocker.
- **Lokaler Administrator oder Schadsoftware mit Benutzerrechten** kann Anmeldeinformationen des Benutzers lesen; außerhalb des Bedrohungsmodells einer Desktop-Anwendung.
