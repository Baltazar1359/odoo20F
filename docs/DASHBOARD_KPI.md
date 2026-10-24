# Geschäftsführungs-Dashboard PLCS – Kennzahlen und Datenquellen

Modul `plcs_dashboard` 20.0.1.0.0 (Stand 24.10.2026). Menü **Geschäftsführung**.

**Grundsätze**

- Jede Zahl wird aus Odoo-Datensätzen berechnet, ohne Platzhalter. Fehlt die Datengrundlage, zeigt die Karte „nicht verfügbar“ und den Grund.
- Alle Abfragen laufen mit den Rechten des angemeldeten Benutzers, ohne `sudo()`. Fehlt ein Leserecht, zeigt die Karte „keine Berechtigung“.
- Es zählt nur die **aktive Firma** (Firmenwechsler oben rechts), standardmäßig PLCS.
- **Zeitraum:** heute, 7, 30 oder 90 Tage (jeweils einschließlich heute) oder ein freier Zeitraum (höchstens 2 Jahre). Der **Vergleichszeitraum** ist gleich lang und endet am Tag vor dem Zeitraum.
- **Rollen:** „Geschäftsführungs-Dashboard: Lesen“ (operative Kennzahlen, Lager, Qualität) und „… : Finanzen“ (zusätzlich Umsatz, Auftragswerte, Bestandswert).
- Ein **Klick auf eine Karte** öffnet die Datensätze mit derselben Domain wie die Berechnung.

## A. Umsatz und Geschäft

| KPI | Datenquelle (Modell) | Berechnung | Verfügbar | Zusätzliche Felder |
|---|---|---|---|---|
| Umsatz (netto, fakturiert) | `account.move` | Summe `amount_untaxed_signed` gebuchter Ausgangsrechnungen minus Gutschriften, `invoice_date` im Zeitraum | ja (Finanzen) | – |
| Umsatzentwicklung | `account.move` | wie Umsatz, je Tag (≤ 31 Tage), Woche (≤ 120 Tage) oder 30-Tage-Abschnitt, mit Vergleichszeitraum (Diagramm) | ja (Finanzen) | – |
| Auftragseingang (netto) | `sale.order` | Summe `amount_untaxed` bestätigter Aufträge (`state = sale`), `date_order` im Zeitraum, in Firmenwährung umgerechnet | ja (Finanzen) | – |
| Aufträge | `sale.order` | Anzahl bestätigter Aufträge, `date_order` im Zeitraum | ja | – |
| Ø Auftragswert | `sale.order` | Auftragseingang ÷ Aufträge | ja (Finanzen), ohne Aufträge „nicht verfügbar“ | – |
| Umsatz Blistercenter | `account.move` | wie Umsatz, Kunde (Firma) mit Kennzeichen „Blistercenter“ | ja (Finanzen) | – |
| Umsatz nach Geschäftsbereich | `account.move` | Blistercenter / übrige Kunden (Diagramm) | ja (Finanzen) | – |
| Umsatz GreenRX | – | GreenRX-Leistungen werden **nicht in Odoo fakturiert**. Die gespiegelten ATIV-Gutschriften (`plcs.ativ.credit.note`) sind Gutschriften von ATIV an Apotheken und **kein PLCS-Umsatz**. | **nein** | GreenRX-Abrechnung (Rechnung PLCS → ATIV) in Odoo, z. B. Rechnungen an ATIV mit Analytik „GreenRX“ |

## B. Aufträge und operative Prozesse

| KPI | Datenquelle | Berechnung | Verfügbar | Zusätzliche Felder |
|---|---|---|---|---|
| Offene Aufträge | `sale.order` | bestätigt, Lieferstatus `pending`, `started` oder `partial` (Stichtag heute) | ja | – |
| Abgeschlossene Aufträge | `sale.order`, `stock.picking` | Lieferstatus `full`, letzte Ausgangslieferung im Zeitraum | ja | – |
| Überfällige Lagervorgänge | `stock.picking` | offen (`waiting`, `confirmed`, `assigned`), geplantes Datum vor jetzt; aufgeteilt nach Eingang, Lieferung, intern | ja | – |
| Ø Bearbeitungsdauer | `stock.picking`, `sale.order` | Mittelwert (Versanddatum Lieferung − Auftragsdatum) in Tagen, Lieferungen im Zeitraum | ja, ohne Versand „nicht verfügbar“ | – |
| Aufträge nach Bearbeitungsstatus | `sale.order.bc_state` | offene Blistercenter-Aufträge je Status (Eingegangen, Zahlungssperre, Zu beschaffen, In Beschaffung, Lieferbereit, Teilweise geliefert), Diagramm | ja (nur Blistercenter) | Für andere Aufträge gibt es keinen Bearbeitungsstatus |

