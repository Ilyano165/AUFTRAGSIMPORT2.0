# Installer, Ablageorte, Protokolle

## Installation

Pro Rechner nach `C:\Program Files\IC-Ware\Auftrags-Import` (Administratorrechte, Windows 10/11 x64).

    msiexec /i IC-Ware-AuftragsImport-2.0.1-x64.msi                          (interaktiv)
    msiexec /i IC-Ware-AuftragsImport-2.0.1-x64.msi /qn /l*v install.log    (still, für Intune/GPO)

Startmenü-Eintrag „IC-Ware Auftrags-Import“ mit derselben App-ID wie das Programm (ein Taskleistensymbol).

## Upgrade

Neue Version einfach installieren (interaktiv oder still). Windows Installer entfernt die alte
Version und installiert die neue (Major Upgrade). Ältere Versionen über eine neuere zu installieren
wird verweigert. Aufträge, Kataloge, Einstellungen und Passwörter bleiben unberührt, weil der
Installer diese Orte nie anlegt oder entfernt. Datenbankschema-Migrationen führt das Programm beim
ersten Start selbst aus (vorwärts, in einer Transaktion).

## Reparatur

Einstellungen → Apps → IC-Ware Auftrags-Import → Erweiterte Optionen → Reparieren, oder

    msiexec /fa IC-Ware-AuftragsImport-2.0.1-x64.msi

Stellt fehlende oder beschädigte Programmdateien wieder her; Benutzerdaten bleiben unverändert.

## Deinstallation

    msiexec /x IC-Ware-AuftragsImport-2.0.1-x64.msi /qn

Entfernt Programmdateien, Startmenü-Eintrag und `HKLM\Software\IC-Ware\Auftrags-Import`.
Benutzerdaten bleiben bewusst erhalten (Auftragsverlauf, Aufbewahrungspflichten). Endgültiges
Löschen nur nach Rücksprache und Prüfung der Aufbewahrungsfristen: die Ordner unten und die
Einträge „IC-Ware/Auftrags-Import/…“ in der Windows-Anmeldeinformationsverwaltung.

## Ablageorte

| Ort | Inhalt | Schreibt |
|---|---|---|
| `C:\Program Files\IC-Ware\Auftrags-Import` | Programm, Lizenztexte, Richtlinien-Beispiel | nur der Installer |
| `%PROGRAMDATA%\IC-Ware\Auftrags-Import\policy.json` | Richtlinie der IT (optional) | Administrator; Benutzer nur lesen |
| `%APPDATA%\IC-Ware\Auftrags-Import` | `settings.json` und Sicherungen davon | Benutzer, wandert mit dem Profil |
| `%LOCALAPPDATA%\IC-Ware\Auftrags-Import` | Datenbank, Katalog-Backups, `logs`, `fehlerberichte`, `diagnostics` | Benutzer, nur dieser Rechner |
| Windows-Anmeldeinformationsverwaltung | Postfach-Passwörter („IC-Ware/Auftrags-Import/<konto>“) | Benutzer |

Die Datenbank liegt nicht im wandernden Profil: servergespeicherte Profile synchronisieren beim
Abmelden und vertragen keine offene SQLite-Datei. Nichts davon liegt neben der EXE; Program Files
ist für Benutzer schreibgeschützt. Die Daten gehören dem Windows-Benutzer: Zwei Benutzer auf
demselben Rechner haben getrennte Datenbestände.

Ältere Testinstallationen mit Datenbank im Roaming-Ordner werden beim ersten Start einmalig
verschoben (nur wenn am neuen Ort noch keine Datenbank liegt; es wird nichts überschrieben).

## Richtlinie der IT

Beispiel im Programmordner unter `docs\Richtlinie-Beispiel.json`. Die IT legt
`%PROGRAMDATA%\IC-Ware\Auftrags-Import\policy.json` an (Ordnerrechte: Administratoren Vollzugriff,
Benutzer Lesen). Inhalt: Support-Kontakt im Fehlerdialog, Protokollstufe, Update-Einstellungen.
Geheimnisse werden abgelehnt. Eine fehlerhafte Richtlinie blockiert den Start nicht; die Probleme
stehen im Protokoll.

## Protokolle und Fehlerberichte

- `logs\auftrags-import.log`, Rotation bei 5 MB, 10 ältere Dateien (höchstens etwa 55 MB).
- Jede Zeile wird vor dem Schreiben bereinigt: Passwörter, Mailadressen, IBAN, Telefonnummern,
  Anschriften, Benutzername in Pfaden. Zeilen sind auf 4 000 Zeichen begrenzt.
- `fehlerberichte\absturz-<Fehler-ID>.txt` bei unerwarteten Fehlern (höchstens 50, älteste werden
  gelöscht): Fehler-ID, Zeitpunkt, Build, Aufrufstapel; keine Variableninhalte, keine Geheimnisse.
- `fehlerberichte\native-abstuerze.log` bei Abstürzen außerhalb von Python (etwa im Grafiktreiber).
