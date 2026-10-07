# Mail-Abruf

Stand: Version 2.0 (unveröffentlicht). Unterstützte Anbieter und deren Teststatus:
[`mail-anbieter.md`](mail-anbieter.md).

## Kurzfassung

- **Aufträge abrufen** (Knopf oder F5) liest das Postfach des aktiven Firmenprofils und legt für
  jede Bestellmail einen Auftrag an. Der Abruf läuft im Hintergrund, die Oberfläche bleibt bedienbar.
- **Das Postfach wird nie verändert.** Keine Mail wird als gelesen markiert, verschoben oder
  gelöscht. Archiviert wird erst im späteren Exportablauf (noch nicht umgesetzt).
- **Kein Auftrag doppelt:** Bereits bekannte Mails werden ohne erneuten Download übersprungen.
- **Passwörter** liegen ausschließlich in der Windows-Anmeldeinformationsverwaltung.
- **Microsoft 365 wird nicht unterstützt**, weil Microsoft für IMAP nur noch OAuth2 zulässt.

## 1. Postfach einrichten

Einstellungen (Strg+,) → **Postfächer** → **Neu**.

| Feld | Bedeutung |
|---|---|
| Name | Anzeigename; daraus entsteht die Kennung (z. B. „Bestellungen Köln“ → `bestellungen-koeln`) |
| Server | IMAP-Server des Anbieters, z. B. `imap.ionos.de` |
| Verschlüsselung | **SSL/TLS** (Port 993, Standard) oder **STARTTLS** (Port 143). Unverschlüsselt ist nicht möglich. Beim Umschalten wechselt der Port mit. |
| Benutzername | meist die vollständige Mailadresse |
| Passwort | siehe Abschnitt 2 |
| Ordner | Standard `INBOX`; Unterordner und Umlaute (z. B. `Aufträge`) sind möglich |
| Erlaubte Absender | leer = alle; sonst Adressen oder Domains, z. B. `@kunde.de, einkauf@firma.de` |
| Höchstens je Abruf | Obergrenze neuer Mails pro Abruf (Rest beim nächsten Abruf), Standard 200 |
| Postfach aktiv | inaktive Postfächer werden nicht abgerufen |

Danach im Firmenprofil (Einstellungen → Firmenprofile → **Mailkonto**) das Postfach zuordnen.
Abgerufen wird immer das Postfach des **aktiven** Profils. Ein Postfach, das ein Profil nutzt,
kann nicht gelöscht werden.

## 2. Passwortverwaltung

- Das Passwort wird beim Speichern direkt in die **Windows-Anmeldeinformationsverwaltung**
  geschrieben (Eintrag `IC-Ware/Auftrags-Import/<Kennung>`), nie in `settings.json`, nie in
  Protokolle oder Fehlerberichte.
- Ein gespeichertes Passwort wird **nie angezeigt**. Die Seite zeigt nur „Passwort ist gespeichert“.
- Passwortfeld **leer lassen = unverändert**. Neues Passwort eintippen = ersetzen.
- Beim Löschen eines Postfachs wird sein Passwort mitgelöscht.
- Ist die Anmeldeinformationsverwaltung nicht nutzbar, startet das Programm trotzdem. Die Seite
  zeigt „Windows-Anmeldeinformationsverwaltung nicht verfügbar“, der Abruf meldet
  „Kein Passwort für das Postfach verfügbar“.
- Viele Anbieter verlangen statt des normalen Passworts ein **App-Passwort** bzw. ein eigenes
  Mail-Passwort (Gmail, Yahoo, T-Online, GMX/Web.de mit Zwei-Faktor). Siehe `mail-anbieter.md`.
- Nur für Sonderfälle (Dienstbetrieb ohne Anmeldeinformationsverwaltung) kann in `settings.json`
  pro Postfach `"credential_source": "environment"` gesetzt werden. Das Passwort kommt dann aus
  der Umgebungsvariable `ICW_SECRET_<KENNUNG>` (Kennung in Großbuchstaben, `-` wird `_`, z. B.
  `ICW_SECRET_BESTELLUNGEN_KOELN`). In der Oberfläche ist das bewusst nicht einstellbar.

## 3. Verbindung testen

**Verbindung testen** meldet sich an, öffnet den Ordner **nur lesend** und zählt die Nachrichten.
Es wird keine Mail geladen, markiert, verschoben oder gelöscht.

- Ist ein Passwort eingetippt, wird dieses getestet (noch ohne Speichern), sonst das gespeicherte.
- Der Test macht **genau einen Versuch** (höchstens 30 Sekunden) und meldet Fehler sofort, statt
  wie der Abruf mehrfach zu wiederholen.
