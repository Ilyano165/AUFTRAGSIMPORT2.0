# Lexware-Integrationstest

**Status: Exportadapter implementiert – Zielsystemvalidierung ausstehend.** Bis dieser Test bestanden und protokolliert ist, darf das Produkt nicht als „Lexware-kompatibel“ bezeichnet werden. Auch danach gilt die Aussage nur für das getestete Lexware-Produkt in der getesteten Version.

Diese Datei wird erzeugt (`python tools/lexware_testpaket.py`); die erwarteten Dateien liegen in `integration/lexware/cases/<Test>/`.

## Voraussetzungen

1. Windows-Rechner mit der Lexware-Version des Kunden; Datensicherung vor dem Test.
2. Eigener **Testmandant**, niemals der Produktivmandant.
3. Stammartikel anlegen: TEST-ART-1 (19 %, 10,00), TEST-ART-2 (19 %, 5,50), TEST-ART-3 (7 %, 24,90). TEST-ART-3 als Lagerartikel mit Bestand 5 (Test 10).
4. Firmeneinstellung „Preise netto/brutto“ notieren (OQ-18).
5. Import: eBusiness → Standard-Shopschnittstelle → Bestellungen importieren aus Datei (Menüpfad je Version prüfen und im Protokoll notieren).
6. Je Test nur die genannten Dateien importieren; Ergebnis sofort protokollieren, Screenshots der Seiten „Kunde“, „Positionen“ und „Summe“ ablegen.

## Protokollkopf

| Angabe | Wert |
|---|---|
| Datum | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Tester | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Lexware-Produkt | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Version / Build | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Testmandant | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Firmeneinstellung Preise (netto/brutto) | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Windows-Version | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Exportadapter | lexware_opentrans 1.0.0 |
| Spezifikation | lexware-opentrans-order 1.0.0 (sha256 02f10caf2ec9…) |

## Testfälle

### Test 01: Einfache Bestellung

| Feld | Inhalt |
|---|---|
| Zweck | Grundfunktion, Kundenanlage, Datumsformat |
| Input | Beleg IT-2026-000001, Bestellnr. IT-01, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000001.xml`, `01b-datum-iso.xml` |
| Produktprüfung | Export zulässig; Hinweise: – |
| Vorgehen | Datei importieren, als Auftrag übernehmen, keinen Kunden zuordnen |
| Lexware result (erwartet) | Auftrag mit 1 Position TEST-ART-1, Menge 1, 10,00 netto, 19 %; Bestellnummer IT-01; Auftragsdatum 01.10.2026; Zahlungsart Rechnung; neuer Kunde angelegt |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-01, OQ-04, OQ-08, OQ-17 |

### Test 02: Mehrere Positionen

| Feld | Inhalt |
|---|---|
| Zweck | Mengen > 1, Zeilensumme, gemischte Steuersätze |
| Input | Beleg IT-2026-000002, Bestellnr. IT-02, 3 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000002.xml` |
| Produktprüfung | Export zulässig; Hinweise: – |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | 3 Positionen: TEST-ART-1 3 × 10,00 = 30,00; TEST-ART-2 2 × 5,50 = 11,00; TEST-ART-3 1 × 24,90 (7 %). Entscheidend: Einzelpreis bleibt 10,00 (nicht 30,00) |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-05, OQ-18 |

### Test 03: Lieferadresse abweichend

| Feld | Inhalt |
|---|---|
| Zweck | BUYER_PARTY als Lieferadresse |
| Input | Beleg IT-2026-000003, Bestellnr. IT-03, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung abweichend, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000003.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_DELIVERY_ADDRESS_BEHAVIOUR: Abweichende Lieferanschrift wird als BUYER_PARTY übergeben; Lexware ergänzt Lieferanschriften bestehender Kunden nur und überschreibt sie nie (OQ-07); EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Einmal ohne Kundenzuordnung, einmal einem bestehenden Kunden mit vorhandener Lieferadresse zuordnen |
| Lexware result (erwartet) | Rechnungsadresse Köln, Lieferadresse Lagerweg 7b, 53783 Eitorf; bei bestehendem Kunden laut LX-SPEC nur Ergänzung leerer Felder – prüfen, welche Lieferadresse im Auftrag steht |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-07 |

### Test 04: Umlaute

