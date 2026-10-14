# ATIV → PLCS: Schnittstelle Gutschriften und Retourentickets

Stand: 14.10.2026 · Modul `plcs_greenrx` 20.0.1.6.0 · Datenvertrag `plcs.ativ.gutschrift/1`

## 1. Architektur

```
ATIV (Odoo 19 Online)                                PLCS (Odoo 20, Odoo.sh)
──────────────────────                               ─────────────────────────────────────────────
purchase.order  („Gutschriften“)  ◄── JSON-2-API ─── Abruf (nur lesend, API-Schlüssel, HTTPS)
purchase.order.line                    (Bearer)        │
helpdesk.ticket, x_vorgang                             ▼
res.partner, product.product                         Warteschlange plcs.ativ.queue
ir.attachment, mail.message                            │  Portionen, Wiederholung, Protokoll
                                                       ▼
Server-Aktion „Webhook senden“  ── POST ──────────►  /plcs_greenrx/ativ/webhook?token=…
(optional, nur Anstoß)                                 │  merkt nur „Datensatz X geändert“ vor
                                                       ▼
                                                     Spiegel (nur lesen): plcs.ativ.credit.note,
                                                     .line, plcs.ativ.ticket, .message, .document
                                                       │
                                                       ▼
                                                     PLCS-Vorgang plcs.greenrx.ticket (Soll-Daten)
                                                     Boxen, Scans, Ist, Ampel, Eingang: unverändert
```

**Warum Abruf statt reinem Webhook:** Die Webhook-Aktion von Odoo Online sendet nur Feldwerte
des geänderten Datensatzes (mit 1 Sekunde Zeitlimit, ohne Wiederholung). Positionen, Tickets und
Dateien kommen darin nicht vollständig an, und eine Zeitsteuerung alle 12 Stunden ist damit nicht
möglich. PLCS liest deshalb alle Daten über die API. Der Webhook stößt nur den sofortigen Abruf
der betroffenen Gutschriften an.

**Zeitplan:** Geplante Aktion „GreenRX: ATIV-Gutschriften abgleichen (alle 12 Stunden)“ auf
Odoo.sh, erster Lauf 04:10 UTC, danach alle 12 Stunden (04:10 und 16:10 UTC, im Sommer 06:10 und
18:10 Uhr deutscher Zeit, im Winter 05:10 und 17:10 Uhr). Sie läuft auf dem Server, unabhängig von
einer Browsersitzung. Die Aktion „GreenRX: ATIV-Warteschlange abarbeiten“ arbeitet die
Arbeitsschritte ab. Sie wird bei Bedarf sofort angestoßen (Knopf, Webhook) und läuft zusätzlich
alle 30 Minuten für fällige Wiederholungen.

## 2. Ablauf eines Laufs

1. **Planen.** Beim ersten Lauf (kein Fortschrittsstand) alle Gutschriften des Filters, sonst die
   seit dem Fortschrittsstand (minus 15 Minuten Überlappung) geänderten. Als geändert gilt eine
   Gutschrift auch, wenn sich nur eine Position, ein Ticket, der Vorgang, der Kunde, ein Anhang
   oder eine Nachricht geändert hat.
2. **Abarbeiten** in Portionen (Standard 25 Gutschriften). Jede Portion wird einzeln gespeichert.
3. **Fehler:** Netzwerk- und Serverfehler werden nach 5, 15, 60, 240 Minuten wiederholt
   (höchstens 5 Versuche). Datenfehler einer Gutschrift halten die übrigen nicht auf.
4. **Abschluss:** Der Fortschrittsstand wird erst übernommen, wenn alle Schritte des Laufs
   erfolgreich waren. Fehlgeschlagene Schritte bleiben sichtbar und lassen sich wiederholen.
5. **Überschneidung:** Läuft schon ein Lauf, wird kein zweiter geplant. Doppelte Schritte werden
   nicht angelegt. Die Warteschlange wird mit Zeilensperren (`SKIP LOCKED`) abgearbeitet.

