# Oberfläche – Gestaltung und Verhalten

Stand: Version 2.0.0 (GUI-Iteration). Gilt für `src/icware_auftragsimport/gui/` und
`src/icware_auftragsimport/app/presentation.py`.

## Grundsätze

Ruhig, präzise, kompakt. Neutrale Windows-Flächen, eine Akzentfarbe, keine Verläufe, Schatten,
Glas-Effekte, Animationen oder Dashboard-Kacheln. Bedeutung trägt immer Text plus Form, nie nur
Farbe. Begriffe in der Oberfläche sind fachlich („Freigeben“, „Lieferadresse“), nie technisch.

## Farbe und Marke

| Rolle | Wert | Kontrast |
|---|---|---|
| Markenzeichen (nur „IC“-Kachel) | #19E56A auf #111111 | 11,2 : 1 |
| Akzent (Primärknopf, Fokus, aktiver Bereich) | #0B6B3A auf Weiß | 6,61 : 1 |
| Text auf Fensterfläche | #1B1B1B auf #F3F3F3 | 15,5 : 1 |
| Sekundärtext | #5C5C5C auf Weiß | 6,69 : 1 |
| Warnung / Fehler / Info / Erfolg | siehe `gui/theme.py` `TONES` | 5,7–6,2 : 1 |

Das Markengrün #19E56A erreicht auf Weiß nur 1,69 : 1 und wirkt als Flächenfarbe neonartig. Es
erscheint deshalb ausschließlich im Markenzeichen. Alle Werte stehen als Tokens in `gui/theme.py`.

## Aufbau

Titelleiste „IC-Ware | Auftrags-Import“ mit Profil, „Aufträge abrufen“ (während des Abrufs
„Abruf läuft …“ und „Abbrechen“) und „Exportieren (n)“. Die Statusleiste zeigt rechts den
Postfachstatus und während des Abrufs einen Fortschrittsbalken (`docs/mail-abruf.md`).
Darunter die Bereiche mit Zählern und rechts die Suche. Links die Auftragsliste, rechts die
Details; der Teiler ist verschiebbar, Größe und Teilung werden gespeichert.

| Bereich | Enthaltene Status |
|---|---|
| Posteingang | alle offenen: Neu, Prüfen, Bereit, Freigegeben, Export läuft, Fehler, Unklar |
| Bereit | Bereit, Freigegeben |
| Prüfung erforderlich | Neu, Prüfen |
| Erledigt | Exportiert, Ignoriert |
| Fehler | Fehler, Unklar |

## Status

| Status | Zeichen | Ton | Bedeutung |
|---|---|---|---|
| Neu | Ring | Info | eingegangen, noch nicht geprüft |
| Prüfen | Dreieck | Warnung | Angaben fehlen oder sind unsicher |
| Bereit | Punkt | Erfolg | vollständig, kann freigegeben werden |
| Freigegeben | Haken | Erfolg | Belegnummer vergeben, wartet auf Export |
| Export läuft | Uhr | Info | Datei wird geschrieben |
| Exportiert | Haken | Grau | an Lexware übergeben |
| Ignoriert | Strich | Grau | keine Bestellung |
| Fehler / Unklar | Quadrat | Fehler | Export fehlgeschlagen bzw. Ergebnis unklar |

Erledigte Aufträge zeigen keine Probleme mehr an; die Spalte „Probleme“ gilt nur für offene.

## Validierung

Jeder Befund nennt, was fehlt, wo, und was zu tun ist:

    ■ Artikelnummer fehlt
      → Position 4 → Artikel aus dem Katalog auswählen → Benutzeraktion erforderlich

    ■ Lieferadresse unvollständig
      → Hausnummer fehlt → Benutzeraktion erforderlich

Gruppen: „Muss behoben werden“, „Bitte prüfen“, „Hinweise“. Enter oder Doppelklick springt zur
Stelle: zur Position mit geöffnetem Feld bzw. zum markierten Adressfeld. Felder sind rot markiert,
wenn eine Pflichtangabe fehlt, gelb, wenn die Erkennung unsicher ist; der Grund steht im Tooltip.
Die Formulierung liegt Qt-frei in `IssueView.steps` und ist getestet.

## Export

