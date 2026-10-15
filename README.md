# odoo20 – PLCS-Module für Odoo 20 (Odoo.sh)

Eigene Module für die PLCS GmbH. Die Module liegen direkt im Wurzelverzeichnis, so wie Odoo.sh sie erwartet.

| Modul | Inhalt | Stand |
|---|---|---|
| `plcs_greenrx` | GreenRX-Wareneingang: Scan je Packung, Boxen, Vorprüfung mit Ampel, KabQ-Drucke, VP-Freigabe | einsatzbereit für den Test |
| `blistercenter` | Großhandel für Blistercenter: Shop- und Telefonbestellung, Vorkasse, Sammelbestellung beim Hersteller, Quarantäne mit VP-Freigabe, Ware fest je Kunde, Endkontrolle, Rückruf | Branch `feat/blistercenter-v2`, zum Test |

**Prüfumgebung:** https://baltazar1359-odoo20.odoo.com (Odoo.sh, Branch `main` = Production). Prüfanleitung: [`docs/PRUEFANLEITUNG.md`](docs/PRUEFANLEITUNG.md), Testcodes: [`docs/greenrx_testcodes.pdf`](docs/greenrx_testcodes.pdf).

---

## plcs_greenrx – GreenRX-Wareneingang

Umgesetzt nach dem Entwurf „PLCS Lager-Oberfläche“ (Handy und PC mit Handscanner).

### Ablauf im Lager (Menü GreenRX › Wareneingang scannen)

1. **Paket wählen:** Den **QR-Code auf dem Retourenschein** scannen. Er enthält:
   - die Referenznummer (Vorgang, z. B. V202601693),
   - die Reklamationsnummer,
   - je Produkt Ticketnummer, PZN, Charge und Packungsanzahl.

   Odoo findet den Vorgang. Ist er noch nicht in Odoo, legt Odoo ihn aus dem QR-Code an (abschaltbar). Weicht der Schein von den ERP-Daten ab, zeigt Odoo einen Hinweis. Ein schon erfasster Vorgang wird abgelehnt. Alternativ lässt sich nach Referenz, Ticket-, Reklamations-, Gutschrift- oder Kundennummer, nach der Apotheke oder nach dem Artikel suchen. Die Suche läuft in der Datenbank über alle offenen Vorgänge; Trennzeichen (Leerzeichen, `-`, `.`, `/`) spielen keine Rolle. Exakte Treffer stehen vor Teiltreffern, Artikel-Treffer zuletzt (höchstens 20).
2. **Box scannen:** QR-Code einer leeren Box GX001–GX200 scannen. Handeingabe ist möglich und wird im Verlauf vermerkt.
3. **Packungen scannen:** Ein Scan speichert Seriennummer, Charge und Verfall aus dem securPharm-Code (GS1 oder IFA). Der erste Scan legt Artikel und Charge der Box fest.
   - Scans kommen in eine **Warteschlange** und werden der Reihe nach verarbeitet. Auch sehr schnelle Scans hintereinander gehen nicht verloren.
   - Jeder Scan bekommt eine eigene Rückmeldung: Ton (ok bzw. Fehler), Meldung oben und ein Eintrag unter **„Letzte Scans“**.
   - Die **Zahl der Packungen in der Box** steht groß oben rechts, darunter die angemeldete Anzahl.
   - **Packung von Hand eingeben** (1.5.0), wenn der Code nicht lesbar ist: PZN (oder GTIN), Charge, Verfall (MM/JJJJ) und Seriennummer (einmal). Odoo prüft die Prüfziffer der PZN/GTIN. Danach gelten dieselben Regeln wie beim Scan. Die Maske bleibt offen: PZN, Charge und Verfall stehen für die nächste Packung schon da, nur die Seriennummer wird neu getippt. Tippt der Handscanner versehentlich in ein Feld der Maske, wird das als normaler Scan verarbeitet.
   - Die Handeingabe ist gekennzeichnet: „Hand“ in der Liste der Box, Vermerk im Verlauf, „H“ auf der Anlage Box-Inhalt, Feld „Von Hand eingegeben“ an der Seriennummer. In der Vorprüfung und bei der VP erscheint der Hinweis „x von y Packungen von Hand eingegeben“. Er hat **keinen Einfluss auf die Ampel** (Entscheidung 14.10.2026).
   - **Entfernen** einer Packung fragt vorher nach („Ja, entfernen“ / „Nein, behalten“).