- Erfolg: „Verbindung erfolgreich: 16 Nachrichten im Ordner „INBOX“. Es wurde nichts verändert.“

## 4. Aufträge abrufen

### Ablauf

```
verbinden → Ordner lesend öffnen → Nachrichten auflisten → bekannte UIDs überspringen
→ Kopfdaten → Vorprüfung (Größe, Spam, automatische Antwort, Absenderfilter)
→ Mail laden (BODY.PEEK) → MIME einlesen → Text wählen → Bestellung erkennen
→ Duplikate prüfen → validieren → speichern (eine Transaktion je Mail)
```

Während des Abrufs ist der Knopf gesperrt („Abruf läuft …“), **Abbrechen** erscheint, die
Statusleiste zeigt Fortschritt („Nachricht 3 von 18“). Danach aktualisieren sich Liste und Zähler,
und eine Zusammenfassung erscheint, etwa:

```
16 Nachrichten geprüft
10 Bestellungen erkannt
   davon 10 zur Prüfung
1 bereits bekannt
1 wegen Absenderfilter verworfen
2 als Spam markiert
1 automatische Antwort
1 nicht lesbar (Details im Protokoll)
```

Die Zusammenfassung enthält nie Betreffzeilen, Absender oder Inhalte.

Statusleiste: **nicht verbunden**, **Abruf läuft …**, **Abruf abgeschlossen · 10:42 · 4 neu**,
**Abruf fehlgeschlagen**, **Abruf abgebrochen**.

### Welche Mails gelesen werden

Alle **nicht gelöschten** Mails des Ordners, nicht nur ungelesene. Grund: Öffnet ein Kollege eine
Bestellung in Outlook, gilt sie als gelesen und wäre sonst für den Import unsichtbar. Da der Abruf
selbst nichts als gelesen markiert, brächte ein „ungelesen“-Filter keinen Nutzen. Bereits
gespeicherte Mails erkennt der Abruf an Ordner, UIDVALIDITY und UID und lädt sie nicht erneut.

### Das Postfach bleibt unverändert

Der Ordner wird mit `EXAMINE` (schreibgeschützt) geöffnet, Mails werden mit `BODY.PEEK` geladen.
Beides verhindert unabhängig voneinander jede Markierung. Gegen einen echten IMAP-Server getestet:
Die Markierungen aller Mails sind nach Abruf, Verbindungstest und Abbruch byte-gleich. Eine
Gegenprobe ohne diese Schutzmechanismen setzt sofort `\Seen` und lässt den Test scheitern.

### Vorprüfung (vor dem Download)

| Prüfung | Regel | Gespeichert? |
|---|---|---|
| Größenlimit | Mails über 25 MiB (Servergröße) werden nicht geladen | nein |
| Spam | **nur Markierungen des Mailservers**: Junk-Markierung (`$Junk`, `Junk`; `$NotJunk` hebt auf), `X-Spam-Flag: YES`, `X-Spam-Status: Yes`, `X-Spam: yes`, Microsoft-Spamfilter (`SFV:SPM`, SCL ≥ 5), Betreff-Kennung `[SPAM]` / `***SPAM***` | nein |
| Automatische Antwort | `Auto-Submitted: auto-replied`, `X-Autoreply`, `X-Autorespond`, Zustellberichte (`multipart/report`, `MAILER-DAEMON@`, `postmaster@`) | nein |
| Absenderfilter | „Erlaubte Absender“ des Postfachs und Absenderregeln des Profils (Ignorieren) | nein |

Eine eigene inhaltliche Spam-Bewertung gibt es bewusst nicht: Ein fälschlich aussortierter
Auftrag kostet mehr als eine Werbemail in der Prüfliste. `Auto-Submitted: auto-generated` und
`Precedence: bulk` werden **nicht** aussortiert, weil Bestellbenachrichtigungen aus Webshops sie
tragen. Vorgeprüfte Mails werden nicht gespeichert (Datensparsamkeit) und bei jedem Abruf neu
bewertet, damit geänderte Regeln sofort wirken. Sie erscheinen deshalb bei jedem Abruf erneut in
der Zusammenfassung.

### Duplikate

| Fall | Ergebnis |
|---|---|
| Gleiche Mail (Konto, Ordner, UIDVALIDITY, UID) | übersprungen, ohne Download |
| Gleiche Message-ID (anderer Ordner, neue UIDVALIDITY, erneut zugestellt) | als Duplikat gespeichert, kein Auftrag, „bereits bekannt“ |
| Gleicher Inhalt, neue Message-ID | Auftrag wird angelegt, aber **zur Prüfung** mit Hinweis „Mail mit identischem Inhalt bereits verarbeitet“ |
| Gleiche Bestellnummer des Kunden | Auftrag mit Hinweis „Bestellnummer bereits erfasst“ |

