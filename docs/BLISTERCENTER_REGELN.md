# Blistercenter – Regelkatalog (Stand 19.10.2026, Modul 20.0.1.2.0)

Blistercenter sind die Kunden des PLCS-Großhandels. Sie bestellen bei PLCS im Shop, per Telefon, E-Mail oder Fax. PLCS bündelt die Mengen in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

**Status:** Etappe 1 (Gerüst) und Etappe 2 (Geschäftsregeln) sind umgesetzt. Entscheidungen von Mehrdad am 15.10.2026 und 19.10.2026 (Gespräch mit Herrn Krebs) sind in Teil B eingetragen. **Seit 1.2.0 entfällt die Vorkasse.** Alle Regeln mit Schalter lassen sich unter Blistercenter › Einstellungen ändern (Rolle Administrator).

## Ablauf

1. **Kunde anlegen** (von Hand): Kennzeichen „Blistercenter“, Zahlungsart (Lastschrift über DATEV), **Zahlungsziel 7 oder 14 Tage**, **Rabatt 0, 1 oder 2 % auf den HAP**. Portalzugang über den Knopf „Portalzugänge“ am Kunden (Standard-Assistent von Odoo; er verschickt die Einladung, wenn man sie dort auslöst).
2. **Bestellung**: im Shop (nur freigeschaltete Blistercenter) oder als **Telefonbestellung** (Menü Kundenbestellungen › Telefonbestellung erfassen, oder Knopf am Kunden).
3. **Proforma-Rechnung**: Knopf „Proforma drucken“ im bestätigten Auftrag. Nur Information über Menge und Preis, keine Buchung, kein automatischer Versand.
4. **Zu bestellen / Sammelbestellung**: PLCS sieht offene Kundenmengen je Hersteller und entscheidet selbst, wann bestellt wird: bündeln → Anfrage beim Hersteller → manuell bestellen. Odoo bestellt nie automatisch.
5. **Wareneingang** in die **Blistercenter-Quarantäne** (Vorgangstyp BCIN).
6. **Freigabe durch die VP** (Vorgangstyp BCFRG) in den Bestand. Kurzläufer brauchen eine zusätzliche Freigabe.
7. **Ware fest je Kunde**: Nach der Freigabe reservieren die Lieferungen genau die Ware aus ihrer Sammelbestellung.
8. **Kommissionierung** (Person 1) → **Endkontrolle** und Bestätigung (Person 2) → Lieferung (komplett oder, nach Rücksprache, als Teillieferung).
9. **Rechnung je Lieferung**: Nach jeder Lieferung legt Odoo die Rechnung über die gelieferte Menge als Entwurf an. Die Buchhaltung bucht und versendet sie.
10. **Zahlung per Lastschrift über DATEV**: Mandat und Einzug nur in DATEV. PLCS bucht die Zahlung später in Odoo ein.

## A. Regeln im Modul