4. **Vorprüfung:** 6 Sichtprüfungen (Ja/Nein) und die Prüfungen des Systems ergeben einen Ampel-Vorschlag. Eine mildere Ampel als der Vorschlag ist nicht möglich. Wer strenger wählt, gibt einen Grund an.
5. **Drucken:** KabQ-Checkliste und Anlage Box-Inhalt, bei Rot zusätzlich das Sperrblatt. Danach Ausdruck bestätigen und Box abschließen.
   - **Jede Änderung nach dem Druck macht den Ausdruck ungültig** (Zurück, Antworten, Ampel, Bemerkung, Packungen scannen oder entfernen). Dann ist ein neuer Druck Pflicht, und die Bestätigung muss neu gesetzt werden.
   - Odoo zählt die Drucke. Ein Nachdruck trägt auf der KabQ-Checkliste **„Neuausdruck Nr. X“** mit Datum und Uhrzeit.
   - „Box abschließen“ lehnt ab, wenn die Box seit dem letzten Druck geändert wurde.
6. **Nächste Box oder Paket fertig:** Bei „Paket fertig“ wird der Eingang gebucht. Grün und Gelb landen in GreenRX/Quarantäne, Rot im GreenRX/Sperrlager. Weicht Soll von Ist ab, ist eine Begründung Pflicht. Ist noch eine Box offen (auch von einer anderen Person), nennt Odoo Box, Schritt und Person. Die Box lässt sich über ihre Box-Nummer (Scan oder Handeingabe) fortsetzen, als VP auch die Box einer anderen Person.

Tasten am PC: **F8** Drucken, **F9** Weiter bzw. Box abschließen, **Esc** Meldung schließen.

### Seriennummer und Charge in Odoo

Odoo kennt je Packung nur ein „Los/Seriennummer“-Feld. Das Modul speichert dort die **securPharm-Seriennummer** (eindeutig je Packung) und die **Charge** in einem eigenen Feld „Charge“ am Los. Charge und Verfall werden in Bestand, Wareneingang (Details) und Bewegungen neben der Seriennummer angezeigt und sind dort such- und gruppierbar.

### Regeln, die Odoo durchsetzt

- Eine Box enthält einen Artikel und eine Charge.
- Jede Seriennummer gibt es nur einmal im System. Das gilt auch für offene Erfassungen.
- Ein Doppelscan derselben Packung wird erkannt und nicht doppelt gezählt (Prüfung auf dem Server).
- Papier und Daten stimmen überein: abgeschlossen wird nur mit einem Ausdruck, der zum aktuellen Stand der Box passt.
- Die Restlaufzeit wird geprüft (Standard 3 Monate). Darunter schlägt Odoo Rot vor.
- Jede Box wird dem passenden Ticket des Retourenscheins zugeordnet (Artikel + Charge). Diese Fälle führen zum Vorschlag Gelb:
  - die Charge weicht vom Schein ab,
  - der Artikel steht nicht auf dem Schein,
  - es sind mehr Packungen als angemeldet.