| Feld | Inhalt |
|---|---|
| Zweck | Zeichensatz UTF-8 (Empfehlung LX-SPEC) |
| Input | Beleg IT-2026-000004, Bestellnr. IT-04, 1 Position(en), Zahlung Rechnung, Rechnung: Bäckerei Größe & Söhne GmbH, 41061 Mönchengladbach, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000004.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Firma, Name, Straße und Ort exakt mit ä, ö, ü, Ä, Ö, Ü, ß |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-02 |

### Test 04b: Umlaute ISO-8859-1

| Feld | Inhalt |
|---|---|
| Zweck | Zeichensatz der Referenzdatei |
| Input | Beleg IT-2026-00004b, Bestellnr. IT-04b, 1 Position(en), Zahlung Rechnung, Rechnung: Bäckerei Größe & Söhne GmbH, 41061 Mönchengladbach, Lieferung wie Rechnung, ISO-8859-1, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00004b.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Wie Test 04 |
| Lexware result (erwartet) | Identisches Ergebnis wie Test 04 |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-02 |

### Test 05: Sonderzeichen

| Feld | Inhalt |
|---|---|
| Zweck | &, <, >, Anführungszeichen, €, lange Texte (CDATA) |
| Input | Beleg IT-2026-000005, Bestellnr. IT-05, 1 Position(en), Zahlung Rechnung, Rechnung: Müller & Partner <Technik> "Nord" GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000005.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise; EXP_DELIVERY_METHOD_LONG: Versandart ist länger als 32 Zeichen; die Referenz zeigt eine Kürzung (OQ-13) |
| Vorgehen | Importieren und übernehmen; Bemerkung und Versandart prüfen |
| Lexware result (erwartet) | Firmenname exakt wie Eingabe; Bemerkung mit „EUR“ statt €; lange Bemerkung vollständig oder dokumentiert gekürzt; Versandart (40 Zeichen) vollständig oder gekürzt |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-03, OQ-13 |

### Test 05b: Sonderzeichen als Entitäten

| Feld | Inhalt |
|---|---|
| Zweck | Notation laut LX-SPEC 3.1.3 |
| Input | Beleg IT-2026-00005b, Bestellnr. IT-05b, 1 Position(en), Zahlung Rechnung, Rechnung: Müller & Partner <Technik> "Nord" GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation entities (Details: `input.json`) |
| Expected XML | `IT-2026-00005b.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise; EXP_DELIVERY_METHOD_LONG: Versandart ist länger als 32 Zeichen; die Referenz zeigt eine Kürzung (OQ-13) |
| Vorgehen | Wie Test 05 |
| Lexware result (erwartet) | Identisches Ergebnis wie Test 05 |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-03 |

### Test 06: Fehlender Preis

| Feld | Inhalt |
|---|---|
| Zweck | Produkt blockiert; Diagnosedateien klären das Lexware-Verhalten |
| Input | Beleg IT-2026-000006, Bestellnr. IT-06, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `06a-ohne-preis.xml`, `06b-preis-null.xml` |
| Produktprüfung | Export blockiert: EXP_PRICE_MISSING: Position 1: kein Preis aus Mail oder Katalog; das Lexware-Verhalten ohne Preis ist ungeklärt (OQ-06); Hinweise: – |
| Vorgehen | Nur die Diagnosedateien 06a/06b importieren |
| Lexware result (erwartet) | Produkt: kein Export (EXP_PRICE_MISSING). 06a ohne ARTICLE_PRICE, 06b Preis 0,00: beobachten, ob Lexware den Stammpreis nimmt oder 0,00 als manuellen Preis setzt |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-06 |

### Test 07: Versandkosten

| Feld | Inhalt |
|---|---|
| Zweck | REMARK delivery_method und shipping_fee |
| Input | Beleg IT-2026-000007, Bestellnr. IT-07, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000007.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Zweimal importieren: ohne und mit angelegter Nebenleistung „UPS“ |
| Lexware result (erwartet) | Ohne Nebenleistung: Versandart und 8,90 in der Nachbemerkung; mit Nebenleistung „UPS“: eigene Auftragsposition 8,90 |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-11 |

### Test 08a: Zahlungsart Rechnung

| Feld | Inhalt |
|---|---|
| Zweck | PAYMENT-Abbildung |
| Input | Beleg IT-2026-00008a, Bestellnr. IT-08a, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00008a.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Zahlungsart im Auftrag: Rechnung |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | – |

### Test 08b: Zahlungsart Vorkasse

| Feld | Inhalt |
|---|---|
| Zweck | PAYMENT-Abbildung |
| Input | Beleg IT-2026-00008b, Bestellnr. IT-08b, 1 Position(en), Zahlung Vorkasse, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00008b.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Zahlungsart im Auftrag: Vorkasse |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | – |

### Test 08c: Zahlungsart Nachnahme

| Feld | Inhalt |
|---|---|
| Zweck | PAYMENT-Abbildung |
| Input | Beleg IT-2026-00008c, Bestellnr. IT-08c, 1 Position(en), Zahlung Nachnahme, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00008c.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Zahlungsart im Auftrag: Nachnahme |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | – |

### Test 08d: Zahlungsart Barzahlung

| Feld | Inhalt |
|---|---|
| Zweck | PAYMENT-Abbildung |
| Input | Beleg IT-2026-00008d, Bestellnr. IT-08d, 1 Position(en), Zahlung Barzahlung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00008d.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Zahlungsart im Auftrag: Barzahlung |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | – |

### Test 08e: Zahlungsart Bankeinzug

| Feld | Inhalt |
|---|---|
| Zweck | PAYMENT-Abbildung |
| Input | Beleg IT-2026-00008e, Bestellnr. IT-08e, 1 Position(en), Zahlung Lastschrift, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-00008e.xml` |
| Produktprüfung | Export zulässig; Hinweise: EXP_NO_BANK_DATA: Bankeinzug wird ohne Bankdaten übergeben; das Lastschriftmandat muss in Lexware hinterlegt sein (OQ-12); EXP_PRICE_FROM_CATALOG: Preis aus dem Artikelkatalog für Position(en) 1; Lexware übernimmt Preise aus der Datei als manuelle Positionspreise |
| Vorgehen | Importieren und übernehmen |
| Lexware result (erwartet) | Zahlungsart im Auftrag: Bankverbindung ohne Bankdaten |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-12 |

