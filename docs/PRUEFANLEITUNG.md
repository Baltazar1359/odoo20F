# GreenRX-Wareneingang – Prüfanleitung (Odoo.sh)

## Umgebung

| | |
|---|---|
| Adresse | https://baltazar1359-odoo20.odoo.com |
| Odoo.sh-Projekt | https://www.odoo.sh/project/baltazar1359-odoo20 (Branch `main` = Production) |
| Anmeldung als Administrator | Odoo.sh › Branch `main` › **Connect** (ohne Passwort) |
| Firma | PLCS GmbH (Test), Sprache Deutsch, Zeitzone Europe/Berlin |

### Eingerichtet

- Modul `plcs_greenrx` installiert, mit Lagerorten **GreenRX/Quarantäne, /Bestand, /Sperrlager**, Vorgangstypen **GRXIN, GRXFRG, GRXSPR** und Boxen **GX001–GX200**.
- **30 Artikel** mit PZN, GTIN-Barcode, Chargenverfolgung und Ablaufdatum (`import/greenrx_artikel_pzn.csv`).
- **100 Retourenvorgänge** mit 101 Tickets und 287 angemeldeten Packungen aus dem GreenRX-ERP (`import/greenrx_vorgaenge_letzte100.csv`). Alle Tickets sind einem Artikel zugeordnet.
- Benutzer:
  - **Administrator**: Lager (Erfassung) + VP-Freigabe + Lagerleitung.
  - **lager.test** (Lager Test): nur Erfassung.
  - **vp.test** (VP Test): VP-Freigabe.
- Für **lager.test** und **vp.test** ist noch **kein Passwort gesetzt**. Setzen: Einstellungen › Benutzer › Benutzer öffnen › Aktion › Passwort ändern.
- Selbstregistrierung auf der Login-Seite ist abgeschaltet (nur auf Einladung).
- Es wurden keine E-Mails verschickt.

## Testmaterial

`docs/greenrx_testcodes.pdf` enthält:

- Retourenschein-QR für **V202601693**,
- Box-Codes **GX001–GX003**,
- 7 Packungscodes passend zum Schein,
- 2 Fehlerfälle.

Das PDF ausdrucken oder am PC öffnen und mit Handy-Kamera bzw. Handscanner lesen.

## Prüfablauf (ca. 15 Minuten)

### A. Erfassung (als Administrator oder lager.test)

1. **GreenRX › Wareneingang scannen** öffnen. Auf der Startseite stehen 100 offene Pakete.
2. **Retourenschein-QR scannen.** Erwartet: Vorgang V202601693, Reklamation S23124, zwei Tickets (BRILIQUE 3 Packungen, BEVESPI 4 Packungen).
3. **Box GX001 scannen**, dann die 3 BRILIQUE-Packungen scannen.
   - Einen Code zweimal scannen: wird als Doppelscan erkannt und nicht doppelt gezählt.
   - „Fehlerfall: andere Charge" in dieser Box scannen: wird abgelehnt (eine Box = ein Artikel, eine Charge).
4. **Vorprüfung:** 6 Fragen beantworten. Bei unauffälligen Antworten erwartet: Vorschlag Grün. Eine mildere Ampel als der Vorschlag ist nicht wählbar.
5. **Drucken (F8):** KabQ-Checkliste und Anlage Box-Inhalt erscheinen als PDF. Ausdruck bestätigen, **Box abschließen (F9)**.
6. **Nächste Box GX002**: die 4 BEVESPI-Packungen, Vorprüfung, Drucken, Abschließen.
7. Optional **GX003** mit „Fehlerfall: Verfall in 6 Wochen". Erwartet: Vorschlag **Rot** (Restlaufzeit unter 3 Monaten) und zusätzlich das Sperrblatt im Druck.
8. **Paket fertig.** Bei Abweichung Soll/Ist (z. B. durch die Zusatzbox) verlangt Odoo eine Begründung. Danach liegt die Ware in GreenRX/Quarantäne, rote Boxen im Sperrlager.

### B. VP-Freigabe (als vp.test, also eine andere Person)

1. **GreenRX › VP-Freigabe** öffnen. Die Boxen erscheinen, Rot zuerst. Eigene Boxen sieht man dort nicht (4-Augen-Prinzip).
2. **GX001 freigeben.** Die Ware wird nach GreenRX/Bestand gebucht. Bei einer **gelben** oder **roten** Box verlangt Odoo eine Begründung.
3. **GX003 sperren** (Grund angeben). Die Ware bleibt im Sperrlager.

### C. Kontrollen

- **GreenRX › Übersicht › Bestand GreenRX:** Ware nach Lagerort (Quarantäne, Bestand, Sperrlager) und Box gruppiert, je Packung mit Seriennummer, **Charge**, **Verfall** und Ampel. Über „Gruppieren nach“ auch nach Charge oder Verfallsmonat.
- **GreenRX › Übersicht › Box-Erfassungen:** je Box Status, aktueller Lagerort, VP und Begründung.
- **GreenRX › Übersicht › Seriennummern:** Jede Packung ist mit Charge, Verfall, Box und Ticket gespeichert.
- **Sperre gegen Umgehung:** Versuch, Ware per normalem Transfer aus GreenRX/Quarantäne umzulagern. Erwartet: Odoo lehnt das ab.
- **Suche:** Auf der Startseite nach Apotheke, Ticket- oder Reklamationsnummer suchen.

## Hinweise

- Die Packungscodes sind Testdaten mit Präfix `TST…`. Eine Seriennummer lässt sich nur einmal erfassen.
- Weitere Testbögen: `python3 docs/make_testcodes.py <Vorgang> --box <erste freie Box>` erzeugt `docs/testcodes_<Vorgang>.pdf` mit neuen Seriennummern, z. B. `docs/testcodes_V202601607.pdf` (FORXIGA 5 mg, 3 Packungen, Boxen GX004–GX006).
- Die Vorgänge **V202601692** und **V202601693** sind ATIV-Testvorgänge aus dem ERP.
- **V202601583, V202601584 und V202601618** haben im ERP kein Ticket. Sie wurden ohne Ticketnummer übernommen.
- Die Schnittstelle zum GreenRX-ERP ist installiert, aber nicht verbunden. Für den Abruf fehlen ein API-Benutzer und die Anpassung an die aktuelle ERP-Struktur (Gutschrift/Vorgang).
- Jeder Push auf `main` aktualisiert diese Datenbank. Die Daten bleiben erhalten, Odoo.sh legt vorher ein Backup an.