Wiederholte Übertragungen erzeugen keine Duplikate (Schlüssel = Quellidentität). Unveränderte
Gutschriften werden über eine Prüfsumme erkannt und nicht neu geschrieben.

## 3. Quellidentität

`<Quellsystem>:<Modell>:<ID>`, z. B. `ativ-service:purchase.order:1898`. Das Quellsystem ist die
Einstellung „Kennung Quellsystem“ (leer = Datenbankname bei ATIV). Testkopie und Produktivsystem
haben daher getrennte Schlüssel. ATIV-IDs werden nie als PLCS-IDs verwendet.

Der PLCS-Vorgang (Referenznummer V…) trägt die Quellidentität des ATIV-Vorgangs (`x_vorgang`) im
Feld „Externe ID“. Ein schon vorhandener Vorgang gleicher Nummer (z. B. aus dem Retourenschein-QR)
wird übernommen und verknüpft.

## 4. Feldzuordnung (geprüft in der ATIV-Testkopie am 14.10.2026)

### Gutschrift (`purchase.order` → `plcs.ativ.credit.note`)

| PLCS | ATIV | Hinweis |
|---|---|---|
| Quellidentität | `id` | |
| Gutschriftnummer | `name` | z. B. 20267602 |
| Vorgangsnummer | `x_studio_vorgang.x_name` | V…, = Referenznummer auf dem Retourenschein |
| Reklamationsnummer | `x_studio_complaint_number`, sonst `x_vorgang.x_complaint_number` | |
| Kunde | `partner_id` | Absender/Melder |
| Kundennummer | `partner_id.x_studio_vendor_number` („Supplier Number“) | `ref` ist bei ATIV leer |
| E-Mail, Telefon, Adresse, Ort | `partner_id.email/phone/street/street2/zip/city/country_id` | |
| Abholadresse | Kundenadresse, bei `x_studio_abholadresse_kontaktadresse = False` die Adresse von `x_studio_contact_address_name` | Auslegung, mit ATIV bestätigen |
| Abholtermin (geplant) | `date_planned` („Erwartete Ankunft“) | wird bei ATIV vom Benutzer PLCS gepflegt |
| Abholung erfolgt | `x_studio_pick_up_done` | |
| Status Gutschrift | `state` (Entwurf / Bestellung) | |
| Bestätigt am | `date_approve` | |
| Eingangsstatus / Wareneingang ATIV | `receipt_status`, `x_wareneingang_done` | |
| Quarantänegrund | `x_studio_quarantne_grund` | |
| Beträge | `amount_untaxed`, `amount_tax`, `amount_total`, `currency_id` | |
| Angelegt von / Zuständig | `create_uid`, `user_id` | nur als Text, keine Benutzer in PLCS |
| Angelegt / geändert bei ATIV | `create_date`, `write_date` | |

### Position (`purchase.order.line` → `plcs.ativ.credit.note.line`)

| PLCS | ATIV |
|---|---|
| Quellidentität | `id` |
| Ticket | `x_studio_ticket`, sonst Kopf `x_studio_ticket_1` (657 ältere Positionen ohne Ticketverweis) |
| PZN / GTIN / Artikel | `product_id.default_code` / `.barcode` / `.name` |
| Menge (Soll), Einheit | `product_qty`, `product_uom_id` |
| Preis, Steuern, Beträge | `price_unit`, `tax_ids` (Name), `price_subtotal`, `price_tax`, `price_total` |
| Charge, Verfall | Ticket `x_studio_batch_number` (= Ticketname), `x_studio_expiration_date` |
| Lagerbedingungen | `product_id.x_studio_storage_conditions` |
| ATIV-Bewertung | `x_studio_sellable_qty`, `x_studio_quarantine_qty`, `x_studio_blocked_qty`, `x_studio_destroyed_qty` |

### Ticket (`helpdesk.ticket` → `plcs.ativ.ticket`)

