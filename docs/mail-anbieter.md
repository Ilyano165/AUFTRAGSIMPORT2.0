# Mail-Anbieter: Teststatus und Voraussetzungen

Stand: 2. Oktober 2026. **Ein Anbieter gilt nur als TESTED, wenn ein echter Abruf gegen ein
echtes Postfach dieses Anbieters erfolgreich war.** Recherche zu Voraussetzungen ersetzt keinen Test.

| Status | Bedeutung |
|---|---|
| TESTED | echter Abruf gegen diesen Server erfolgreich, Postfach nachweislich unverändert |
| NOT TESTED | nicht gegen ein echtes Postfach geprüft |
| UNSUPPORTED | mit der aktuellen Version nicht nutzbar |
| REQUIRES OAUTH | Anbieter verlangt OAuth2 (nicht implementiert) |
| REQUIRES APP PASSWORD | normales Passwort wird abgelehnt; App-Passwort bzw. eigenes Mail-Passwort nötig |

## Matrix

| Anbieter | IMAP-Server | Status | Voraussetzungen laut Anbieter (recherchiert, nicht getestet) |
|---|---|---|---|
| Lokaler Testserver (Dovecot 2.3.21, Linux) | localhost | **TESTED** | automatisiert: SSL/TLS, STARTTLS, Anmeldung, Fehlerfälle, Abbruch, Duplikate |
| Microsoft 365 / Exchange Online | outlook.office365.com | **UNSUPPORTED · REQUIRES OAUTH** | Passwortanmeldung für IMAP seit Oktober 2022 abgeschaltet. Die App lehnt OAuth2-Postfächer mit klarer Meldung ab. Keine Umgehung. |
| Gmail (privat) | imap.gmail.com:993 | **NOT TESTED · REQUIRES APP PASSWORD** | Bestätigung in zwei Schritten aktivieren, App-Passwort erzeugen; IMAP ist laut Quellen seit 2025 immer aktiv |
| Google Workspace | imap.gmail.com:993 | **NOT TESTED** | Quellen widersprüchlich, ob App-Passwörter noch verfügbar sind; vor Ort prüfen, ggf. faktisch REQUIRES OAUTH |
| IONOS | imap.ionos.de:993 | **NOT TESTED** | Mailadresse und Postfachpasswort |
| STRATO | imap.strato.de:993 | **NOT TESTED** | Mailadresse und Postfachpasswort |
| all-inkl.com | je Kundenkonto (KAS) | **NOT TESTED** | Servername und Benutzer aus dem KAS übernehmen |
| GMX | imap.gmx.net:993 | **NOT TESTED** · mit Zwei-Faktor: **REQUIRES APP PASSWORD** | IMAP ist standardmäßig aus: Einstellungen → POP3/IMAP Abruf → „POP3 und IMAP Zugriff erlauben“ |
| WEB.DE | imap.web.de:993 | **NOT TESTED** · mit Zwei-Faktor: **REQUIRES APP PASSWORD** | wie GMX: IMAP-Zugriff erst freischalten |
| Yahoo | imap.mail.yahoo.com:993 | **NOT TESTED · REQUIRES APP PASSWORD** | normales Passwort für Fremdprogramme seit 2023 abgeschaltet |
| T-Online | secureimap.t-online.de:993 | **NOT TESTED · REQUIRES APP PASSWORD** | eigenes „Passwort für E-Mail-Programme“ (account.telekom.de → Passwörter); das Kundencenter-Passwort wird abgelehnt |

Serveradressen sind Angaben der Anbieter bzw. aus Anleitungen und nicht durch einen Test bestätigt.

## Typische Fehlerbilder

- **„Anmeldung am Mailserver fehlgeschlagen“ trotz richtigem Passwort:** bei GMX/WEB.DE IMAP nicht
  freigeschaltet; bei T-Online das Kundencenter- statt des Mail-Passworts; bei Gmail/Yahoo kein
  App-Passwort.
- **„Anmeldeverfahren wird noch nicht unterstützt“:** Postfach ist als OAuth2 eingetragen
  (Microsoft 365).
- **„Sichere Verbindung (TLS) fehlgeschlagen“:** Servername stimmt nicht mit dem Zertifikat
  überein, oder ein Virenscanner bzw. Firmen-Proxy bricht TLS auf (NOT TESTED – WINDOWS REQUIRED).

## Live-Test gegen einen Anbieter durchführen

Der Test liest nur: Ordner schreibgeschützt öffnen, höchstens 20 Mails in eine Wegwerf-Datenbank
laden, danach Markierungen vergleichen. Er verändert das Postfach nicht.

```
set ICWARE_IMAP_IONOS_USER=bestellung@kunde.de
set ICWARE_IMAP_IONOS_PASSWORD=...
python -m pytest tests/integration/test_imap_providers.py -k IONOS -rs
```

Kennungen: `IONOS`, `STRATO`, `ALLINKL` (zusätzlich `_HOST`), `GMX`, `WEBDE`, `GMAIL`, `YAHOO`,
`TONLINE`. Optional `_HOST`, `_PORT`, `_FOLDER`, `_STARTTLS=1`. Ohne Zugangsdaten wird der Anbieter
als „NOT TESTED“ übersprungen. Nach einem erfolgreichen Lauf: Status in dieser Tabelle mit Datum,
Version und Prüfer auf TESTED setzen. Die Testlogik selbst ist gegen den lokalen Dovecot geprüft;
das zählt nicht als Anbietertest.
