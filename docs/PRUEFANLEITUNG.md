# GreenRX-Wareneingang – Prüfanleitung (Odoo.sh)

## Umgebung

| | |
|---|---|
| Adresse | https://baltazar1359-odoo20.odoo.com |
| Odoo.sh-Projekt | https://www.odoo.sh/project/baltazar1359-odoo20 (Branch `main` = Production) |
| Anmeldung als Administrator | Odoo.sh › Branch `main` › **Connect** (ohne Passwort) |
| Firma | PLCS GmbH (Test), Sprache Deutsch, Zeitzone Europe/Berlin |
| Stand dieser Anleitung | `plcs_greenrx` **20.0.1.10.0** (20.10.2026) |

**Neue Funktionen vor dem Merge testen:** Jeder Feature-Branch hat bei Odoo.sh einen eigenen Dev-Build (Odoo.sh › Branch › **Connect**). Die Dev-Datenbank ist neu, englisch/USD (Odoo-Demofirma) und enthält keine PLCS-Daten und keine Testbenutzer. Sie läuft nach 1–2 Tagen ab, jeder Push erzeugt eine neue leere Datenbank. Für den 4-Augen-Test dort einen zweiten Benutzer mit VP-Rolle anlegen.

### Eingerichtet

- Modul `plcs_greenrx` installiert (installierte Version: Einstellungen › Apps › „PLCS GreenRX Wareneingang“), mit Lagerorten **GreenRX/Quarantäne, /Bestand, /Sperrlager**, Vorgangstypen **GRXIN, GRXFRG, GRXSPR** und Boxen **GX001–GX200**.
- **30 Artikel** mit PZN, GTIN-Barcode, Chargenverfolgung und Ablaufdatum (`import/greenrx_artikel_pzn.csv`).
- **100 Retourenvorgänge** mit 101 Tickets und 287 angemeldeten Packungen aus dem GreenRX-ERP (`import/greenrx_vorgaenge_letzte100.csv`, Erstimport). Ein Teil davon ist inzwischen erfasst; die Zahl offener Pakete steht in der Scan-App unter „Danach“.
- Benutzer:
  - **Administrator**: Lager (Erfassung) + VP-Freigabe + Lagerleitung.
  - **lager.test** („Lager Test (Erfassung)“): nur Erfassung.
  - **vp.test** („VP Test (Freigabe)“): VP-Freigabe.
- Passwörter setzt Mehrdad: Einstellungen › Benutzer › Benutzer öffnen › Aktion › Passwort ändern.
- **Blistercenter ist in Production nicht installiert** (Entscheidung 15.10.2026, Variante B). Blistercenter wird nur im Dev-Build getestet, siehe `docs/PRUEFANLEITUNG_BLISTERCENTER.md`.
- Selbstregistrierung auf der Login-Seite ist abgeschaltet (nur auf Einladung).
- Es wurden keine E-Mails verschickt.

## Testmaterial

**Wichtig:** Jede Seriennummer lässt sich nur einmal erfassen, jeder Vorgang nur einmal. Vor dem Test in GreenRX › Übersicht › Retourenvorgänge prüfen, ob der Vorgang des Testbogens noch „Offen“ ist und die Boxen leer sind (Übersicht › Boxen GX). Sonst einen neuen Bogen erzeugen.

| Testbogen | Inhalt |
|---|---|
| `docs/testpaket_VTEST1801.pdf` | Testplan T1–T9 (1.8.0) mit allen Codes: Retourenschein VTEST1801, Boxen GX181–GX186, 7 Packungen. Vorgang wird aus dem Retourenschein angelegt. Beschreibung: `docs/TESTPLAN_1.8.0.md` |
| `docs/greenrx_testcodes.pdf` | Retourenschein V202601693, Boxen GX001–GX003, 7 Packungen, 2 Fehlerfälle (erster Test vom 13.10.; in Production bereits erfasst) |
| `docs/testcodes_V202601607.pdf` | FORXIGA 5 mg, 3 Packungen, Boxen GX004–GX006 |

Neue Bögen (braucht `pip install zxing-cpp`):

- Paket wie VTEST1801: `python3 docs/make_testpack_1_8.py --ref VTEST1802 --box 171`
- Bogen zu einem vorhandenen Vorgang: `python3 docs/make_testcodes.py <Vorgang> --box <erste freie Box>`

Das PDF ausdrucken oder am PC öffnen und mit Handscanner bzw. Handy-Kamera lesen.

**Scannen:** „Scannen“ auf der Startseite ist für den Handscanner (der Code wird ins Suchfeld getippt) oder für die Eingabe von Hand. „Mit Kamera scannen“ nutzt die Kamera von Handy oder Tablet und erscheint nur, wenn das Gerät eine hat. Für den Lageralltag ist der Handscanner vorgesehen.

## Prüfablauf (ca. 15 Minuten)

### A. Erfassung (als Administrator oder lager.test)