| PLCS | ATIV |
|---|---|
| Ticketnummer | `ticket_ref` |
| Titel | `name` (bei ATIV = Charge) |
| Beschreibung | `description` |
| Bearbeitungsstand | `stage_id` (deutsch angezeigt, englischer Name für die Einordnung) |
| Artikel, PZN, Packungen, Preis | `x_studio_product`, `x_studio_pzn`, `x_studio_product_number`, `x_studio_product_price` |
| Retourengrund, GDP-Bestätigung | `x_studio_reason_for_product_return`, `x_studio_confirmation_of_gdp_compliant_storage_1` |
| Ablehnungsgrund | `x_studio_reason_for_rejection`, `x_studio_rejection_reason` |
| Adresse laut Ticket | `x_studio_strae_hausnummer`, `x_studio_strae2`, `x_studio_plz`, `x_studio_stadt`, `x_studio_land` |
| Bearbeiter, Ersteller, Zeiten | `user_id`, `create_uid`, `create_date`, `write_date`, `close_date` |
| Verlauf | `mail.message` des Tickets (Autor, Zeit, Art, Text, Anhänge), Statuswechsel aus `mail.tracking.value` |

`mail.tracking.value` darf bei ATIV nur ein Administrator lesen. Ohne dieses Recht erfasst PLCS
Statuswechsel beim Abruf selbst („beim Abruf erkannt“).

### Dokumente (`ir.attachment` → `plcs.ativ.document`)

Angaben zu allen Anhängen an Ticket, Gutschrift und Vorgang (Name, Typ, Größe, Prüfsumme, Datum,
hochgeladen von). Dateiinhalte werden über die API gelesen, mit der Prüfsumme geprüft und als
Anhang am PLCS-Vorgang gespeichert. Einstellung „Dokumente“:

- **Standard:** Dateien erwarteter Vorgänge werden kopiert, übrige bei Bedarf (Knopf „Öffnen“
  holt die Datei mit den Rechten der Schnittstelle).
- Alle kopieren: rund 6.100 Dateien, etwa 2 GB (Stand Testkopie).

Eine geänderte Datei wird als neue Fassung gespeichert, die alte bleibt mit „(alte Fassung)“.

## 5. Einordnung „erwartet“

Je Ticketstufe (englischer Name bei ATIV, einstellbar):

| Einordnung | Stufen (Standard) | PLCS-Status bei noch nicht begonnenem Vorgang |
|---|---|---|
| erwartet | Approved, Picked Up, On Hold | Offen |
| angemeldet | New, In Progress | Nicht erwartet |
| abgeschlossen | Receipt, Stock, Sold, Archive; oder Wareneingang bei ATIV erledigt; oder Gutschrift vor dem Stichtag | Nicht erwartet |
| storniert | Rejected, Canceled | Storniert |

„Picked Up“ heißt bei ATIV: Transoflex hat abgeholt, das Paket ist unterwegs zu PLCS. Ein Vorgang
mit mehreren Tickets gilt als erwartet, wenn mindestens ein Ticket erwartet ist.

Regeln:
- Ein begonnener oder erfasster Vorgang behält seinen PLCS-Status. Meldet ATIV danach eine
  Stornierung oder geänderte Mengen, steht das als Hinweis im Verlauf des Vorgangs.
- Die Lagerleitung kann einen nicht erwarteten Vorgang mit „Trotzdem erwarten“ öffnen. Danach
  ändert die Synchronisierung den Status nicht mehr („Status wieder aus ATIV“ hebt das auf).
- Vorgänge werden nie automatisch gelöscht. Fehlt eine Gutschrift beim Vollabgleich, wird sie als
  „bei ATIV nicht mehr gefunden“ gekennzeichnet. Eine bei ATIV entfernte Position bleibt sichtbar
  und zählt mit Soll 0.

## 6. Schutz lokaler PLCS-Daten