## C. Lager und Warenbestand

| KPI | Datenquelle | Berechnung | Verfügbar | Zusätzliche Felder |
|---|---|---|---|---|
| Bestandswert | `stock.quant.value` (stock_account) | Summe Wert der Bestände an internen Lagerorten | ja (Finanzen **und** Lager-Administrator, da Odoo das Feld nur dieser Rolle zeigt) | – |
| Artikel mit Bestand | `stock.quant` | Anzahl Artikel mit Menge > 0 an internen Lagerorten | ja | – |
| Artikel unter Meldebestand | `stock.warehouse.orderpoint` | Nachbestellregeln mit Bestand < Mindestmenge | **nur mit gepflegten Nachbestellregeln**, sonst „nicht verfügbar“ (PLCS bestellt bewusst manuell) | Nachbestellregeln je Artikel |
| Abgelaufene Chargen / MHD in 0–30, 31–60, 61–90 Tagen | `stock.quant`, `stock.lot.expiration_date` | Anzahl Chargen und Menge mit Verfall im jeweiligen Band, interne Lagerorte | ja | – |
| Ware in Quarantäne / gesperrt | `stock.quant` (Blistercenter-Quarantäne), `plcs.greenrx.box` | Chargen in der Blistercenter-Quarantäne + gesperrte GreenRX-Boxen | ja (GreenRX-Teil nur mit GreenRX-Modul) | – |
| Gesperrte Chargen mit Bestand | `stock.quant`, `stock.lot.bc_blocked` | Chargen mit Sperre und Bestand | ja | – |
| Warenbewegungen | `stock.move` | Anzahl gebuchter Lagerbewegungen je Zeitabschnitt, Eingänge/Ausgänge/intern (Diagramm) | ja | – |

## D. Qualitätsmanagement und GDP

| KPI | Datenquelle | Berechnung | Verfügbar | Zusätzliche Felder |
|---|---|---|---|---|
| Offene VP-Freigaben | `stock.picking` (Freigabe BCFRG), `plcs.greenrx.box` | offene Blistercenter-Freigaben + GreenRX-Boxen „Wartet auf VP“ | ja | – |
| Automatisch freigegebene Wareneingänge | `stock.picking.bc_release_mode` | gebuchte Freigaben mit Freigabeweg „automatisch“ im Zeitraum, dazu „durch die VP“ und Anteil | ja (ab Blistercenter 1.3.0) | neu: `bc_release_mode` |
| Gesperrte Wareneingänge | `stock.picking` | offene Freigaben, deren Wareneingangsprüfung eine Abweichung hat | ja (ab 1.3.0) | neu: Wareneingangsprüfung `bc_check_*` |
| Beschädigte Lieferungen / Qualitätsabweichungen | `stock.picking`, `plcs.greenrx.box` | Blistercenter-Wareneingänge mit Abweichung (Prüfung im Zeitraum) + GreenRX-Boxen mit Ampel gelb/rot | ja | – |
| Offene Retouren | `stock.picking`, `plcs.greenrx.ticket` | offene Kundenrücksendungen (Rücklieferung zu einer Lieferung) + GreenRX-Vorgänge „Offen“/„In Erfassung“ | ja | – |
| Offene Reklamationen | – | In Odoo gibt es keinen Reklamationsvorgang | **nein** | Reklamations-/CAPA-Prozess (z. B. Odoo Qualität oder Helpdesk) |
| Freigaben aus der Quarantäne | `stock.picking` | automatisch vs. durch die VP im Zeitraum (Diagramm) | ja, ohne Freigaben „nicht verfügbar“ | – |

## Performance

Summen und Anzahlen rechnet die Datenbank (`_read_group`, `search_count`). Die Zahl der Abfragen wächst nicht mit der Datenmenge (Test `test_queries_do_not_grow_with_data`). Schleifen in Python laufen nur über kleine Mengen: Lieferungen im Zeitraum, offene Blistercenter-Aufträge (höchstens 5000), Nachbestellregeln.
