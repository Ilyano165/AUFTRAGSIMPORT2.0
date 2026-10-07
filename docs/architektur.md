# Architektur und Integrationsabhängigkeiten

## Pipeline

| Schritt | Ort | Stand |
|---|---|---|
| 1 Mail entdecken, 2 sicher laden | `ingest` (Iteration 5) | offen |
| 3 Normalisieren | `parsers/text.py` | fertig |
| 4 Inhalt extrahieren, 5 Anhänge | `parsers/text.py`, Anhänge Iteration 5 | teilweise |
| 6 Bestellung erkennen | `parsers/engine.py` | fertig |
| 7 Positionen | `parsers/order_lines.py` | fertig |
| 8 Anschriften | `parsers/address.py` | fertig |
| 9 Kundendaten | `parsers/customer.py`, `parsers/contact.py` | fertig |
| 10 Zahlung | `parsers/payment.py` | fertig |
| 11 Versand | `parsers/shipping.py` | fertig |
| 12 Artikel zuordnen | `services/matching.py` | fertig |
| 13 Validieren | `services/validation.py` | fertig |
| 14 Benutzerprüfung | GUI (Iteration 6) | offen |
| 15–18 Export vorbereiten, archivieren, atomar schreiben, Zustand speichern | Iteration 4/5 | offen |

## Sicherheitsstufen

Jeder erkannte Wert ist ein `Field` mit Zustand (erkannt, prüfen, unbekannt, manuell) und
einer `Evidence` (Regel, Begründung, Sicherheit, Mailzeile). Unsichere Regeln setzen immer
„prüfen“. Exportierte Pflichtwerte im Zustand „prüfen“ oder „unbekannt“ blockieren den Export.

## Offene Integrationsabhängigkeiten

- T-01 Lexware-Importformat: echte Importe mit Lexware verifizieren.
- T-06 Format des Lexware-Artikelstamm-Exports: bis dahin CSV mit Spaltenzuordnung.
- T-13 Firmierung des Herausgebers.
- OAuth2 für Microsoft 365 (D-01).

## Export (Iteration 4)

`export/model.py` (formatneutrale Repräsentation) → `export/lexware/adapter.py` (Abbildung, Befunde) →
`export/lexware/serializer.py` (XML) → `export/validator.py` (unabhängige Prüfung gegen
`lexware_opentrans_order_v1.json`) → `services/export_service.py` (Modi, Jobzustände, atomares Schreiben,
Bericht) und `services/export_recovery.py` (Startprüfung). Jeder Jobzustand wird vor seiner Wirkung
gespeichert; unklare Fälle werden nie automatisch neu exportiert.