Die Synchronisierung schreibt nur in den Spiegel und in die Soll-Daten des Vorgangs (Kopfdaten,
angemeldete Positionen). Nicht berührt werden Boxen, Scans, Ist-Mengen, Ampel, Vorprüfung,
Begründungen, Retourenschein-Abweichung, Eingänge und VP-Entscheidungen. Bei einem erfassten
Vorgang bleibt auch das Soll eingefroren; Änderungen bei ATIV stehen nur im Verlauf. Bei einer
Position mit erfasster Box wird der Artikel nicht mehr getauscht.

## 7. Datenvertrag `plcs.ativ.gutschrift/1`

Eine Gutschrift wird als JSON-Objekt verarbeitet (gespeichert unter „Herkunft“ der Gutschrift):

```json
{
  "contract": "plcs.ativ.gutschrift/1",
  "source": {"system": "ativ-service", "model": "purchase.order", "id": 1898,
             "key": "ativ-service:purchase.order:1898"},
  "credit_note": {"name": "20267602", "state": "draft", "state_label": "…", "date_order": "…",
                  "date_approve": false, "date_planned": "…", "pickup_done": false,
                  "receipt_status": false, "ativ_receipt_done": false, "complaint_number": "S23124",
                  "quarantine_reason": false, "amount_untaxed": 132.58, "amount_tax": 25.19,
                  "amount_total": 157.77, "currency": "EUR", "created_by": "…", "buyer": "…",
                  "create_date": "…", "write_date": "…"},
  "vorgang": {"key": "…:x_vorgang:1712", "id": 1712, "name": "V202601693",
              "complaint_number": "S23124", "source": "portal", "create_date": "…"},
  "customer": {"key": "…:res.partner:3", "id": 3, "name": "…", "customer_number": "23233332",
               "email": "…", "phone": "…", "address": "…", "city": "Köln"},
  "lines": [{"key": "…:purchase.order.line:1872", "id": 1872, "sequence": 10,
             "ticket_key": "…:helpdesk.ticket:2124",
             "product": {"code": "7560038", "barcode": false, "name": "BRILIQUE 90 mg …",
                         "storage_conditions": "…"},
             "description": "…", "qty": 3.0, "uom": "Units", "price_unit": 13.22,
             "taxes": "…", "price_subtotal": 39.66, "price_tax": 7.54, "price_total": 47.2,
             "qty_sellable": 0, "qty_quarantine": 0, "qty_blocked": 0, "qty_destroyed": 0,
             "quarantine_reason": false}],
  "tickets": [{"key": "…:helpdesk.ticket:2124", "id": 2124, "number": "1001983", "title": "33t32",
               "description": "…", "stage": {"key": "Approved", "name": "Genehmigt"},
               "complaint_number": "S23124", "vorgang": "V202601693",
               "partner": {"name": "…", "email": "…", "phone": "…"}, "address": "…",
               "alt_pickup_address": false, "product": {"name": "…", "pzn": "7560038"},
               "qty": 3.0, "price": 13.22, "batch": "33t32", "expiry_date": "2029-02-28",
               "storage_conditions": "…", "return_reason": "…", "gdp_confirmation": "…",
               "rejection_reason": false, "pickup_done": false, "assigned_user": "…",
               "created_by": "…", "create_date": "…", "write_date": "…", "close_date": false,
               "messages": [{"key": "…:mail.message:42291", "date": "…", "author": "…",
                             "kind": "comment", "subtype": "…", "body": "…", "tracking": false,
                             "attachments": "…"}]}],
  "documents": [{"key": "…:ir.attachment:17061", "id": 17061, "name": "…pdf",
                 "mimetype": "application/pdf", "size": 427703, "checksum": "…",
                 "owner": {"model": "helpdesk.ticket", "id": 2124, "key": "…"},
                 "create_date": "…", "author": "…", "type": "binary"}]
}
```

Änderungen am Vertrag erhöhen die Versionsnummer. Ein unbekannter Vertrag wird abgelehnt.