Die Schritte beziehen sich auf einen Testbogen mit zwei Artikeln (z. B. `greenrx_testcodes.pdf` neu erzeugt oder VTEST-Paket); Boxnummern und Artikel entsprechend dem Bogen.

1. **GreenRX › Wareneingang scannen** öffnen. Startseite: „In Erfassung“ (angefangene Pakete) und „Danach“ (Zahl offener Pakete).
2. **Retourenschein-QR scannen.** Erwartet: Vorgang, Reklamationsnummer und Tickets wie auf dem Bogen, rechts Soll je Ticket.
3. **Erste Box scannen**, dann die Packungen des ersten Artikels scannen.
   - Einen Code zweimal scannen: wird als Doppelscan erkannt und nicht doppelt gezählt.
   - „Fehlerfall: andere Charge" in dieser Box scannen: wird abgelehnt (eine Box = ein Artikel, eine Charge).
   - **Schnell scannen:** Die Packungen so schnell wie möglich nacheinander scannen. Erwartet: Jeder Scan piept und erscheint unter „Letzte Scans“, keiner geht verloren. Die große Zahl oben rechts zeigt die Packungen in der Box.
   - **Handeingabe (1.5.0):** Eine Packung nicht scannen, sondern „Packung von Hand eingeben“: PZN, Charge, Verfall (MM/JJJJ) und Seriennummer vom Testbogen abtippen, die PZN zuerst mit einer falschen letzten Ziffer. Erwartet: Hinweis „Prüfziffer stimmt nicht“, nichts erfasst. PZN korrigieren und Enter: Packung erfasst und mit „Hand“ markiert. Eine bereits erfasste Seriennummer von Hand eingeben: wird wie beim Scan abgelehnt.
   - **Entfernen:** Bei einer Packung „entfernen“ tippen. Erwartet: Sicherheitsabfrage mit Seriennummer. „Nein, behalten“ lässt die Packung in der Box.
4. **Vorprüfung:** 6 Fragen beantworten. Bei unauffälligen Antworten erwartet: Vorschlag Grün. Eine mildere Ampel als der Vorschlag ist nicht wählbar.
5. **Drucken (F8):** KabQ-Checkliste und Anlage Box-Inhalt erscheinen als PDF. Ausdruck bestätigen, **Box abschließen (F9)**.
   - **Änderung nach dem Druck (einmal bei der ersten Box prüfen):** Nach dem Druck **Zurück**, eine Antwort ändern, wieder weiter. Erwartet: Hinweis „Der letzte Ausdruck … ist ungültig“, die Bestätigung ist weg, Abschließen geht erst nach neuem Druck. Der neue Ausdruck trägt oben rechts **„Neuausdruck Nr. 1“** mit Uhrzeit. Den alten Ausdruck vernichten.
6. **Nächste Box**: Packungen des zweiten Artikels, Vorprüfung, Drucken, Abschließen.
7. Optional eine dritte Box mit „Fehlerfall: Verfall in 6 Wochen“. Erwartet: Vorschlag **Rot** (Restlaufzeit unter 3 Monaten) und zusätzlich das Sperrblatt im Druck.
8. **Paket fertig.** Bei Abweichung Soll/Ist (z. B. durch die Zusatzbox) verlangt Odoo eine Begründung; das Feld erscheint direkt unter der Fortschrittsanzeige. Ist noch eine Box offen, nennt Odoo Box, Schritt und Person. Diese Box über „Box-Nummer von Hand eingeben“ öffnen und abschließen oder abbrechen. Danach liegt die Ware in GreenRX/Quarantäne, rote Boxen im Sperrlager.

### B. VP-Freigabe (als vp.test, also eine andere Person)

1. **GreenRX › VP-Freigabe** öffnen. Die Boxen erscheinen, Rot zuerst. Boxen, an denen man selbst beteiligt war, stehen am Ende mit „nur sperren“: freigeben muss eine andere VP (4-Augen-Prinzip).
2. **Erste Box freigeben.** Die Ware wird nach GreenRX/Bestand gebucht. Bei einer **gelben** oder **roten** Box verlangt Odoo eine Begründung.
3. **Rote Box sperren** (Grund angeben). Die Ware bleibt im Sperrlager.
   - **Erneut prüfen (1.9.1):** Von einer VP gesperrte Boxen stehen unten im Bereich „Gesperrte Boxen erneut prüfen“. Eine unbeteiligte VP kann sie mit Begründung doch freigeben.
4. Bei einer Box mit Handeingabe steht in der Liste „Hand: n“ und in der Detailansicht der gelbe Hinweis „n von m Packungen von Hand eingegeben“. Die Ampel ist dadurch nicht verändert.

### C. Kontrollen

