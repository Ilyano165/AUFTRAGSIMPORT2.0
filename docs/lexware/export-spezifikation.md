# Exportspezifikation Lexware openTRANS

**lexware-opentrans-order 1.0.0 – Status: Exportadapter implementiert – Zielsystemvalidierung ausstehend.**

Maßgeblich ist die maschinenlesbare Datei `src/icware_auftragsimport/export/lexware/lexware_opentrans_order_v1.json`. Diese Seite wird daraus erzeugt.

## Quellen

- **REF-248090** (Echte Referenzdatei): integration/lexware/reference/248090.xml. Einzige vorliegende Datei. Herkunft „Shop 2.0“, 10.05.2021. Ob sie erfolgreich importiert wurde, ist nicht belegt (OQ-17).
- **LX-SPEC-2009** (Herstellerdokumentation): Import von Bestellungen im openTRANS-Format in Lexware warenwirtschaft pro/premium. Gilt laut Dokument ab Lexware Warenwirtschaft 9.00; faktura+auftrag kann abweichen.
- **OT-1.0** (Standard): openTRANS 1.0, Kapitel 4.3 ORDER. Von LX-SPEC referenziert; nicht selbst geprüft. Es werden nur Elemente verwendet, die REF oder LX-SPEC belegen.

## Dateiregeln

- Root `ORDER_LIST` ohne Namespace; `ORDER` mit Namespace `http://www.opentrans.org/XMLSchema/1.0` und `xmlns:xsi`.
- Encoding UTF-8 oder ISO-8859-1, Standard UTF-8 (LX-SPEC-2009 3.1.1 empfiehlt UTF-8; REF-248090 verwendet ISO-8859-1; Siehe OQ-02).
- Textwerte als CDATA (REF-248090 schreibt alle Werte als CDATA; LX-SPEC-2009 3.1.3 beschreibt Entitäten; Siehe OQ-03).
- Zeilenende CRLF, Einrückung zwei Leerzeichen, ein Auftrag je Datei.
- „€“ wird durch „EUR“ ersetzt (LX-SPEC 3.1.2); jede Ersetzung steht im Exportbericht.

## Abbildung

| Element | Quelle im Auftrag |
|---|---|
| ORDER_ID | Bestellnummer des Kunden (bestätigt), sonst interne Belegnummer (OQ-14) |
| ORDER_DATE | Bestelldatum, Uhrzeit 00:00:00 (Zeitteil wird von Lexware ignoriert) |
| BUYER_PARTY | Lieferanschrift; bei „wie Rechnungsanschrift“ die Rechnungsanschrift (wie REF) |
| INVOICE_PARTY | Rechnungsanschrift |
| SUPPLIER_PARTY | Lieferantenanschrift des Importprofils (wie REF; von Lexware nicht übernommen) |
| NAME/NAME2/NAME3 | Firma / Nachname / Vorname; Abteilung hat kein Zielfeld |
| STREET | Straße und Hausnummer, durch Leerzeichen getrennt |
| COUNTRY | deutscher Ländername aus dem ISO-Code |
| PAYMENT | Rechnung CASH 10, Vorkasse CASH 25, Nachnahme CASH 52, Barzahlung CASH 56, Lastschrift ACCOUNT 54 ohne Bankdaten |
| REMARK delivery_method/shipping_fee | Versandart und Versandkosten des Auftrags |
| REMARK order | Notiz des Auftrags |
| SUPPLIER_AID | Artikelnummer des zugeordneten Katalogartikels |
| QUANTITY | Menge, Punkt als Dezimaltrenner |
| PRICE_AMOUNT | Einzelpreis (Mail, sonst Katalog), 2 Nachkommastellen |
| PRICE_LINE_AMOUNT | Einzelpreis × Menge nach openTRANS-Semantik (OQ-05) |
| TAX | Steuersatz des Katalogartikels als Anteil |
| ARTICLE_PRICE@type | net_list bei Profil netto, gros_list bei brutto (OQ-18) |

## Elementbaum

