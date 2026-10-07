# Security Report – Hardening Pass

Stand: 2026-10-01 · Umfang: gesamter Code von IC-Ware Auftrags-Import 2.0 (Iteration 1–4) sowie die für Iteration 5 benötigten Eingangskomponenten.

## Ergebnis in einem Satz

Alle P0/P1-Befunde im Code sind behoben und durch 238 automatisierte Sicherheitstests abgesichert. Ein P0 bleibt als **Release-Blocker** außerhalb des Codes: Der ausgelieferte Python-Interpreter muss die geprüften Mindeststände erfüllen, und die Anwendung prüft das selbst.

## Methode

1. Code-Inventur aller Eingänge (Mail, Anhänge, Katalog, XML, Einstellungen), Ausgänge (Export, Logs, Berichte) und aller 95 regulären Ausdrücke.
2. Recherche der Laufzeit-CVEs (CPython, libexpat) und Abhängigkeitsaudit mit `pip-audit` gegen die PyPI-Schwachstellendatenbank.
3. Threat Model (siehe `threat-model.md`).
4. Behebung, danach Angriffstests: Mutations-Fuzzing, MIME- und ZIP-Bomben, XXE/Billion Laughs, Symlink-Austausch, TLS-Erzwingung, ReDoS-Messung.

## Befunde

Priorität: **P0** ausnutzbar von außen bzw. Release-Blocker · **P1** erheblich · **P2** Härtung · **P3** Restrisiko/Hinweis.

| ID | P | Befund | Status | Nachweis |
|---|---|---|---|---|
| SEC-01 | P0 | Entwicklungs-Laufzeit Python 3.12.3 mit expat 2.6.1: CVE-2024-6923 (Header-Injection, behoben in 3.12.5), CVE-2023-27043 (Adressauswertung, behoben in 3.12.6), CVE-2024-8176 (expat-Stapelüberlauf, behoben in 2.7.0/2.7.1) | **Teilweise – Release-Blocker.** Laufzeitprüfung `security/runtime.py`, `requires-python >= 3.12.6`. Offen: Release-Build mit aktuellem CPython (Iteration 7) | `test_runtime_check…` |
| SEC-02 | P0 | Kein gehärteter Mail-Parser: keine Grenzen, Rekursion bei tiefer Verschachtelung, Absturz bei Formfehlern möglich | Behoben: `ingest/mime.py` | `test_mail_hardening.py` inkl. Fuzzing |
| SEC-03 | P0 | IMAP-TLS nur in einem Docstring „zugesichert“: beliebige Ports, kein TLS-Modus, keine Zertifikatsvorgaben | Behoben: `TlsMode`, Portprüfung, `ingest/imap.py` | `test_imap_security.py` |
| SEC-04 | P0 | Anhänge ohne Typ-, Größen- und Inhaltsprüfung | Behoben: `ingest/attachments.py` | `test_attachment_limits.py` |
| SEC-05 | P1 | Referenzanalyse und Validator nutzten den Standard-XML-Parser; DOCTYPE-Erkennung per Bytesuche war mit UTF-16 umgehbar | Behoben: `security/safe_xml.py` (defusedxml, Prolog- und Kodierungsprüfung) | `test_xml_safety.py` |
| SEC-06 | P1 | Exportordner ohne Schutz gegen Symlink/Junction, Systemordner, Netzlaufwerk, Austausch | Behoben: `security/fs.py`, Pinning (Migration v3), Schreiben über Ordner-Handle (POSIX) | `test_filesystem_safety.py` |
| SEC-07 | P1 | Keine Längenbegrenzung vor der Regex-Erkennung; `_INLINE_NUMBER` quadratisch | Behoben: Text-/Zeilenlimits, Lookbehind; alle Muster gemessen | `test_regex_dos.py` |
| SEC-08 | P1 | Redaktion lückenhaft: Anschriften, PLZ/Ort, Kontonummer/BLZ, Telefon „0221 123456“; keine Längenbegrenzung | Behoben: `security/redaction.py`, `logging_setup.py` | `test_redaction_covers…`, `test_logs_never…` |
| SEC-09 | P1 | Bekannte Passwörter nicht gezielt entfernt; kein bereinigter Absturzbericht | Behoben: `register_secret`, `security/crash.py` | `test_crash_reports…` |
| SEC-10 | P1 | Support-Diagnosebericht fehlte; Gefahr, Logs mit Bestelldaten weiterzugeben | Behoben: `services/diagnostics.py` (nur Zählwerte, keine Texte, Pfade, Hosts) | `test_support_report…` |
| SEC-11 | P1 | Katalogimport ohne Größen-, Zeilen-, Feldlimit; JSON-Rekursion | Behoben | bestehende Katalogtests + Limits |
| SEC-12 | P1 | `pypdf>=6.7` erlaubte eine Version mit 15 bekannten Lücken (behoben erst ab 6.16.1); `pypdf`/`openpyxl` ungenutzt; `defusedxml` nur optional | Behoben: entfernt bzw. Pflichtabhängigkeit, exakte Pins | pip-audit: keine bekannten Lücken |
| SEC-13 | P2 | Unicode-Täuschung (Bidi, Nullbreite) in Dateinamen und Headern | Behoben: `security/sanitize.py`; Anhänge mit versteckten Zeichen gesperrt | Dateinamen- und Header-Tests |
| SEC-14 | P2 | Message-ID mit Leerzeichen wurde „repariert“ statt ersetzt (Kollisionsgefahr) | Behoben (vom eigenen Test gefunden) | `test_invalid_message_id…` |
| SEC-15 | P2 | Exportdateien und Zwischendateien mit Standardrechten (POSIX) | Behoben: 0600 | `test_existing_symlink…` |
| SEC-16 | P3 | `_PRICE`-Muster bleibt quadratisch, durch Zeilenlimit auf ≈15 ms je Suche begrenzt | Akzeptiert | `test_regex_dos.py` |
| SEC-17 | P3 | Windows: kein Ordner-Handle-Schreiben; theoretisches Junction-Rennen | Akzeptiert, siehe Threat Model | – |
| SEC-18 | P3 | Rohmails/PII unverschlüsselt in SQLite; Löschkonzept fehlt | Offen (Iteration 5/6) | – |
| SEC-19 | P3 | Abhängigkeiten ohne Hash-Pinning | Offen (Iteration 7, `pip-compile --generate-hashes`) | – |