- Soll/Ist wird je Ticket geführt.
- Eine Box kann nur eine Person befüllen.
- Der GreenRX-Eingang (GRXIN) wird **nur** mit „Paket fertig“ gebucht, nicht über Lager › Transfers oder die Barcode-App. Wurde ein Eingang früher schon vorzeitig gebucht, kommen weitere Boxen automatisch in einen **Nachtrags-Eingang**, den „Paket fertig“ mitbucht.
- Die VP sieht eine Box erst, wenn das ganze Paket fertig ist.
- Ware verlässt Quarantäne und Sperrlager **nur** über die VP-Freigabe. Andere Wege werden abgelehnt: Transfer, Barcode-App, Inventur oder Ausschuss.
- Eine Box in Quarantäne oder Sperrlager darf nicht ausgepackt oder umgepackt werden. Die VP-Freigabe bewegt nur genau die erfassten Seriennummern; passt der Box-Inhalt nicht mehr zur Erfassung, wird nichts bewegt (1.8.0).
- Kontextwerte aus API-Aufrufen geben keine Berechtigung: Die Sperren für Quarantäne, Sperrlager und „Paket fertig“ lassen sich nicht über mitgeschickte Werte abschalten. Die Verknüpfung eines Eingangs mit dem Vorgang ist nicht änderbar (1.8.0).
- Seriennummer, Artikel, Charge und Verfall einer erfassten Packung sind fest (nicht umbenennbar, nicht löschbar). Eine Seriennummer wird auch dann abgelehnt, wenn sie in einer abgeschlossenen Erfassung vorkommt; gleichzeitige Scans an zwei Arbeitsplätzen sichert ein eindeutiger Datenbankindex ab (1.8.0).
- Ist die gewählte Ampel strenger als der Vorschlag (auch Gelb → Rot), ist eine eigene Begründung Pflicht (1.8.0).
- Der Ausdruck wird auch ungültig, wenn sich Daten außerhalb der Box ändern, die auf dem Formblatt stehen (z. B. Absender, Lagertemperatur des Artikels). Nachdrucke nach dem Abschluss zeigen genau den bestätigten Inhalt (1.8.0).
- Formblatt: „Wareneingangsprüfung durchgeführt von“ nennt, wer die Vorprüfung abgeschlossen hat; „Erfasst von“ nennt den Erfasser. Der Abgleich PZN/Bezeichnung/Charge erfolgt mit der Ticketposition; ohne Position steht „nein“, ohne Charge im Ticket bleibt das Feld offen (1.8.0).
- Status und Ergebnisse lassen sich nicht direkt per API ändern. Alle Änderungen laufen über die geprüften Funktionen.
- Die geprüften Funktionen verlangen auf dem Server die Gruppe „GreenRX: Lager (Erfassung)“ (die VP hat sie automatisch) und einen Vorgang der eigenen Firma. Drucken und Nachdrucken darf nur der Erfasser oder eine VP.

### VP-Freigabe (Menü GreenRX › VP-Freigabe)

- Die Liste ist nach Ampel sortiert (Rot zuerst).
- 4-Augen-Prinzip: Eine VP, die an einer Box beteiligt war (angelegt, gescannt – auch abgelehnte oder wieder entfernte Scans –, Vorprüfung, Bestätigung des Ausdrucks, Abschluss), darf sie **nicht freigeben**, nur sperren. Solche Boxen stehen am Ende der Liste mit dem Hinweis „nur sperren“. Eine Sperre durch eine beteiligte VP wird im Verlauf vermerkt; freigeben kann danach nur eine andere VP.
- **Freigeben** bucht die Box nach GreenRX/Bestand. Bei einer gelben oder roten (gesperrten) Box ist eine Begründung Pflicht.
- **Sperren** bucht die Box ins Sperrlager. Ein Grund ist Pflicht.

Wo die Ware liegt: **GreenRX › Übersicht › Bestand GreenRX** (nach Lagerort und Box gruppiert, je Packung Seriennummer, Charge, Verfall, Ampel) oder **Box-Erfassungen** (Status, Lagerort, VP, Begründung je Box).

### Retourenvorgänge und Tickets

Ein Paket ist ein **Vorgang** (Referenznummer V…). Er enthält je Produkt ein **Ticket** mit PZN, erwarteter Charge und Anzahl, so wie auf dem Retourenschein.

Handscanner im Tastaturmodus müssen beim Retourenschein-QR die **Zeilenumbrüche** mitsenden. Sonst lässt sich der Code nicht eindeutig lesen, und Odoo meldet das. Mit der Handykamera funktioniert es immer.