- `ORDER_LIST` [1..1] (REF-248090, LX-SPEC-2009 2.1)
  - `ORDER` [1..1] (REF-248090, LX-SPEC-2009 2.1)
    - `ORDER_HEADER` [1..1] (REF-248090)
      - `CONTROL_INFO` [1..1] (REF-248090)
        - `GENERATOR_INFO` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
        - `GENERATOR_DATE` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
      - `ORDER_INFO` [1..1] (REF-248090)
        - `ORDER_ID` [1..1] – Auftrag-Bestellnummer (REF-248090, LX-SPEC-2009 4.1)
        - `ORDER_DATE` [1..1] – Datumsteil wird Auftragsdatum, Zeitteil ignoriert (REF-248090, LX-SPEC-2009 4.1)
        - `ORDER_PARTIES` [1..1] (REF-248090)
          - `BUYER_PARTY` [1..1] – Lieferadresse (Lexware-Bedeutung, nicht openTRANS-Käufer) (REF-248090, LX-SPEC-2009 4.1)
            - `PARTY` [1..1] (REF-248090)
              - `ADDRESS` [1..1] (REF-248090)
                - `NAME` [1..1] – Firmenname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME2` [1..1] – Nachname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME3` [1..1] – Vorname (REF-248090, LX-SPEC-2009 3.3)
                - `STREET` [1..1] – Straße (inklusive Hausnummer, wie in der Referenz) (REF-248090, LX-SPEC-2009 4.1)
                - `CITY` [1..1] – Ort (REF-248090, LX-SPEC-2009 4.1)
                - `ZIP` [1..1] – PLZ (REF-248090, LX-SPEC-2009 4.1)
                - `COUNTRY` [1..1] – Land als Name; Länderkürzel werden nicht umgewandelt (REF-248090, LX-SPEC-2009 4.1)
                - `PHONE` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `FAX` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `EMAIL` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `VAT_ID` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
          - `INVOICE_PARTY` [1..1] – Rechnungsadresse; ohne Kundenzuordnung wird ein neuer Kunde angelegt (REF-248090, LX-SPEC-2009 4.1)
            - `PARTY` [1..1] (REF-248090)
              - `ADDRESS` [1..1] (REF-248090)
                - `NAME` [1..1] – Firmenname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME2` [1..1] – Nachname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME3` [1..1] – Vorname (REF-248090, LX-SPEC-2009 3.3)
                - `STREET` [1..1] – Straße (inklusive Hausnummer, wie in der Referenz) (REF-248090, LX-SPEC-2009 4.1)
                - `CITY` [1..1] – Ort (REF-248090, LX-SPEC-2009 4.1)
                - `ZIP` [1..1] – PLZ (REF-248090, LX-SPEC-2009 4.1)
                - `COUNTRY` [1..1] – Land als Name; Länderkürzel werden nicht umgewandelt (REF-248090, LX-SPEC-2009 4.1)
                - `PHONE` [1..1] – Telefon (REF-248090, LX-SPEC-2009 4.1)
                - `FAX` [1..1] – Fax (REF-248090, LX-SPEC-2009 4.1)
                - `EMAIL` [1..1] – E-Mail (REF-248090, LX-SPEC-2009 4.1)
                - `VAT_ID` [1..1] – USt-IdNr. (REF-248090, LX-SPEC-2009 4.1)
          - `SUPPLIER_PARTY` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
            - `PARTY` [1..1] (REF-248090)
              - `ADDRESS` [1..1] (REF-248090)
                - `NAME` [1..1] – Firmenname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME2` [1..1] – Nachname (REF-248090, LX-SPEC-2009 3.3)
                - `NAME3` [1..1] – Vorname (REF-248090, LX-SPEC-2009 3.3)
                - `STREET` [1..1] – Straße (inklusive Hausnummer, wie in der Referenz) (REF-248090, LX-SPEC-2009 4.1)
                - `CITY` [1..1] – Ort (REF-248090, LX-SPEC-2009 4.1)
                - `ZIP` [1..1] – PLZ (REF-248090, LX-SPEC-2009 4.1)
                - `COUNTRY` [1..1] – Land als Name; Länderkürzel werden nicht umgewandelt (REF-248090, LX-SPEC-2009 4.1)
                - `PHONE` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `FAX` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `EMAIL` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
                - `VAT_ID` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
        - `PAYMENT` [1..1] – Zahlungsart; fehlt PAYMENT, gilt Rechnung (REF-248090, LX-SPEC-2009 4.2.3)
          - `CASH` [0..1] (LX-SPEC-2009 4.2.3.1)
            - `PAYMENT_TERM` [1..1] – 10 Rechnung, 25 Vorkasse, 52 Nachnahme, 56 Barzahlung (LX-SPEC-2009 4.2.3.1)
          - `ACCOUNT` [0..1] (REF-248090, LX-SPEC-2009 4.2.3.2)
            - `HOLDER` [1..1] – Kontoinhaber (REF-248090, LX-SPEC-2009 4.2.3.2)
            - `BANK_NAME` [1..1] – Bankname (REF-248090, LX-SPEC-2009 4.2.3.2)
            - `BANK_CODE` [1..1] – BLZ (REF-248090, LX-SPEC-2009 4.2.3.2)
            - `BANK_ACCOUNT` [1..1] – Kontonummer (REF-248090, LX-SPEC-2009 4.2.3.2)
            - `PAYMENT_TERM` [1..1] – 54 Bankeinzug (REF-248090, LX-SPEC-2009 4.2.3.2)
        - `REMARK` [0..1] – Versandart; Position nur bei gleichnamiger Nebenleistung, sonst Nachbemerkung (REF-248090, LX-SPEC-2009 3.2, LX-SPEC-2009 4.2.6)
        - `REMARK` [0..1] – Versandkosten (siehe delivery_method) (REF-248090, LX-SPEC-2009 3.2)
        - `REMARK` [0..1] – Bemerkungstext des Belegs (REF-248090, LX-SPEC-2009 3.2, LX-SPEC-2009 5.3)
    - `ORDER_ITEM_LIST` [1..1] (REF-248090)
      - `ORDER_ITEM` [1..n] (REF-248090, LX-SPEC-2009 4.1)
        - `LINE_ITEM_ID` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
        - `ARTICLE_ID` [1..1] (REF-248090)
          - `SUPPLIER_AID` [1..1] – Artikelnummer; ohne Stammartikel wird die Position verworfen (REF-248090, LX-SPEC-2009 4.1, LX-SPEC-2009 4.2.2)
          - `DESCRIPTION_LONG` [0..1] – Positionstext; vom Adapter nicht geschrieben (OQ-15) (REF-248090, LX-SPEC-2009 4.1)
        - `QUANTITY` [1..1] – Artikelmenge (REF-248090, LX-SPEC-2009 4.1)
        - `ORDER_UNIT` [1..1] – wird nicht übernommen (REF-248090, LX-SPEC-2009 4.1)
        - `ARTICLE_PRICE` [1..1] (REF-248090, LX-SPEC-2009 4.2.5)
          - `PRICE_AMOUNT` [1..1] – laut LX-SPEC nicht verwendet (REF-248090, LX-SPEC-2009 4.1)
          - `PRICE_LINE_AMOUNT` [1..1] – Positionspreis; Bedeutung bei Menge > 1 offen (OQ-05) (REF-248090, LX-SPEC-2009 4.2.5)
          - `TAX` [1..1] – Steuersatz als Anteil, 0.19 = 19 % (REF-248090, LX-SPEC-2009 4.1)

## Dokumentiert, aber bewusst nicht verwendet

- `ORDER_INFO/PRICE_CURRENCY` (LX-SPEC-2009 4.1): „978“ entspricht Euro, einzig erlaubter Wert. In REF nicht vorhanden; Position im Element ungeklärt. Adapter erzwingt Währung EUR intern (OQ-21).
- `REMARK type=tax_area` (LX-SPEC-2009 3.2, 4.2.5.4): Merchant/EU/Non_EU; Schreibweise in LX-SPEC uneinheitlich (Non_EU, non_eu, non_EU). Auslandskunden werden blockiert (OQ-10).
- `REMARK type=additional_costs` (LX-SPEC-2009 3.2, 4.2.7): Zahlungsartkosten; vom Import nicht erkannt, daher nicht genutzt.
- `ORDER_ITEM/REMARK type=origin_company_id` (LX-SPEC-2009 4.2.8): Mandantenschutz; Position im ORDER_ITEM unbelegt (OQ-16).
- `ORDER_ITEM/REMARK type=arbitrary_data` (LX-SPEC-2009 4.2.5.3): Nettopreis bei Bruttoauftrag in Nettofirma; nicht genutzt.
- `PAYMENT/CARD` (LX-SPEC-2009 4.2.3.3): Kreditkarte; wird bewusst nie erzeugt (keine Kartendaten speichern).
- `ARTICLE_ID/DESCRIPTION_SHORT` (LX-SPEC-2009 4.1): Würde Artikelbezeichnung und Matchcode setzen; nicht genutzt (OQ-15).
- `ORDER_PARTIES/*/PARTY/PARTY_ID` (LX-SPEC-2009 4.1): Wird von Lexware nicht übernommen; Kundennummer ist nicht übertragbar (OQ-08).
- `SHIPMENT_PARTIES/DELIVERY_PARTY` (OT-1.0): In LX-SPEC nicht erwähnt; Lieferadresse läuft über BUYER_PARTY.

## Offene Fragen

| ID | Thema | Frage | Auswirkung | Klärung |
|---|---|---|---|---|
| OQ-01 | Zielsystem | Welches Lexware-Produkt in welcher Version nutzt der Kunde? LX-SPEC gilt für Warenwirtschaft ab 9.00 (Stand 2009); faktura+auftrag und aktuelle Versionen können abweichen. | Alle Abbildungen | Produkt und Version beim Kunden erfragen; Integrationstest auf genau dieser Version |
| OQ-02 | Encoding | UTF-8 (LX-SPEC-Empfehlung) oder ISO-8859-1 (REF)? | Umlaute, Sonderzeichen | Test 4 und 4b in beiden Varianten |
| OQ-03 | Textnotation | Verarbeitet die Zielinstallation CDATA (REF) und Entitäten (LX-SPEC) gleichwertig? | Sonderzeichen &, <, >, Anführungszeichen | Test 5 |
| OQ-04 | Datumsformat | REF nutzt „JJJJ-MM-TT hh:mm:ss“, LX-SPEC zeigt ISO „JJJJ-MM-TTThh:mm:ss+01:00“. Akzeptiert Lexware beide? | Auftragsdatum | Test 1 (REF-Format), Diagnose-Datei 1b (ISO) |
| OQ-05 | PRICE_LINE_AMOUNT | Liest Lexware PRICE_LINE_AMOUNT bei Menge > 1 als Zeilensumme (openTRANS) oder als Einzelpreis? REF enthält nur Menge 1. | Falsche Preise um Faktor Menge | Test 2 (Menge 3, Einzelpreis 10,00, Zeilensumme 30,00) |
| OQ-06 | Fehlender Preis | Was passiert ohne ARTICLE_PRICE oder mit Preis 0? Positionspreise aus der Datei sind laut LX-SPEC 4.2.5.5 manuell. | Mailbestellungen nennen oft keinen Preis | Diagnose-Dateien 6a/6b; bis dahin blockiert der Adapter Positionen ohne Preis |
| OQ-07 | Lieferadresse | LX-SPEC: BUYER_PARTY wird in die Lieferadresse des Kunden übernommen, ergänzt aber nie. Erscheint eine abweichende Lieferadresse im Auftrag selbst? | Ware an falsche Adresse | Test 3 mit neuem und mit bestehendem Kunden |
| OQ-08 | Kundenzuordnung | PARTY_ID wird ignoriert; Zuordnung nur manuell im eBusiness-Dialog, sonst Neuanlage. Ist das für den Kunden-Workflow akzeptabel? | Doppelte Kunden in Lexware | Workflow mit Anwender klären; Test 1 mit bestehendem Kunden |
| OQ-09 | Ländernamen | COUNTRY braucht den Namen. Stimmen die Schreibweisen (Österreich, Schweiz, …) mit der Länderliste der Installation überein? | Falsches Land im Kundenstamm | Test 3b mit Österreich |
| OQ-10 | Steuergebiet | tax_area-Werte (Merchant/EU/Non_EU) und Schreibweise sind uneinheitlich dokumentiert. | Falsche Steuer bei Auslandskunden | Bis zur Klärung blockiert der Adapter Rechnungsanschriften außerhalb des Lieferantenlandes |
| OQ-11 | Versandkosten | REF überträgt Versand doppelt (Artikelzeile YT19VERUPS01 und REMARK). Soll Versand als REMARK (Nebenleistung) oder als Artikel übertragen werden? | Doppelte oder fehlende Versandkosten | Mit Kunde klären; Test 7 mit und ohne gleichnamige Nebenleistung |
| OQ-12 | SEPA | LX-SPEC kennt nur BLZ/Kontonummer. Wie werden IBAN/BIC übertragen? | Lastschrift ohne Bankdaten | Adapter überträgt keine Bankdaten; Mandat muss in Lexware hinterlegt sein |
| OQ-13 | Feldlängen | Maximale Längen der Lexware-Felder sind unbekannt. REF kürzt die Versandart auf 32 Zeichen. | Abgeschnittene Texte | Test 5 mit langen Texten; bis dahin Warnung ab 32 Zeichen Versandart |
| OQ-14 | ORDER_ID | Soll das Lexware-Feld „Bestellnummer“ die Kundenbestellnummer oder die interne Belegnummer tragen? | Zuordnung von Rückfragen | Mit Kunde klären; Standard: Kundenbestellnummer, sonst Belegnummer |
| OQ-15 | Positionstexte | DESCRIPTION_SHORT setzt Bezeichnung/Matchcode, DESCRIPTION_LONG den Positionstext. Gewünscht? | Überschriebene Artikeltexte | Bis zur Klärung nicht gesendet |
| OQ-16 | Mandantenschutz | REMARK origin_company_id würde Importe in falsche Mandanten verhindern; Position im ORDER_ITEM unbelegt. | Import in falschen Mandanten | Diagnose-Datei nach Klärung der Firmen-Guid |
| OQ-17 | Referenzstatus | Wurde REF tatsächlich erfolgreich importiert, und mit welcher Version? | Belastbarkeit der Referenz | Bei Yellotools erfragen |
| OQ-18 | Netto/Brutto | Passt ARTICLE_PRICE type (net_list/gros_list) zur Firmeneinstellung „Preise netto/brutto“? Umrechnung verursacht Rundungsdifferenzen (LX-SPEC 4.2.5). | Falsche Endbeträge | Test 1 und 2 in der Zielfirma prüfen |
| OQ-19 | Unbekannte Artikelnummer | LX-SPEC beschreibt einmal eine Rückfrage, einmal Abbruch. | Unvollständige Aufträge | Adapter blockiert; Diagnose-Datei 9 zeigt das Lexware-Verhalten |
| OQ-20 | Sonderfälle | Serien-/Chargenartikel erzeugen nur eine Auftragsbestätigung; negative Lagerbestände lösen Rückfragen aus. | Abweichender Belegtyp | Im Test 10 einen Lagerartikel mit geringem Bestand verwenden |
| OQ-21 | Währung | PRICE_CURRENCY ist dokumentiert, fehlt aber in REF. Ist es erforderlich? | Keine, solange nur EUR | Adapter lässt nur EUR zu |

## Referenzanalyse 248090.xml

- sha256 `4abe421990a9473d6666f3989cadce5348848fc836f3d475664d9b0d3693d3d5`, 5127 Byte, Encoding ISO-8859-1, Zeilenende CRLF, 67 CDATA-Werte.
- Namespaces: (keiner), http://www.opentrans.org/XMLSchema/1.0.
- Parteien identisch: ja (Käufer = Rechnung = Lieferant; Rollen daher aus der Referenz allein nicht unterscheidbar).
- Reihenfolgen in ARTICLE_PRICE: PRICE_AMOUNT/PRICE_LINE_AMOUNT/TAX; TAX/PRICE_AMOUNT/PRICE_LINE_AMOUNT.
- Auffälligkeit: ORDER_LIST/ORDER/ORDER_HEADER/ORDER_INFO/REMARK: Wert endet abrupt („United Parcel Service Standard (“), mögliche Kürzung
- Auffälligkeit: ORDER_LIST/ORDER/ORDER_ITEM_LIST/ORDER_ITEM/ARTICLE_ID/SUPPLIER_AID: Platzhalterwert „XXXX“
- Abweichung von der Spezifikation: ORDER_LIST/ORDER/ORDER_ITEM_LIST/ORDER_ITEM[1]/ARTICLE_PRICE/PRICE_AMOUNT: Falsche Reihenfolge: steht nach TAX
- Abweichung von der Spezifikation: ORDER_LIST/ORDER/ORDER_ITEM_LIST/ORDER_ITEM[1]/ARTICLE_PRICE/PRICE_LINE_AMOUNT: Falsche Reihenfolge: steht nach TAX
- Nur eine echte Datei liegt vor: Variabilität zwischen Bestellungen ist unbekannt. Mit weiteren Dateien `python tools/lexware_testpaket.py` erneut ausführen.
