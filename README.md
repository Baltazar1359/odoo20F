# odoo20 – PLCS-Module für Odoo 20 (Odoo.sh)

Eigene Module für die PLCS GmbH. Die Module liegen direkt im Wurzelverzeichnis, so wie Odoo.sh sie erwartet.

| Modul | Inhalt | Stand |
|---|---|---|
| `plcs_greenrx` | GreenRX-Wareneingang: Scan je Packung, Boxen, Vorprüfung mit Ampel, KabQ-Drucke, VP-Freigabe | einsatzbereit für den Test |

---

## plcs_greenrx – GreenRX-Wareneingang

Umgesetzt nach dem Entwurf „PLCS Lager-Oberfläche“ (Handy und PC mit Handscanner).

### Ablauf im Lager (Menü GreenRX › Wareneingang scannen)

1. **Paket wählen:** Paketetikett scannen oder nach Apotheke, Kunden-, Referenz- oder Ticketnummer suchen. Odoo zeigt den Inhalt laut Ticket.
2. **Box scannen:** QR-Code einer leeren Box GX001–GX200 scannen. Handeingabe ist möglich und wird im Verlauf vermerkt.
3. **Packungen scannen:** Ein Scan speichert Seriennummer, Charge und Verfall aus dem securPharm-Code (GS1 oder IFA). Der erste Scan legt Artikel und Charge der Box fest.
4. **Vorprüfung:** 6 Sichtprüfungen (Ja/Nein) und die Prüfungen des Systems ergeben einen Ampel-Vorschlag. Eine mildere Ampel als der Vorschlag ist nicht möglich. Wer strenger wählt, gibt einen Grund an.
5. **Drucken:** KabQ-Checkliste und Anlage Box-Inhalt, bei Rot zusätzlich das Sperrblatt. Danach Ausdruck bestätigen und Box abschließen.
6. **Nächste Box oder Paket fertig:** Bei „Paket fertig“ wird der Eingang gebucht. Grün und Gelb landen in GreenRX/Quarantäne, Rot im GreenRX/Sperrlager. Weicht Soll von Ist ab, ist eine Begründung Pflicht.

Tasten am PC: **F8** Drucken, **F9** Weiter bzw. Box abschließen, **Esc** Meldung schließen.

### Regeln, die Odoo durchsetzt

- Eine Box enthält einen Artikel und eine Charge.
- Jede Seriennummer gibt es nur einmal im System. Das gilt auch für offene Erfassungen.
- Ein Doppelscan derselben Packung wird erkannt und nicht doppelt gezählt.
- Die Restlaufzeit wird geprüft (Standard 3 Monate). Darunter schlägt Odoo Rot vor.
- Ein Artikel, der nicht im Ticket steht, und eine Menge über dem Soll führen zum Vorschlag Gelb.
- Eine Box kann nur eine Person befüllen.
- Ware verlässt Quarantäne und Sperrlager **nur** über die VP-Freigabe. Andere Wege werden abgelehnt: Transfer, Barcode-App, Inventur oder Ausschuss.
- Status und Ergebnisse lassen sich nicht direkt per API ändern. Alle Änderungen laufen über die geprüften Funktionen.

### VP-Freigabe (Menü GreenRX › VP-Freigabe)

- Die Liste ist nach Ampel sortiert (Rot zuerst).
- Boxen, die die VP selbst erfasst hat, erscheinen nicht (4-Augen-Prinzip).
- **Freigeben** bucht die Box nach GreenRX/Bestand. Bei einer gesperrten Box ist eine Begründung Pflicht.
- **Sperren** bucht die Box ins Sperrlager. Ein Grund ist Pflicht.

### Tickets aus dem GreenRX-ERP

- **Schnittstelle:** GreenRX › Konfiguration › Schnittstelle GreenRX-ERP (URL, Datenbank, Benutzer, API-Schlüssel, Feldzuordnung). Der Abruf läuft auf Knopfdruck oder alle 15 Minuten.
  - Die Feldzuordnung ist nach dem Datenstand 06/2025 vorbelegt: `helpdesk.ticket`, `ticket_ref`, `x_studio_credit_note`, `x_studio_product`, `x_studio_product_number`.
  - **Vor dem ersten Abruf mit dem aktuellen GreenRX-ERP abgleichen.**
  - Sammelretouren mit mehreren Positionen lassen sich über „Positions-Modell“ abbilden.
- **Import:** GreenRX › Übersicht › Tickets › Zahnrad › Datensätze importieren. Eine Vorlage liegt dort bei.
- **Gegenrichtung:** Das GreenRX-ERP kann Tickets auch selbst anlegen, über `plcs.greenrx.ticket.api_upsert(tickets)` (externe API).

### Einrichtung

Bei der Installation legt das Modul selbst an:

- die Lagerorte GreenRX/Quarantäne, GreenRX/Bestand und GreenRX/Sperrlager unter dem ersten Lager,
- die Vorgangstypen GRXIN, GRXFRG und GRXSPR,
- die Boxen GX001–GX200.

Danach:

1. Benutzern die Gruppe **GreenRX › Lager (Erfassung)** oder **GreenRX › VP-Freigabe** geben.
2. Am Artikel GTIN als Barcode und/oder **PZN** pflegen, dazu die Lagertemperatur. Die Chargenverfolgung muss aktiv sein, ebenso „Ablaufdatum verwenden“.
3. Einstellungen: Lager › Konfiguration › Einstellungen › GreenRX (Restlaufzeit, Box-Bereich, Lagerorte).
4. Box-Etiketten drucken: GreenRX › Übersicht › Boxen GX › Auswahl › Drucken › GreenRX: Box-Etikett (QR).
5. Handscanner auf GS1-Ausgabe einstellen. Das Trennzeichen (FNC1/GS) muss übertragen werden. Unterstützt sind ASCII 29, Alt+029, Strg+] und `#`. Codes ohne Trennzeichen lehnt Odoo standardmäßig ab, weil Seriennummer und Charge dann nicht sicher getrennt sind.

### Tests

```bash
odoo-bin -d test_db -i plcs_greenrx --test-enable --test-tags /plcs_greenrx --stop-after-init
```

23 automatische Tests decken ab:

- den GS1/IFA-Parser,
- den ganzen Ablauf von Ticket bis VP-Freigabe,
- das 4-Augen-Prinzip,
- die rote Box und das Sperrlager,
- die Restlaufzeit,
- Soll/Ist-Abweichungen,
- die Sperre gegen Bewegungen an der Freigabe vorbei,
- die Sperre gegen direkte API-Schreibzugriffe,
- die Schnittstelle (mit simuliertem GreenRX-ERP).

Zusätzlich wurde der Ablauf im Browser durchgespielt (Handy- und PC-Ansicht, Lager und VP).

### Offen / vor dem Echtbetrieb

- Die Druckvorlagen sind eine Nachbildung der KabQ-Checkliste. Abgleich mit dem Word-Formblatt und Freigabe durch die QMB stehen aus. Das gilt auch für die Anlage und das Sperrblatt als neue Formblätter.
- Der Test mit echten Packungen und dem echten Handscanner steht aus (GS1 und IFA).
- Die Feldzuordnung zum aktuellen GreenRX-ERP und ein API-Benutzer bei ATIV fehlen noch.
- Validierung über Change Control (Validierungsplan und -bericht) vor dem Produktivstart.
- Verkauf aus GreenRX/Bestand und Vernichtung gesperrter Ware sind noch nicht Teil des Moduls.