### Test 09: Unbekannte Artikelnummer

| Feld | Inhalt |
|---|---|
| Zweck | Produkt blockiert; Diagnosedatei zeigt Lexware-Verhalten |
| Input | Beleg IT-2026-000009, Bestellnr. IT-09, 1 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `09-unbekannt.xml` |
| Produktprüfung | Export blockiert: EXP_ARTICLE_UNRESOLVED: Position 1: kein Katalogartikel zugeordnet; Lexware würde die Position verwerfen; Hinweise: – |
| Vorgehen | Nur Diagnosedatei 09-unbekannt.xml importieren |
| Lexware result (erwartet) | Produkt: kein Export (EXP_ARTICLE_UNRESOLVED). Lexware laut LX-SPEC: Meldung „ist nicht als Stammartikel vorhanden“; Rückfrage oder Abbruch dokumentieren |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-19 |

### Test 10: Große Bestellung

| Feld | Inhalt |
|---|---|
| Zweck | 120 Positionen, Laufzeit, Vollständigkeit |
| Input | Beleg IT-2026-000010, Bestellnr. IT-10, 120 Position(en), Zahlung Rechnung, Rechnung: Testkunde Lexware GmbH, 50667 Köln, Lieferung wie Rechnung, UTF-8, Notation cdata (Details: `input.json`) |
| Expected XML | `IT-2026-000010.xml` |
| Produktprüfung | Export zulässig; Hinweise: – |
| Vorgehen | Importieren, Zeit messen, Positionsanzahl und Summe prüfen |
| Lexware result (erwartet) | 120 Positionen in beliebiger Reihenfolge (LX-SPEC), Summe laut Erwartungsdatei; bei Lagerartikeln ggf. Rückfrage zu negativem Bestand |
| Observed result | &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
| Pass/Fail | ☐ Pass ☐ Fail |
| Offene Fragen | OQ-20 |

## Zusammenfassung

| Test | Pass/Fail | Bemerkung |
|---|---|---|
| 01 | | |
| 02 | | |
| 03 | | |
| 04 | | |
| 04b | | |
| 05 | | |
| 05b | | |
| 06 | | |
| 07 | | |
| 08a | | |
| 08b | | |
| 08c | | |
| 08d | | |
| 08e | | |
| 09 | | |
| 10 | | |

## Freigabe

Erst wenn alle Tests bestanden oder Abweichungen geklärt und im Adapter umgesetzt sind, trägt ein Administrator im Importprofil `target_system` (Produkt und Version) und `target_validated_on` (Datum) ein. Ohne diese Angaben verweigert die Software den Produktivexport; Vorschau, Probelauf und Testexport bleiben möglich.