- **ATIV-Schnittstelle (ab 1.6.0):** GreenRX › Konfiguration › Schnittstelle GreenRX-ERP, Art „ATIV-Gutschriften“.
  PLCS liest die ATIV-Gutschriften (`purchase.order`) mit Vorgang, Positionen, Tickets, Kunde, Verlauf und Dokumenten
  über die JSON-2-API (nur lesend). Abruf alle 12 Stunden, per Knopf „ATIV-Daten synchronisieren“ oder sofort nach
  einem Webhook aus ATIV. Die Daten liegen getrennt im Menü „ATIV-Gutschriften“ und füllen das Soll des
  Retourenvorgangs. Boxen, Scans, Ist und Eingänge bleiben unberührt. Laut ATIV nicht erwartete Vorgänge stehen auf
  „Nicht erwartet“; die Lagerleitung kann sie mit „Trotzdem erwarten“ öffnen.
  Einzelheiten, Feldzuordnung und Datenvertrag: [`docs/ATIV_SCHNITTSTELLE.md`](docs/ATIV_SCHNITTSTELLE.md).
- **Alte Feldzuordnung** (Art „Helpdesk-Tickets“, Stand 06/2025): Abruf auf Knopfdruck oder alle 15 Minuten.
  Vorhandene Schnittstellen behalten diese Art beim Update.
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

Die automatischen Tests (davon 5 Browser-Tests, die Chrome brauchen) decken ab:

- den GS1/IFA-Parser und den Retourenschein-QR (auch zerteilt durch den Handscanner),
- den ganzen Ablauf von Ticket bis VP-Freigabe,
- das 4-Augen-Prinzip mit allen Beteiligten einer Box (1.4.0, `TestGreenrxFourEyesAccess`),
- die Rollen- und Firmenprüfung auf dem Server und die Druckberechtigung (1.4.0),
- die rote Box und das Sperrlager,
- die Restlaufzeit,
- Soll/Ist-Abweichungen,
- die Sperre gegen Bewegungen an der Freigabe vorbei,
- die Sperre gegen direkte API-Schreibzugriffe,
- die Schnittstelle (mit simuliertem GreenRX-ERP),
- ungültigen Ausdruck nach Änderungen, Neuausdruck und Abschluss-Sperre,
- die Suche über mehr als 1.000 offene Vorgänge,
- 5 Scans direkt hintereinander in der Scan-Oberfläche (Browser-Test, `TestGreenrxScanQueue`; braucht Chrome),
- „Paket fertig“ mit offener Box einer anderen Person und mit Soll/Ist-Abweichung aus Schritt 2 (Browser-Test, `TestGreenrxPackageFinish`),
- das Update auf 1.3.1: alte Ausdrucke offener Boxen werden ungültig,
- die Sperre gegen vorzeitiges Buchen des Eingangs und den Nachtrags-Eingang (1.3.2),
- die Handeingabe einer Packung: Prüfziffer, gleiche Regeln wie beim Scan, Kennzeichnung, Rechte, Hinweis ohne Ampel-Wirkung (1.5.0, `TestManualParser`, `TestManualPack`),
- Handeingabe, Scan in ein Eingabefeld und Sicherheitsabfrage beim Entfernen in der Oberfläche (Browser-Test, `TestGreenrxManualPackUi`).
- die Befunde des Code-Reviews vom 14.10.2026 (1.8.0, `TestReviewFixes`): Kontextwerte per API, Auspacken und fremder Inhalt
  vor der Freigabe, gleichzeitige Scans, feste Seriennummern, Begründung Gelb → Rot, Ticketvergleich und Prüfer auf dem
  Formblatt, Druckinhalt bei Stammdatenänderung, seitenweiser Abruf, Firmentrennung, Menge 0, mehrdeutige GS1-Codes;
  dazu Scans mit sofortigem F9 (Browser-Test `test_scans_before_next_step_all_arrive`).
- die ATIV-Schnittstelle mit simuliertem ATIV (1.6.0): Erstübernahme, Dokumente, alle Suchmerkmale, keine Duplikate,
  Änderungen nur an Position/Ticket/Anhang/Nachricht, Schutz lokaler Wareneingangsdaten, vorübergehende und
  dauerhafte Fehler mit Wiederholung, überlappende Läufe, Statuswechsel, Stichtag, fehlende Gutschriften,
  nur lesende Aufrufe, Webhook (auch über HTTP) und Mengen-/Referenzabgleich.

