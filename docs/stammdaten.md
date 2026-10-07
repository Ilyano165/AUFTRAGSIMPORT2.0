# Stammdaten: Firmenprofile und Artikelkatalog

Stand: Version 2.0.0 (Stammdaten-Iteration). Code: `config/schema.py`, `catalog/`,
`services/profile_rules.py`, `services/rematch.py`, `app/workbench.py`, `gui/settings/`.

## Firmenprofile

Ein Profil steht für eine Firma (etwa „Yellotools“, später Firma B, C, D). Es enthält:

| Bereich | Inhalt |
|---|---|
| Firma | Profilname, Profil-ID, Lieferantenadresse (eigene Firma) |
| Postfach & Absender | zugeordnetes Mailkonto, Absenderregeln, Standard für unbekannte Absender |
| Lexware & Export | Lexware-Importpfad, Testordner, Netzlaufwerk ja/nein, Belegpräfix, Netto/Brutto, Zeichensatz, erlaubte Steuersätze, Zielsystemvalidierung |
| Versand & Zahlung | Versandmethoden mit Erkennungsbegriffen und Kosten, freigegebene Zahlungsarten, Standard-Zahlungsart |
| Mailvorlage | Auftragsbestätigung mit Platzhaltern `{firma}`, `{ansprechpartner}`, `{bestellnummer}`, `{belegnummer}`, `{datum}`, `{positionen}`, `{lieferant}` |

### Trennung der Daten

Die Trennung liegt in der Datenbank, nicht nur in der Oberfläche:

- Jeder Auftrag trägt seine Profil-ID (Migration v4). Liste, Export und Freigabe sehen nur das
  aktive Profil; ein Export fremder Aufträge wird abgelehnt.
- Belegnummern zählen je Profil (`document:<profil>:<präfix>:<jahr>`). Ein Profilzähler startet
  nie unter dem gemeinsamen Zähler früherer Versionen, damit keine Nummer doppelt entsteht.
- Kataloge, Versionsnummern und Backups sind je Profil getrennt (Backups in eigenem Unterordner;
  ein Backup eines anderen Profils wird abgelehnt).
- Beim Speichern werden alle Profile gemeinsam geprüft: Zwei Profile dürfen sich weder Export-
  noch Testordner noch ein Postfach teilen.
- Aufträge aus der Zeit vor Mehrprofil-Fähigkeit werden beim ersten Start dem ersten Profil
  zugeordnet und protokolliert.
- Profile mit Aufträgen und das aktive Profil lassen sich nicht löschen.

### Absenderregeln

Muster: genaue Adresse (`einkauf@kunde.de`) oder Domain (`@kunde.de`, gilt auch für
Subdomains, nie für `kunde.de.example`). Reihenfolge: genaue Adresse, dann die längste passende
Domain, sonst der Standard des Profils. Aktionen: als Bestellung verarbeiten, verarbeiten und
immer prüfen, ignorieren. Im Profil-Editor gibt es einen Live-Test.

### Profilprüfungen am Auftrag

Zusätzlich zur Auftragsvalidierung: Auftrag gehört zu anderem Profil (Fehler), Zahlungsart im
Profil nicht freigegeben (Warnung), Versandart im Profil nicht hinterlegt (Warnung).

## Artikelkatalog

### Quellen

| Format | Hinweise |
|---|---|
| CSV / TXT | UTF-8 (mit oder ohne BOM) oder Windows-1252 (ANSI); Trennzeichen `;` `,` Tab `\|` automatisch; Kopfzeile optional. Passt zum ASCII-Export aus Lexware warenwirtschaft und faktura+auftrag. |
| XLSX | eigener, gehärteter Leser (kein openpyxl); Blattauswahl; Zahlen ohne Gleitkomma-Artefakte. Formeln werden mit ihrem gespeicherten Ergebnis gelesen, Datumswerte als Zahl. `.xls`, `.xlsm`, `.ods` werden mit Hinweis abgelehnt. |
| JSON | Liste von Artikeln oder Objekt mit `articles` (eigenes Export- und Backupformat). |

Grenzen: 20 MB, 200 000 Zeilen, 200 Spalten, 2 000 Zeichen je Feld; XLSX-Einträge werden gestreamt
mit Größengrenze gelesen, XML ohne DTD und Entitäten, Verweise im Archiv können den Ordner
`xl/` nicht verlassen.

### Zuordnungsassistent

Felder: Artikelnummer\*, Artikelname\*, Alias (bis zu zwei Spalten, mehrere Werte mit `|` oder
`;`), Preis, Steuersatz, Einheit, aktiv/inaktiv. Vorschläge kommen aus bekannten Spaltennamen,
auch aus Lexware-Exporten („Verkaufspreis brutto“, „Steuerart“, „GTIN“, „Matchcode“) und aus der
zuletzt verwendeten Zuordnung des Profils. Netto/Brutto wird erkannt und über den Steuersatz in die
Preisart des Profils umgerechnet (4 Nachkommastellen, kaufmännisch gerundet). Spalten wie
„gesperrt“ oder „archiviert“ werden umgekehrt gelesen. Die Vorschau zeigt die gelesenen Werte live.