Der Betreff wird für die Duplikaterkennung nie verwendet.

### Was gespeichert wird

Je Mail in **einer** Datenbanktransaktion: Mail (mit Rohdaten), Auftrag, Idempotenzschlüssel und
Journaleintrag (nur Kennungen). Ein Fehler oder Abbruch hinterlässt keine halben Daten.

- Bestellmails von zugelassenen Absendern werden **nie still verworfen**, auch wenn die Erkennung
  unsicher ist. Sie landen zur Prüfung; dort gibt es „Keine Bestellung“.
- Unlesbare Mails (beschädigtes MIME) werden mit Rohdaten als „fehlgeschlagen“ gespeichert, damit
  der Support sie prüfen kann, und nicht bei jedem Abruf erneut versucht.
- Text oder HTML: Der Textteil wird verwendet, außer er ist nur ein Platzhalter („Bitte
  HTML-Ansicht verwenden“) neben deutlich längerem HTML.
- Anhänge werden auf Gefahren geprüft (gesperrte Typen, aktive PDF-Inhalte, Makros). Ein
  gesperrter Anhang wird gemeldet, der Auftrag trotzdem aus dem Mailtext angelegt.
  **PDF-, XLSX- und CSV-Inhalte werden nicht ausgewertet.**

### Ungespeicherte Änderungen

Ein Abruf aktualisiert Liste und Zähler, lädt den gerade geöffneten Auftrag aber **nicht** neu.
Ungespeicherte Änderungen bleiben erhalten und werden nicht gespeichert (getestet).

## 5. Abbrechen

**Abbrechen** wirkt zwischen zwei Mails. Die gerade geladene Mail wird nicht gespeichert, bereits
gespeicherte Aufträge bleiben vollständig, das Postfach bleibt unverändert, der nächste Abruf
holt den Rest ohne Duplikate (gegen echten Server getestet).

Während des Verbindungsaufbaus kann die Verbindung erst nach Ablauf des Zeitlimits (bis 30 s)
beendet werden; danach folgt kein weiterer Versuch. Schließen des Fensters während eines Abrufs
bricht ab und beendet den Hintergrund-Thread sauber.

## 6. Fehlerbehandlung

Fehler **einer Mail** werden gezählt, im Protokoll mit UID und Fehlercode vermerkt, und der Abruf
läuft weiter. Fehler der **Verbindung oder Datenbank** beenden den Abruf sauber; bis dahin
gespeicherte Aufträge bleiben erhalten.

| Meldung | Art | Was tun |
|---|---|---|
| Postfach nicht vollständig eingerichtet | Abruf startet nicht | Server, Benutzer, Ordner eintragen; Postfach dem Profil zuordnen |
| Kein Passwort für das Postfach verfügbar | beendet den Abruf vor der Verbindung | Passwort unter Einstellungen → Postfächer speichern |
| Anmeldeverfahren wird noch nicht unterstützt | Abruf startet nicht | OAuth2-Postfach (Microsoft 365); Postfach mit Passwortanmeldung verwenden |
| IMAP-Verbindung fehlgeschlagen | beendet den Abruf | Netzwerk und Servername prüfen |
| Anmeldung am Mailserver fehlgeschlagen | beendet den Abruf | Benutzer und Passwort prüfen; ggf. App-Passwort |
| Sichere Verbindung (TLS) fehlgeschlagen | beendet den Abruf | Servername prüfen; die Zertifikatsprüfung bleibt immer aktiv |
| Zeitüberschreitung: Der Mailserver antwortet nicht | beendet den Abruf nach Wiederholungen | Netzwerk prüfen, später erneut |
| Postfachordner nicht erreichbar | beendet den Abruf | Ordnernamen prüfen |
| Der Mailserver meldet einen Fehler | beendet den Abruf | später erneut; bei Wiederholung Support |
| Nachricht konnte nicht gelesen werden | eine Mail | Mail bleibt unverändert im Postfach |
| Anhang konnte nicht verarbeitet werden | eine Mail | Auftrag ist trotzdem angelegt |
| Bestellung konnte nicht ausgewertet werden | eine Mail | Mail ist gespeichert; Support |
| Datenbankfehler | beendet den Abruf | Programm neu starten; Support |

Bei Anmelde-, TLS-, Ordner- und Einrichtungsfehlern bietet die Zusammenfassung den Knopf
**Postfach-Einstellungen**. Technische Fehlertexte erscheinen nie in der Oberfläche, nur im
Protokoll (ohne Passwort, ohne Inhalte, ohne Absender).

Bei Netzwerkfehlern und Zeitüberschreitungen macht der Abruf bis zu vier Versuche mit wachsender
Wartezeit; die Statusleiste zeigt „Verbindung unterbrochen, neuer Versuch in … s“.