Webhook (von ATIV): `POST /plcs_greenrx/ativ/webhook?token=<Schlüssel>` mit dem Standardinhalt der
Odoo-Webhook-Aktion (`_model`, `_id`). Angenommen werden `purchase.order`, `purchase.order.line`,
`helpdesk.ticket`, `x_vorgang`, `res.partner`, `mail.message` und `ir.attachment` (über
`res_model`/`res_id`). Antwort 200 `{"ok": true}`, 401 bei falschem Schlüssel, 400 bei ungültigem
JSON. Der Schlüssel wird in PLCS nur als SHA-256-Prüfsumme gespeichert.

## 8. Einrichtung

### PLCS

1. GreenRX › Konfiguration › Schnittstelle GreenRX-ERP › Neu.
2. Art „ATIV-Gutschriften“, URL `https://<ATIV-Datenbank>.odoo.com`, Datenbank, Benutzer
   (Login des API-Benutzers), API-Schlüssel. Protokoll JSON-2.
3. „Verbindung testen“: prüft Anmeldung, Leserechte und alle Felder; meldet fehlende Felder.
4. Bei Bedarf Stufen, Stichtag und Dokumente anpassen.
5. „ATIV-Daten synchronisieren“ (beim ersten Mal: Erstübernahme).
6. „Automatisch alle 12 Stunden“ einschalten.
7. Optional: Reiter Webhook › „Neuen Webhook-Schlüssel erzeugen“. Die angezeigte Adresse in ATIV
   eintragen (nur einmal sichtbar).

### ATIV

- API-Benutzer mit Leserechten auf Einkauf (Gutschriften), Kundendienst-Tickets, Kontakte,
  Produkte und `x_vorgang`. Den API-Schlüssel legt der Benutzer selbst an
  (Mein Profil › Sicherheit › API-Schlüssel). PLCS ruft nur lesende Methoden auf; der Client lehnt
  schreibende Aufrufe ab.
- Optional (nur für den Sofort-Anstoß): Automatisierungsregeln „Bei Erstellung und Aktualisierung“
  auf Gutschrift und Ticket mit der Aktion „Webhook senden“ an die PLCS-Adresse, ohne weitere
  Felder. Fachliche Status werden dafür nicht verändert.

## 9. Bedienung

- **Manueller Start:** Schnittstelle › „ATIV-Daten synchronisieren“ (Änderungen) oder
  „Vollabgleich“ (alle Gutschriften neu prüfen, fehlende kennzeichnen). Einzelner Vorgang:
  Retourenvorgang › „Mit ATIV abgleichen“.
- **Stand:** Schnittstelle zeigt letzten erfolgreichen Lauf, Laufstatus, offene und
  fehlgeschlagene Schritte. GreenRX › Konfiguration › ATIV-Läufe zeigt je Lauf neue und
  aktualisierte Gutschriften und Vorgänge, unveränderte, Fehler und übernommene Dateien.
- **Fehler:** Lauf öffnen › „Fehlgeschlagene wiederholen“ oder Schnittstelle › „Fehler
  wiederholen“. Die Ursache steht je Schritt (ATIV-Warteschlange).
- **Deaktivieren:** Schnittstelle › „Integration ausschalten“ (Zeitplan und Webhook aus). Bei
  ATIV die Webhook-Regel archivieren. Ganz abschalten: Schnittstelle archivieren.
- **Suche im Lager:** Wareneingang scannen › Suche nach Kundennummer, Kundenname, Referenz-,
  Ticket-, Vorgangs-, Gutschrift- oder Reklamationsnummer. Nicht erwartete, erfasste und
  stornierte Vorgänge erscheinen am Ende, gestrichelt und nicht startbar.

## 10. Offene Punkte

- Stichtag für alte „Picked Up“-Tickets (540 seit Januar 2026) mit PLCS klären.
- Abholadresse und Abholtermin (`date_planned`) als Bedeutung mit ATIV bestätigen.
- API-Benutzer bei ATIV festlegen (vorhandener Benutzer „PLCS“ mit Schreibrechten oder eigener
  Nur-Lese-Benutzer).