### Validierung

| Befund | Schwere |
|---|---|
| Artikelnummer leer, doppelt (ohne Groß-/Kleinschreibung, Leerzeichen, Bindestriche) | Fehler |
| Artikelname fehlt | Fehler |
| Preis keine Zahl, negativ, über 10 Mio., mehr als 4 Nachkommastellen | Fehler |
| Steuersatz keine Zahl oder nicht im Profil hinterlegt | Fehler |
| aktiv/inaktiv unbekannter Wert | Fehler |
| Bruttopreis ohne Steuersatz (nicht umrechenbar) | Fehler |
| Alias bei mehreren Artikeln, Alias gleich fremder Artikelnummer | Warnung |
| gleicher Name bei mehreren Artikeln, Steuersatz fehlt, Zahl mehrdeutig geschrieben | Warnung |
| Alias im selben Artikel doppelt (einmal übernommen) | Hinweis |

Fehler verhindern den Import. „Validieren“ prüft den aktiven Katalog mit den heutigen
Profilregeln, etwa nach Änderung der erlaubten Steuersätze.

### Import, Backup, Versionen

Ablauf: Lesen → Zuordnen → Prüfen → **Backup der aktiven Version** → eine Datenbanktransaktion
für Version, Artikel, Aktivierung und Protokoll. Scheitert ein Schritt (auch das Backup), bleibt
der aktive Katalog unverändert; es entsteht keine halbe Version. Wurde der Katalog zwischen
Prüfung und Import geändert, wird der Import abgelehnt.

Je Version gespeichert: Versionsnummer (je Profil ab 1), Zeitpunkt, Quelle (Datei und SHA-256),
Art (CSV, Excel, JSON, Wiederherstellung), Anzahl Artikel, verwendete Zuordnung, Bemerkung,
Bezug auf die Vorversion, Backup-Datei und Änderungsstatistik (neu, entfernt, geändert, davon
Preise, unverändert; bis 500 Einzeländerungen mit Vorher/Nachher-Preis).

Backups: JSON je Profil, Dateirechte 0600, atomar geschrieben, mit Prüfsumme über die Artikel;
die letzten 30 je Profil bleiben. Vor jedem Import und Rücksprung automatisch, zusätzlich
manuell. Wiederherstellen erzeugt eine neue, nachvollziehbare Version (Art „Wiederherstellung“).
Veränderte oder fremde Backups werden abgelehnt.

Rücksprung: Versionen → „Diese Version aktivieren“ (vorher Backup der aktiven).

### Export

XLSX (empfohlen; Texte sind Texte, Formeln sind ausgeschlossen, Kopfzeile fixiert mit Filter),
CSV für Excel (UTF-8 mit BOM, Semikolon, Schutz-Hochkomma vor `= + - @`, Tab, Wagenrücklauf und
Vollbreitenvarianten nach OWASP; der eigene Import entfernt das Hochkomma wieder), JSON.

### Matching und Neuzuordnung

Zu jeder Position zeigt „Positionen“ die Zuordnung: Quelle (Mailtext, Artikelnummer, Katalog-
version), Artikelnummer, Name, Match-Methode (Artikelnummer, Alias, exakter Name, Name
normalisiert, ähnlicher Name, manuell), Match-Sicherheit (sicher, hoch, mittel, unsicher mit
Übereinstimmung in Prozent, kein Treffer) und Status (automatisch zugeordnet, manuell zugeordnet,
Benutzerprüfung erforderlich, nicht zugeordnet). Bei unsicheren Treffern stehen Vorschläge zur
Auswahl; „Vorschlag übernehmen“ speichert eine manuelle Zuordnung und bewertet den Auftrag neu.

„Neu zuordnen“ zeigt zuerst, welche Positionen offener Aufträge (Neu, Prüfen, Bereit) sich gegen
den aktiven Katalog ändern würden, auch aktualisierte Preise und Steuersätze. Manuelle
Zuordnungen, freigegebene und exportierte Aufträge bleiben unberührt.

## Offene Punkte

- Der Mail-Abruf ist noch nicht angebunden. Absenderregeln, Profilzuordnung über das Postfach
  und Versandartenerkennung sind umgesetzt und getestet, greifen aber erst mit dem Abruf.
- Der Lexware-Export nutzt Versandmethoden und Mailvorlage noch nicht; die Mailvorlage hat eine
  Vorschau, aber noch keinen Versand.
- Der ältere Katalogpfad `services/catalog.py` besteht für bestehende Tests weiter und soll in
  der nächsten Iteration auf `catalog/` umgestellt werden.
