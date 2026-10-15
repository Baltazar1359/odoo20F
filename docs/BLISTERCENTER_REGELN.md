# Blistercenter – Regelkatalog (Stand 15.10.2026, Modul 20.0.1.1.0)

Blistercenter sind die Kunden des PLCS-Großhandels. Sie bestellen bei PLCS im Shop, per Telefon, E-Mail oder Fax. PLCS bündelt die Mengen in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

**Status:** Etappe 1 (Gerüst) und Etappe 2 (Geschäftsregeln) sind umgesetzt. Entscheidungen von Mehrdad am 15.10.2026 sind in Teil B eingetragen. Alle Regeln mit Schalter lassen sich unter Blistercenter › Einstellungen ändern (Rolle Administrator).

## Ablauf

1. **Kunde anlegen** (von Hand): Kennzeichen „Blistercenter“, Zahlungsmodell, Preisliste. Portalzugang über den Knopf „Portalzugänge“ am Kunden (Standard-Assistent von Odoo; er verschickt die Einladung, wenn man sie dort auslöst).
2. **Bestellung**: im Shop (nur freigeschaltete Blistercenter) oder als **Telefonbestellung** (Menü Kundenbestellungen › Telefonbestellung erfassen, oder Knopf am Kunden).
3. **Vorkasse**: Bei Bestätigung legt Odoo die **Anzahlungsrechnung 100 %** als Entwurf an. Die Buchhaltung bucht und versendet sie. Gebündelt wird erst nach Zahlung.
4. **Sammelbestellung**: offene Kundenmengen bündeln → Anfrage beim Hersteller → manuell bestellen.
5. **Wareneingang** in die **Blistercenter-Quarantäne** (Vorgangstyp BCIN).
6. **Freigabe durch die VP** (Vorgangstyp BCFRG) in den Bestand. Kurzläufer brauchen eine zusätzliche Freigabe.
7. **Ware fest je Kunde**: Nach der Freigabe reservieren die Lieferungen genau die Ware aus ihrer Sammelbestellung.
8. **Kommissionierung** (Person 1) → **Endkontrolle** und Bestätigung (Person 2) → Lieferung.

## A. Regeln im Modul

| Nr. | Regel | Schalter |
|---|---|---|
| R1 | Die Regeln gelten nur für Kunden mit Kennzeichen „Blistercenter“. | – |
| R2 | Shopfreischaltung setzt nur die Rolle **Freigabe**. Odoo merkt sich wer und wann. | – |
| R3 | Warenkorb und Bestellbestätigung im Shop nur für **freigeschaltete Blistercenter**. | ja |
| R4 | Zahlungsmodell, Zahlungsbedingung und Preisliste eines Blistercenters ändert nur die Rolle **Freigabe**. | – |
| R5 | Zahlungssperre setzt und löst nur die Rolle **Freigabe**. Ein Grund ist Pflicht. | – |
| R6 | Zahlungssperre verhindert **Auftragsbestätigung** und **Bündelung**. | ja |
| R7 | Zahlungssperre verhindert den **Versand**. | ja |
| R8 | **Vorkasse:** Positionen kommen erst nach vollständiger Zahlung in eine Sammelbestellung. | ja |
| R9 | **Vorkasse:** Versand erst nach vollständiger Zahlung. | ja |
| R10 | In eine Sammelbestellung kommen nur Warenpositionen **bestätigter** Blistercenter-Aufträge. | – |
| R11 | Eine Auftragsposition kann aufgeteilt werden, aber **nie mehr als die bestellte Menge** ist in Sammelbestellungen. | – |
| R12 | Aus einer Sammelbestellung entsteht **genau eine Anfrage** beim Hersteller (Entwurf, bestellt wird manuell). | – |
| R13 | Nach Erstellung der Anfrage sind die Kundenmengen gesperrt. Storno nur, solange die Bestellung beim Hersteller nicht bestätigt ist. | – |
| R14 | Die Sammelbestellung ist **erledigt**, wenn der Wareneingang vollständig gebucht ist. | – |
| R15 | Charge **sperren darf jeder** (Grund Pflicht), **entsperren nur die Rolle Freigabe**. Gesperrte Chargen werden nie versendet und nicht freigegeben. | – |
| R16 | Einstellungen ändert nur die Rolle **Administrator**. | – |
| R17 | Benachrichtigungen sind **Aufgaben in Odoo**, keine E-Mails. | ja (je Ereignis) |
| R18 | **Bestellweg** (Shop, Telefon, E-Mail, Fax, Sonstiges) ist vor der Bestätigung Pflicht. Außer beim Shop ist der **Besteller** Pflicht (Kontakt oder Name). Odoo speichert, wer die Bestellung angenommen hat und wann. | ja |
| R19 | **Vorkasse:** Bei Bestätigung legt Odoo eine **Anzahlungsrechnung über 100 %** als Entwurf an und gibt der Buchhaltung eine Aufgabe. Kein automatisches Buchen, kein E-Mail-Versand. | ja |
| R20 | Artikel mit der Route **„Auf Auftrag beschaffen“** sperren die Bestätigung einer Blistercenter-Bestellung (sonst Doppelbestellung). | ja |
| R21 | Nur **chargengeführte Artikel mit Verfallsdatum** lassen sich bündeln. Bei Freigabe und Versand muss jede Charge ein Verfallsdatum haben. | ja |
| R22 | Ware aus Sammelbestellungen geht immer in die **Blistercenter-Quarantäne** (Vorgangstyp „Blistercenter: Wareneingang“). Eine Herstellerbestellung mit anderem Eingangstyp lässt sich nicht bestätigen. | – |
| R23 | Ware **verlässt die Quarantäne nur durch die Rolle Freigabe (VP)**, in den Bestand nur über „Blistercenter: Freigabe (VP)“. Gilt für alle Wege (Transfer, Inventur, Ausschuss, Rücksendung). | – |
| R24 | **Vier-Augen-Prinzip beim Eingang:** Wer den Wareneingang gebucht hat, darf ihn nicht selbst freigeben. | ja |
| R25 | **Kurzläufer:** Chargen mit Restlaufzeit **unter 3 Monaten** (einstellbar) brauchen eine **zusätzliche Freigabe der VP** mit Begründung – beim Einlagern und beim Versand. | ja (Monate, je Schritt) |
| R26 | **Ware fest je Kunde:** Lieferungen an Blistercenter warten auf die Ware ihrer Sammelbestellung und reservieren nur diese (kein Zugriff auf freien Bestand). Ausnahme je Position: **„Aus Lagerbestand liefern“** (nur, solange die Position nicht gebündelt ist). | ja |
| R27 | **Endkontrolle durch zweite Person:** Erst „Kommissionierung abgeschlossen“ melden, dann bestätigt eine andere Person. Jede Änderung nach dem Kommissionieren (Menge, Charge, Lagerplatz) setzt die Meldung zurück. | ja |
| R28 | **Teillieferung nur nach Rücksprache:** Standard ist Komplettlieferung. Eine Teillieferung muss in der Bestellung mit Vermerk erlaubt sein (wer, wann, mit wem abgestimmt). | ja |

