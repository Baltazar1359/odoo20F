# Importdateien GreenRX (Stand 13.10.2026)

Herkunft: GreenRX-ERP (Testkopie 261001sammelretoure.odoo.com), die letzten 100 Gutschriften (14.09.–13.10.2026), nur gelesen.

| Datei | Import in | Inhalt |
|---|---|---|
| `greenrx_artikel_pzn.csv` | Lager › Produkte › Zahnrad › Datensätze importieren | 30 Artikel mit PZN, GTIN (aus PZN), Chargen- und Verfallsverfolgung |
| `greenrx_vorgaenge_letzte100.csv` | GreenRX › Übersicht › Retourenvorgänge › Zahnrad › Datensätze importieren | 100 Vorgänge, 101 Tickets, 287 Packungen |

Reihenfolge: erst Artikel, dann Vorgänge. Jeder Vorgang nur einmal importieren (doppelte Referenznummern werden abgelehnt).

Auffällig:
- V202601583, V202601584, V202601618: Gutschrift ohne Ticket/Charge (bereits bestätigt).
- V202601692, V202601693: Absender ATIV Service, vermutlich Testvorgänge – bei Bedarf vor dem Import löschen.

Dieser Ordner ist kein Odoo-Modul und wird von Odoo nicht geladen.