| Nr. | Regel | Schalter |
|---|---|---|
| R1 | Die Regeln gelten nur für Kunden mit Kennzeichen „Blistercenter“. | – |
| R2 | Shopfreischaltung setzt nur die Rolle **Freigabe**. Odoo merkt sich wer und wann. | – |
| R3 | Warenkorb und Bestellbestätigung im Shop nur für **freigeschaltete Blistercenter**. | ja |
| R4 | Zahlungsart, Zahlungsziel, Rabatt und Preisliste eines Blistercenters ändert nur die Rolle **Freigabe**. | – |
| R5 | Zahlungssperre setzt und löst nur die Rolle **Freigabe**. Ein Grund ist Pflicht. | – |
| R6 | Zahlungssperre verhindert **Auftragsbestätigung** und **Bündelung**. | ja |
| R7 | Zahlungssperre verhindert den **Versand**. | ja |
| R8 | ~~Vorkasse: Positionen kommen erst nach vollständiger Zahlung in eine Sammelbestellung.~~ **Entfallen 1.2.0.** | – |
| R9 | ~~Vorkasse: Versand erst nach vollständiger Zahlung.~~ **Entfallen 1.2.0.** | – |
| R10 | In eine Sammelbestellung kommen nur Warenpositionen **bestätigter** Blistercenter-Aufträge. | – |
| R11 | Eine Auftragsposition kann aufgeteilt werden, aber **nie mehr als die bestellte Menge** ist in Sammelbestellungen. | – |
| R12 | Aus einer Sammelbestellung entsteht **genau eine Anfrage** beim Hersteller (Entwurf, bestellt wird manuell). | – |
| R13 | Nach Erstellung der Anfrage sind die Kundenmengen gesperrt. Storno nur, solange die Bestellung beim Hersteller nicht bestätigt ist. | – |
| R14 | Die Sammelbestellung ist **erledigt**, wenn der Wareneingang vollständig gebucht ist. | – |
| R15 | Charge **sperren darf jeder** (Grund Pflicht), **entsperren nur die Rolle Freigabe**. Gesperrte Chargen werden nie versendet und nicht freigegeben. | – |
| R16 | Einstellungen ändert nur die Rolle **Administrator**. | – |
| R17 | Benachrichtigungen sind **Aufgaben in Odoo**, keine E-Mails. | ja (je Ereignis) |
| R18 | **Bestellweg** (Shop, Telefon, E-Mail, Fax, Sonstiges) ist vor der Bestätigung Pflicht. Außer beim Shop ist der **Besteller** Pflicht (Kontakt oder Name). Odoo speichert, wer die Bestellung angenommen hat und wann. | ja |
| R19 | ~~Vorkasse: Anzahlungsrechnung über 100 % bei Bestätigung.~~ **Entfallen 1.2.0** (ersetzt durch R29, R32). Kachel „Anzahlungsrechnungen zu buchen“ und „Warten auf Zahlung“ entfallen. | – |
| R20 | Artikel mit der Route **„Auf Auftrag beschaffen“** sperren die Bestätigung einer Blistercenter-Bestellung (sonst Doppelbestellung). | ja |
| R21 | Nur **chargengeführte Artikel mit Verfallsdatum** lassen sich bündeln. Bei Freigabe und Versand muss jede Charge ein Verfallsdatum haben. | ja |
| R22 | Ware aus Sammelbestellungen geht immer in die **Blistercenter-Quarantäne** (Vorgangstyp „Blistercenter: Wareneingang“). Eine Herstellerbestellung mit anderem Eingangstyp lässt sich nicht bestätigen. | – |
| R23 | Ware **verlässt die Quarantäne nur durch die Rolle Freigabe (VP)**, in den Bestand nur über „Blistercenter: Freigabe (VP)“. Gilt für alle Wege (Transfer, Inventur, Ausschuss, Rücksendung). | – |
| R24 | **Vier-Augen-Prinzip beim Eingang:** Wer den Wareneingang gebucht hat, darf ihn nicht selbst freigeben. | ja |
| R25 | **Kurzläufer:** Chargen mit Restlaufzeit **unter 3 Monaten** (einstellbar) brauchen eine **zusätzliche Freigabe der VP** mit Begründung – beim Einlagern und beim Versand. | ja (Monate, je Schritt) |
| R26 | **Ware fest je Kunde:** Lieferungen an Blistercenter warten auf die Ware ihrer Sammelbestellung und reservieren nur diese (kein Zugriff auf freien Bestand). Ausnahme je Position: **„Aus Lagerbestand liefern“** (nur, solange die Position nicht gebündelt ist). | ja |
| R27 | **Endkontrolle durch zweite Person:** Erst „Kommissionierung abgeschlossen“ melden, dann bestätigt eine andere Person. Jede Änderung nach dem Kommissionieren (Menge, Charge, Lagerplatz) setzt die Meldung zurück. | ja |
| R28 | **Teillieferung nur nach Rücksprache:** Standard ist Komplettlieferung. Eine Teillieferung muss in der Bestellung mit Vermerk erlaubt sein (wer, wann, mit wem abgestimmt). | ja |
| R29 | **Proforma-Rechnung** aus dem bestätigten Auftrag (Knopf „Proforma drucken“). Aufdruck „Proforma – keine Rechnung“. Keine Buchung, kein automatischer Versand. Standard-Proforma von Odoo, für die Rolle Blistercenter freigegeben. | – |
| R30 | **Zu bestellen:** Kein automatischer Einkauf. Liste und Kachel „Zu bestellen“ zeigen offene Kundenmengen **je Hersteller** (aus der Einkaufskondition). PLCS bündelt und bestellt manuell. | – |
| R31 | **Zahlungsziel Pflicht:** Ohne Zahlungsbedingung am Kunden lässt sich die Bestellung nicht bestätigen. Das Modul legt „7 Tage netto“ und „14 Tage netto“ an; der Auftrag übernimmt das Ziel des Kunden. | ja |
| R32 | **Rechnung je Lieferung:** Nach jeder Lieferung legt Odoo die Rechnung über die **gelieferte Menge** als Entwurf an (mit Verweis auf die Lieferung) und gibt der Buchhaltung eine Aufgabe. Kein Buchen, keine E-Mail. Artikel müssen „Fakturierung: gelieferte Mengen“ haben, sonst ist die Bestätigung gesperrt. | ja |
| R33 | **Lastschrift über DATEV:** Zahlungsart „Lastschrift (DATEV)“ am Kunden. Rechnung (und Proforma) tragen den Hinweis auf den SEPA-Lastschrifteinzug. Mandat nur in DATEV, keine Mandatsreferenz in Odoo. | – |
| R34 | **Rabatt je Kunde:** Verkaufspreis = **HAP** (Verkaufspreis am Artikel). Je Kunde **0, 1 oder 2 %** auf alle Artikel, andere Werte nicht möglich. Ein abweichender Rabatt in einer Position sperrt die Bestätigung. Der Preis zum Zeitpunkt des Auftrags gilt. | ja |
| R35 | **Preise von Hand:** keine Lauer-Taxe. HAP am Artikel, Einkaufspreis je Hersteller und Artikel als Einkaufskondition. | – |
| R36 | **Überfällige Rechnungen:** Auftrag, Lieferung und Kunde zeigen einen deutlichen Hinweis (Anzahl, Betrag). **Keine Sperre.** Kachel „Überfällige Rechnungen“. | – |