- **GreenRX › Übersicht › Bestand GreenRX:** Ware nach Lagerort (Quarantäne, Bestand, Sperrlager) und Box gruppiert, je Packung mit Seriennummer, **Charge**, **Verfall** und Ampel. Über „Gruppieren nach“ auch nach Charge oder Verfallsmonat.
- **GreenRX › Übersicht › Box-Erfassungen:** je Box Status, aktueller Lagerort, VP und Begründung.
- **GreenRX › Übersicht › Seriennummern:** Jede Packung ist mit Charge, Verfall, Box und Ticket gespeichert.
- **Sperre gegen Umgehung:** Versuch, Ware per normalem Transfer aus GreenRX/Quarantäne umzulagern. Erwartet: Odoo lehnt das ab.
- **Eingang nur über „Paket fertig“:** Den GreenRX-Eingang (GRXIN…) eines Pakets in Erfassung unter Lager › Transfers öffnen und „Bestätigen“ klicken. Erwartet: Odoo lehnt das ab und verweist auf „Paket fertig“.
- **Suche:** Auf der Startseite nach Apotheke, Ticket-, Reklamations-, Gutschrift- oder Kundennummer oder nach dem Artikel suchen. Die Suche läuft über alle offenen Vorgänge (auch bei mehr als 1.000); Leerzeichen und Bindestriche sind egal (z. B. „k 10234“ findet „K-10234“).
- **Box-Erfassungen › Box öffnen › Verlauf:** Jeder Druck ist mit Nummer vermerkt, ein ungültig gewordener Ausdruck ebenfalls.
- **Formblätter (1.9.0):** Box-Erfassungen › Box öffnen › Reiter „Formblätter“: jedes gedruckte PDF mit Druck-Nr., Zeit, Benutzer und Status (gültig/ersetzt). Löschen oder Ersetzen lehnt Odoo ab. Von Übersicht › Seriennummern führt „Formblatt“ zum gültigen PDF. Ist die letzte Packung einer Box ausgelagert, wird die Belegung archiviert (Filter „Archiv“).
- **Tasten am PC:** F8 Drucken (Schritt 5), F9 Weiter bzw. Box abschließen (Schritte 3–5), Esc Meldung schließen. In anderen Schritten tun F8/F9 nichts. Auf manchen Laptops zusätzlich Fn drücken.
- **Neu in 1.8.0 (Code-Review 14.10.):**
  - Packungen schnell scannen und sofort „Weiter“ (F9): Alle Packungen stehen in der Box, keine wird verworfen.
  - Vorprüfung mit Vorschlag Gelb, Rot wählen ohne Bemerkung: Odoo verlangt einen Grund.
  - Box in Quarantäne unter Lager › Pakete auspacken: Odoo lehnt das ab.
  - Seriennummer einer erfassten Packung unter Lager › Seriennummern umbenennen: Odoo lehnt das ab.
  - Box drucken, danach den Absender des Vorgangs oder die Lagertemperatur des Artikels ändern: Abschluss verlangt neuen Druck.
  - Vorprüfung durch eine andere Person als den Erfasser: Formblatt nennt bei „Wareneingangsprüfung durchgeführt von“ den Prüfer, in der Anlage bei „Erfasst von“ den Erfasser.
- **Neu in 1.10.0 – Erfassung abbrechen:**
  - Retourenschein scannen, in Schritt 2 keine Box scannen, „Erfassung abbrechen“ → bestätigen. Erwartet: zurück auf „Meine Aufgaben“, Vorgang nicht mehr unter „In Erfassung“; im Vorgang (Retourenvorgänge) Status „Offen“, Verlauf nennt Person und stornierten Eingang.
  - Denselben Retourenschein erneut scannen. Erwartet: normale Erfassung mit neuem Eingang.
  - Vorgang mit erfasster Box: Knopf fehlt in der App; im Backend lehnt Odoo ab und nennt die Box.

## Hinweise

- Die Packungscodes sind Testdaten mit Präfix `TST…`. Eine Seriennummer lässt sich nur einmal erfassen.
- Weitere Testbögen: `python3 docs/make_testcodes.py <Vorgang> --box <erste freie Box>` erzeugt `docs/testcodes_<Vorgang>.pdf` mit neuen Seriennummern, z. B. `docs/testcodes_V202601607.pdf` (FORXIGA 5 mg, 3 Packungen, Boxen GX004–GX006).
- Die Vorgänge **V202601692** und **V202601693** sind ATIV-Testvorgänge aus dem ERP.
- **V202601583, V202601584 und V202601618** haben im ERP kein Ticket. Sie wurden ohne Ticketnummer übernommen.
- Die ATIV-Schnittstelle (ab 1.6.0) ist installiert, in Production aber nicht verbunden: Der Connector steht nach der Migration auf „legacy“, der API-Schlüssel fehlt. Getestet wurde sie gegen die ATIV-Testkopie (Beschreibung: `docs/ATIV_SCHNITTSTELLE.md`).
- Jeder Push auf `main` aktualisiert diese Datenbank. Die Daten bleiben erhalten, Odoo.sh legt vorher ein Backup an.