Vorher: „3 Aufträge bereit“ und „0 Aufträge mit Fehlern“, blockierte Aufträge mit Grund, dann
„Export starten“. Danach: Ergebnisübersicht mit Beleg, Kunde, Ergebnis und Datei. Exportiert
werden nur freigegebene Aufträge. Der Produktivexport bleibt gesperrt, bis das Zielsystem
validiert ist; Testexporte sind jederzeit möglich.

## Tastatur

| Taste | Wirkung |
|---|---|
| Strg+F | Suche |
| Esc | Suche leeren, sonst zurück zur Liste; in Dialogen Abbrechen |
| Enter | markierten Auftrag öffnen; in der Validierung zur Stelle springen; in Dialogen Standardknopf |
| Pfeiltasten | Liste, Tabellen, Befunde |
| Tab / Umschalt+Tab | Suche → Bereiche → Liste → Details → Felder und zurück |
| Strg+S | speichern |
| Strg+E | exportieren |
| Strg+1 … Strg+5 | Bereich wechseln |
| Strg+Tab | nächster Detailbereich |
| F2 | Zelle in den Positionen bearbeiten |
| F5 | Aufträge abrufen |
| F1 | Übersicht der Tastenkürzel |

Ungespeicherte Änderungen werden beim Wechsel des Auftrags, des Bereichs, vor dem Export und
beim Schließen abgefragt; die Titelleiste zeigt sie mit „*“.

## High-DPI und Fenstergrößen

- Skalierung ohne Rundung (`PassThrough`), damit 125 % und 150 % exakt so groß sind wie bei
  anderen Windows-Programmen. Mit Rundung würde Qt 150 % auf 200 % aufrunden.
- Stil „Fusion“ mit eigenem Stylesheet: auf Windows 10 und 11 identisch und bei gebrochenen
  Faktoren ohne die bekannten Rahmenfehler des Windows-Stils.
- Statuszeichen und Markenzeichen werden vektoriell gezeichnet und bleiben in jeder Stufe scharf.
- Alle Maße sind geräteunabhängige Pixel; keine festen Pixelbilder.
- Spalten der Liste: „Firma“ behält mindestens 190 px; danach entfallen nacheinander
  Ansprechpartner, Positionen, Bestellnummer.
- Detailbereiche erhalten Kurznamen (Rechnung, Lieferung, Versand, Mail), wenn die vollen Namen
  nicht nebeneinander passen; der volle Name steht im Tooltip.
- Schmale Detailansicht: Aktionsknöpfe rutschen in eine zweite Zeile.
- Unter 980 px Fensterbreite stehen Liste und Details übereinander.

Geprüft mit `tools/gui_screenshots.py` (Offscreen-Rendering, Bilder in `screenshots/`):

| Bildschirm | Skalierung | Logische Fenstergröße |
|---|---|---|
| 1366 × 768 | 100 % | 1366 × 728 |
| 1920 × 1080 | 125 % | 1536 × 824 |
| 1920 × 1080 | 150 % | 1280 × 680 |
| 3840 × 2160 | 200 % | 1920 × 1040 |
| schmales Fenster | 125 % | 940 × 700 |

## Grenzen

- Die Screenshots entstehen unter Linux mit DejaVu Sans; diese Schrift ist breiter als Segoe UI.
  Unter Windows ist mehr Platz. Eine Sichtprüfung auf echten Windows-10/11-Geräten steht aus.
- Befund CI (windows-2022, Offscreen-Rendering): Die Liste blendet dort schon bei 940 px
  „Ansprechpartner“ und selbst bei 1920 × 1040 noch Spalten aus, d. h. die Schrift ist im
  Offscreen-Modus unter Windows breiter als angenommen. Ob das auch im normalen Fensterbetrieb mit
  Segoe UI so ist, klärt erst die Sichtprüfung auf echten Geräten. Der Test prüft unter Windows
  deshalb nur die Ausblendregel (Reihenfolge, Mindestbreite „Firma“) und meldet Schrift und Breite.
- Nur helles Design. Ein dunkles Design ist über die Tokens vorbereitet, aber nicht umgesetzt.
- Ohne `--demo` startet die Oberfläche noch nicht produktiv: Mail-Abruf und Einrichtung werden
  erst mit der nächsten Iteration angebunden.
- Das Standard-Belegpräfix „AI“ liest sich in der Oberfläche wie „Artificial Intelligence“.
  Im Demo wird „AU“ verwendet; eine Änderung des Produkt-Standards ist eine Produktentscheidung.