## B. Entscheidungen 15.10.2026 (Mehrdad)

| Frage | Entscheidung | Umsetzung |
|---|---|---|
| Wareneingang und Versand | QM-Ablauf voll: Quarantäne + VP-Freigabe, Endkontrolle durch zweite Person. Statt Sperre bei < 12 Monaten: **Restlaufzeit unter 3 Monaten → zusätzliche VP-Freigabe**. | R22–R25, R27. Auslegung: die Zusatzfreigabe gilt beim Einlagern und beim Versand. |
| Zuordnung der Ware | Fest je Kunde | R26 |
| Vorkasse | 15.10.: Anzahlungsrechnung 100 %. **19.10.: entfällt** | R19 entfallen |
| Rechnung (19.10.) | Proforma nur zur Information; Rechnung je Lieferung; Zahlungsziel fest je Kunde (7 oder 14 Tage) | R29, R31, R32 |
| Zahlung (19.10.) | Lastschrift über DATEV, Mandat nur in DATEV; PLCS bucht die Zahlung später ein | R33 |
| Einkauf (19.10.) | PLCS entscheidet, wann gesammelt beim Hersteller bestellt wird | R30 |
| Preise (19.10.) | Rabatt 0/1/2 % auf HAP, gleich für alle Artikel; keine Lauer-Taxe, Preise von Hand; Einkaufspreis individuell | R34, R35 |
| Offene Posten (19.10.) | Nur Hinweis, keine Sperre | R36 |
| Teillieferung | Nur mit Rückfrage | R28 |
| Route „Auf Auftrag beschaffen“ | (Vorschlag) verbieten | R20 |
| Telefonbestellung | PLCS muss Bestellungen telefonisch bearbeiten können | R18, Menü „Telefonbestellung erfassen“ |
| Kunden und Portal | PLCS legt Blistercenter von Hand an und erteilt den Portalzugang | Knopf „Portalzugänge“ am Kunden; Hinweis, wenn der Shop frei ist, aber kein Zugang besteht |

## C. Noch offen

1. ~~Zahlungssperre automatisch?~~ Entschieden 19.10.: bei überfälligen Rechnungen nur Hinweis (R36). Die manuelle Zahlungssperre bleibt.
2. **Shop:** Selbstregistrierung aus, Preise erst nach Anmeldung (Website-Einstellung)? GDP-Kundenqualifizierung als Voraussetzung für die Freischaltung?
3. **Verfall < 6 Monate im Bestand:** Heute Liste „Kurzläufer im Bestand“ und Kachel (Grenze aus R25). Automatisch sperren?
4. **Online-Zahlung im Shop:** nicht vorgesehen (Lastschrift über DATEV). Im Shop keinen Zahlungsanbieter aktivieren.
5. **Zu wenig geliefert:** Liefert der Hersteller weniger als bestellt, reicht die Ware nicht für alle Kunden der Sammelbestellung. Die Reihenfolge legt heute Odoo fest. Soll PLCS die Verteilung selbst bestimmen?
6. Aufbewahrung und Validierung (Change Control) mit QMB abstimmen.
7. **DATEV-Export:** Der Export ist Teil von Odoo Enterprise (Buchhaltungsberichte). Im Dev-Build prüfen; Kontenrahmen (SKR03) erst nach Freigabe einrichten.
8. **HAP:** Testwerte aus dem Apothekenverkaufspreis gerechnet. Für den Echtbetrieb echte HAP von PLCS übernehmen.