## B. Entscheidungen 15.10.2026 (Mehrdad)

| Frage | Entscheidung | Umsetzung |
|---|---|---|
| Wareneingang und Versand | QM-Ablauf voll: Quarantäne + VP-Freigabe, Endkontrolle durch zweite Person. Statt Sperre bei < 12 Monaten: **Restlaufzeit unter 3 Monaten → zusätzliche VP-Freigabe**. | R22–R25, R27. Auslegung: die Zusatzfreigabe gilt beim Einlagern und beim Versand. |
| Zuordnung der Ware | Fest je Kunde | R26 |
| Vorkasse | Anzahlungsrechnung 100 % | R19 |
| Teillieferung | Nur mit Rückfrage | R28 |
| Route „Auf Auftrag beschaffen“ | (Vorschlag) verbieten | R20 |
| Telefonbestellung | PLCS muss Bestellungen telefonisch bearbeiten können | R18, Menü „Telefonbestellung erfassen“ |
| Kunden und Portal | PLCS legt Blistercenter von Hand an und erteilt den Portalzugang | Knopf „Portalzugänge“ am Kunden; Hinweis, wenn der Shop frei ist, aber kein Zugang besteht |

## C. Noch offen

1. **Zahlungssperre:** Nur manuell (so umgesetzt) oder automatisch bei überfälliger Rechnung? Kreditlimit für Kunden mit „Rechnung“?
2. **Shop:** Selbstregistrierung aus, Preise erst nach Anmeldung (Website-Einstellung)? GDP-Kundenqualifizierung als Voraussetzung für die Freischaltung?
3. **Verfall < 6 Monate im Bestand:** Heute Liste „Kurzläufer im Bestand“ und Kachel (Grenze aus R25). Automatisch sperren?
4. **Online-Zahlung im Shop:** Vorgesehen ist Vorkasse per Überweisung. Bei Online-Zahlungsanbietern Ablauf neu prüfen.
5. **Zu wenig geliefert:** Liefert der Hersteller weniger als bestellt, reicht die Ware nicht für alle Kunden der Sammelbestellung. Die Reihenfolge legt heute Odoo fest. Soll PLCS die Verteilung selbst bestimmen?
6. Aufbewahrung und Validierung (Change Control) mit QMB abstimmen.