### Vom Testlauf in eigenem neuem Code gefundene Fehler

Die Angriffstests haben vier Schwächen in diesem Hardening Pass selbst aufgedeckt, alle behoben: `/EmbeddedFiles` wurde nicht erkannt; `.bat` verlor durch Bereinigung seine Endung; ungültige Message-IDs wurden umgeschrieben statt ersetzt; das häufigste Telefonformat wurde nicht redigiert. Ein weiterer Test hat eine Überredaktion verhindert (Belegnummern blieben für den Support erhalten). Außerdem fiel ein falsch angezeigter OpenSSL-Versionsstand auf (3.0.13 als 3.0.0).

## Laufzeit und Abhängigkeiten

- Entwicklungsumgebung: Python 3.12.3, expat 2.6.1, OpenSSL 3.0.13 → `check_runtime()` meldet zwei Probleme (korrekt).
- Zielvorgabe Release: CPython ≥ 3.12.6 mit expat ≥ 2.7.1; empfohlen die jeweils aktuelle Patchversion. Laut einer Build-Pipeline-Notiz vom September 2026 bündelt CPython 3.12.14 bereits expat 2.8.3.
- OpenSSL: Die Hauptlinie 3.0 (LTS) erreicht nach meinem Kenntnisstand im September 2026 ihr Support-Ende; beim Release-Build die gebündelte OpenSSL-Version prüfen.
- pip-audit (2026-10-01, PyPI-Datenbank): Laufzeitpakete und Entwicklungswerkzeuge ohne bekannte Lücken. Details: `dependencies.md`.

## Pflichten für die nächsten Iterationen

- **Iteration 5 (Ingest):** ausschließlich `ingest.imap.SecureImapClient` und `ingest.mime.parse_mail` verwenden; `check_runtime()` vor jedem Abruf; abgelehnte Mails in Quarantäne (Rohdaten behalten, nie löschen); Anhänge nur per Hash ablegen.
- **Iteration 6 (GUI):** Mailtext nur als Klartext anzeigen (kein HTML-Rendering, keine externen Links, keine Bildnachladung); Absender-/Anhangswarnungen sichtbar; Diagnosebericht-Export nur über `build_support_report`.
- **Iteration 7 (Build):** aktuelle CPython-Laufzeit, Hash-gepinnte Abhängigkeiten, pip-audit in CI, Signatur des Installers, PySide6-LGPL-Pflichten (siehe `dependencies.md`).