## 7. Testmodus

```
AuftragsImport.exe --demo
AuftragsImport.exe --demo --testpostfach C:\Testmails
```

- `--demo` startet mit einem Demo-Bestand und einem **Testpostfach** (Ordner mit `.eml`-Dateien).
  „Aufträge abrufen“ läuft durch **denselben Abrufweg** wie ein echtes Postfach.
- `--testpostfach DIR` liest die `.eml`-Dateien aus `DIR`. Nur zusammen mit `--demo` erlaubt,
  damit Testmails nie im echten Auftragsbestand landen oder exportiert werden.
- Das Testpostfach öffnet Dateien nur zum Lesen und verändert, verschiebt oder löscht nichts.
- Unterschied zum echten Postfach: Dateien tragen keine IMAP-Markierungen. Eine nur per
  Junk-Markierung als Spam gekennzeichnete Mail wird im Testmodus zum Auftrag.

## 8. Testsystem (Entwicklung)

| Bestandteil | Zweck |
|---|---|
| `tests/imapserver.py` | Lokaler Dovecot-IMAP-Server mit eigener Test-CA, SSL/TLS und STARTTLS, frisches Postfach je Test |
| `tests/mailcorpus.py` | 16 reproduzierbare Testmails mit erwartetem Ergebnis (Text, HTML, Multipart, iPhone, PDF, XLSX/CSV, Spam per Kopfzeile und Markierung, Abwesenheitsnotiz, unbekannter Absender, Absenderfilter, ungewöhnliches und beschädigtes MIME, zwei Adressen, Umlaute/Windows-1252, doppelte Message-ID) |
| `tools/imap_testserver.py` | Testserver mit diesem Bestand für manuelle Tests starten; `--eml DIR` schreibt den Bestand für `--testpostfach` |
| `tests/integration/test_imap_dovecot.py` | Abruf gegen den echten Server über den Produktionscode |
| `tests/integration/test_imap_providers.py` | Nur-lesende Live-Tests gegen echte Anbieter, nur mit Zugangsdaten aus Umgebungsvariablen |
| `tests/gui/test_accounts_page.py`, `tests/gui/test_fetch_gui.py` | Oberfläche mit echtem Hintergrund-Thread, auch gegen den echten Server |

Dovecot-Tests laufen nur unter Linux mit installiertem `dovecot`, `doveadm`, `openssl` und
root-Rechten; sonst werden sie als „NOT TESTED“ übersprungen, nie als bestanden gezählt.
Vertrauen in die Test-CA erhalten die Tests über `SSL_CERT_FILE`; der Produktivcode prüft
Zertifikate unverändert gegen den Systemspeicher.

## 9. Was getestet ist und was nicht

**Getestet (automatisiert, Linux, Python 3.12 und 3.14.4):** alles in diesem Dokument gegen einen
lokalen Dovecot 2.3.21 und gegen Attrappen.

**Nicht getestet:**

- Kein echter Mail-Anbieter (siehe `mail-anbieter.md`).
- **NOT TESTED – WINDOWS REQUIRED:** echte Windows-Anmeldeinformationsverwaltung; TLS über den
  Windows-Zertifikatsspeicher; Virenscanner oder Firmen-Proxys, die TLS aufbrechen; Oberfläche
  unter Windows; Abruf aus dem installierten MSI-Build.
- Sehr große Postfächer (zehntausende Mails).

## 10. Bekannte Grenzen

- **OAuth2 fehlt:** Microsoft 365 / Exchange Online ist nicht nutzbar.
- **Adresserkennung:** Bei Signaturen „Person / Betrieb ohne Rechtsform“ (z. B. „Martin Vogt /
  Gasthaus Lindenhof“) werden Firma und Name vertauscht. Solche Aufträge stehen zur Prüfung
  (Adressen aus Signaturen müssen immer bestätigt werden), die Vertauschung selbst wird aber nicht
  angezeigt. Als bekannter Fehler in der Testsuite festgehalten.
- **HTML-Tabellen** mit reiner Mengenspalte („10“ statt „10 x“ oder „10 Kisten“) werden nicht als
  Positionen erkannt; der Auftrag landet mit „Positionen fehlen“ zur Prüfung.
- Inhalte von PDF-, XLSX- und CSV-Anhängen werden nicht ausgewertet.
- Ein Abruf liest nur das Postfach des aktiven Profils.
- Die meisten Aufträge landen zur Prüfung: Adressen aus Signaturen müssen bestätigt werden, und
  ohne Zahlungsart in Mail oder Profil fehlt diese Angabe.
- Archivieren abgerufener Mails nach dem Export ist noch nicht umgesetzt.