Zusätzlich prüft `e2e/playwright_scan_queue.py` mit Playwright gegen einen laufenden Server, dass 5 schnelle Scans alle ankommen:

```bash
ODOO_PASSWORD=… python e2e/playwright_scan_queue.py --url http://127.0.0.1:8069 --db test_db --login admin
```

Testplan zu 1.8.0 mit druckbarem Testpaket: `docs/TESTPLAN_1.8.0.md` und `docs/testpaket_VTEST1801.pdf`.

### Offen / vor dem Echtbetrieb

- Die Druckvorlagen sind eine Nachbildung der KabQ-Checkliste. Abgleich mit dem Word-Formblatt und Freigabe durch die QMB stehen aus. Das gilt auch für die Anlage und das Sperrblatt als neue Formblätter.
- Der Test mit echten Packungen und dem echten Handscanner steht aus (GS1 und IFA).
- ATIV-Schnittstelle: API-Benutzer bei ATIV, Stichtag für alte „Picked Up“-Tickets und Test mit echten Daten stehen aus.
- Validierung über Change Control (Validierungsplan und -bericht) vor dem Produktivstart.
- Verkauf aus GreenRX/Bestand und Vernichtung gesperrter Ware sind noch nicht Teil des Moduls.


---

## blistercenter – Großhandel für Blistercenter

Regeln und Entscheidungen: [`docs/BLISTERCENTER_REGELN.md`](docs/BLISTERCENTER_REGELN.md).

### Ablauf (App „Blistercenter“)

1. **Kunden:** Blistercenter von Hand anlegen, Zahlungsmodell und Preisliste setzen (Rolle Freigabe). Portalzugang über den Knopf **Portalzugänge** am Kunden.
2. **Bestellung:** im Shop oder über **Kundenbestellungen › Telefonbestellung erfassen** (auch Knopf am Kunden). Bestellweg und Besteller sind Pflicht.
3. **Vorkasse:** Bei Bestätigung entsteht die Anzahlungsrechnung über 100 % als Entwurf. Die Buchhaltung bucht und versendet sie (Aufgabe in Odoo).
4. **Sammelbestellungen › Offene Kundenmengen:** Positionen markieren, „Zur Sammelbestellung“, dann „Anfrage erstellen“ und im Einkauf manuell bestellen. Ausnahme: „Aus Lagerbestand liefern“.
5. **Lager und Versand › Wareneingänge:** Ware mit Charge und Verfall buchen. Sie landet in der Quarantäne (WH/Blistercenter/Quarantäne).
6. **Freigabe Wareneingang (VP):** eine andere Person mit Rolle Freigabe bestätigt. Kurzläufer (Rest unter 3 Monaten) vorher mit „Kurzläufer freigeben“.
7. **Kommissionierung:** Ware ist dem Kunden fest zugeordnet. Nach dem Packen „Kommissionierung abgeschlossen“.
8. **Endkontrolle:** eine zweite Person prüft und bestätigt. Odoo prüft Zahlungssperre, Vorkasse, Chargensperre, Kurzläufer und Vollständigkeit.

### Rollen

| Rolle | Darf |
|---|---|
| Benutzer | Bestellungen erfassen, bündeln, Wareneingang buchen, kommissionieren, Endkontrolle, Charge sperren, Teillieferung mit Vermerk erlauben |
| Freigabe (VP) | zusätzlich: Shopfreischaltung, Konditionen, Zahlungssperre, Charge entsperren, Freigabe aus der Quarantäne, Kurzläufer freigeben |
| Administrator | zusätzlich: Einstellungen und Prozessregeln |

### Technik

- Eigene Lagerorte und Vorgangstypen: `WH/Blistercenter/Quarantäne`, `BCIN` (Eingang), `BCFRG` (Freigabe VP). Werden bei Installation und Update angelegt (idempotent).
- Feste Zuordnung über Odoo-Standard-Verkettung: Kundenlieferung „auf Auftrag“ ohne eigene Beschaffung, verkettet mit der Freigabe; reserviert nur deren Chargen.
- Tests: `odoo-bin ... -i blistercenter --test-enable --test-tags /blistercenter` (39 Tests, zusammen mit `plcs_greenrx` 145).
