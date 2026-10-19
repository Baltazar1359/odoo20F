# Prüfanleitung Blistercenter (Modul 20.0.1.2.0)

Testumgebung: Odoo.sh-Dev-Build des Branches `feat/blistercenter-rechnung` (nicht Production). Jeder Push erzeugt eine neue, leere Datenbank.

## Vorbereitung (einmal, als Administrator)

0. **Testdaten (Skript):** Odoo.sh › Branch `feat/blistercenter-rechnung` › SHELL, dann
   `odoo-bin shell --no-http < ~/src/user/docs/blistercenter_testdaten/testdaten.py`.
   Legt an: PLCS-Logo (Firma und Shop), Hersteller „AstraZeneca GmbH (Test)“, 20 Arzneimittel mit PZN, GTIN,
   Charge + Verfall, Shop-Freigabe, Fakturierung nach Lieferung, Einkaufskondition und Produktfoto – darunter
   Forxiga, Xigduo und Tagrisso mit HAP (aus dem Apothekenverkaufspreis gerechnet, Testwert) –, drei Testkunden
   „Blistercenter Musterstadt (Test)“ (0 %, 14 Tage), „Blistercenter Rabatt 1 % (Test)“ (7 Tage),
   „Blistercenter Rabatt 2 % (Test)“ (14 Tage, Überweisung). Mehrfach ausführbar. Einkaufspreise sind fiktiv.
   Damit entfallen die Punkte 3–5 unten; Benutzer (Punkt 1) und Portalzugang legt das Skript nicht an.

1. **Benutzer:** mindestens drei Personen anlegen, damit die Vier-Augen-Regeln prüfbar sind:
   - Lager A und Lager B: Rolle Blistercenter „Benutzer“,
   - VP: Rolle Blistercenter „Freigabe“.
2. **Blistercenter › Einstellungen:** Zuständigkeiten setzen (Bestellungen, Buchhaltung, Sammelbestellungen, VP). Bereich „Lager-Einrichtung“ zeigt Quarantäne, BCIN und BCFRG. Regeln „Zahlungsziel Pflicht“, „Kundenrabatt fest“, „Rechnung je Lieferung“ sind an.
3. **Hersteller:** Kontakt anlegen, Reiter Blistercenter › „Hersteller“ an.
4. **Artikel:** lagerfähig, „Nach Losen verfolgen“, „Ablaufdatum“ an, Fakturierung „gelieferte Mengen“, Verkaufspreis = HAP, Einkaufskondition beim Hersteller, **ohne** Route „Auf Auftrag beschaffen“.
5. **Blistercenter-Kunde:** Kontakt anlegen, Reiter Blistercenter › „Blistercenter“ an, Zahlungsart „Lastschrift (DATEV)“, Zahlungsziel, Rabatt (als VP). Einen Ansprechpartner als Kontakt anlegen.

## Testfälle

| Nr. | Schritt | Erwartet |
|---|---|---|
| T1 | Am Kunden „Telefonbestellung erfassen“, Artikel eintragen, ohne Besteller bestätigen. | Odoo verlangt den Besteller. Mit Besteller: bestätigt; „Angenommen von/am“ ist gefüllt. |
| T2 | Bestellung aus T1 ansehen, „Proforma drucken“. | Keine Rechnung im Auftrag. PDF „Proforma-Rechnung“ mit Hinweis „Proforma – keine Rechnung“ und Lastschrift-Hinweis. Keine Buchung, keine E-Mail. |
| T3 | Blistercenter › Zu bestellen. | Position erscheint unter dem Hersteller, ohne Zahlung bündelbar. Es gibt keine Herstellerbestellung, bis PLCS „Anfrage erstellen“ wählt. |
| T4 | Sammelbestellung › Anfrage erstellen › im Einkauf bestätigen. | Eingangstyp „Blistercenter: Wareneingang“, Ziel Quarantäne. |
| T5 | Als Lager A Wareneingang mit Charge und Verfall (in 2 Jahren) buchen. | Ware in WH/Blistercenter/Quarantäne. Freigabe-Vorgang (BCFRG) entsteht, Aufgabe für die VP. |
| T6 | Freigabe als Lager B bestätigen. | Abgelehnt (nur VP). Als VP: bestätigt. |
| T7 | Wie T5, aber VP bucht den Eingang selbst und will freigeben. | Abgelehnt (Vier-Augen). Andere VP: geht. |
| T8 | Eingang mit Verfall in 1 Monat, Freigabe als VP ohne Zusatzfreigabe. | Abgelehnt (Kurzläufer). „Kurzläufer freigeben“ mit Begründung → Freigabe geht. Beim Versand erneut Kurzläufer-Freigabe nötig. |
| T9 | Lieferung des Kunden öffnen (vor und nach der Freigabe). | Vorher nichts reserviert, auch wenn freier Bestand da ist. Nachher genau die Charge aus T5. |
| T10 | Lager A: „Kommissionierung abgeschlossen“, dann selbst bestätigen. | Abgelehnt (Endkontrolle). Lager B bestätigt → geliefert. |
| T11 | Nach „Kommissionierung abgeschlossen“ die Charge ändern. | Meldung wird zurückgesetzt; erneut kommissionieren. |
| T12 | Nur einen Teil liefern. | Abgelehnt. In der Bestellung „Teillieferung erlauben“ mit Vermerk → Teillieferung geht, Rest bleibt offen. |
| T13 | Charge sperren (Lager A, mit Grund), Lieferung bestätigen. | Abgelehnt. Entsperren nur VP. |
| T14 | Ware per normalem Transfer aus der Quarantäne in den Bestand umlagern. | Abgelehnt (nur über Freigabe VP). |
| T15 | Position „Aus Lagerbestand liefern“ (ohne Sammelbestellung). | Lieferung reserviert aus freiem Bestand; Position lässt sich nicht mehr bündeln. |
| T16 | Kunde mit Shop frei, ohne Portalzugang öffnen. | Hinweis „kein Portalzugang“. Knopf „Portalzugänge“ öffnet den Standard-Assistenten. |
| T17 | Artikel mit Route „Auf Auftrag beschaffen“ bestellen. | Bestätigung abgelehnt. |
| T18 | Je ein Auftrag für die Kunden mit 0 %, 1 %, 2 % (z. B. Forxiga 10 mg 98 St.). | Preis = HAP (178,06 €), Rabatt 0 / 1 / 2 % in der Position. Rabatt in einer Position auf 5 % ändern → Bestätigung abgelehnt. Am Kunden 3 % geht nicht (nur 0, 1, 2). Lager A kann den Rabatt nicht ändern, VP schon. |
| T19 | Kunde ohne Zahlungsziel: Bestellung bestätigen. | Abgelehnt. Mit 7 oder 14 Tagen: geht, Auftrag zeigt das Zahlungsziel. |
| T20 | Teillieferung (T12) bestätigen, danach den Rest liefern. | Nach jeder Lieferung genau eine Rechnung im Entwurf über die gelieferte Menge, mit Lieferschein-Nummer und Lastschrift-Hinweis; Aufgabe für die Buchhaltung; Kachel „Rechnungen zu buchen“. |
| T21 | Rechnung aus T20 buchen, Rechnungsdatum so zurücksetzen, dass das Zahlungsziel überschritten ist. Neue Bestellung und Lieferung öffnen. | Gelber Hinweis „überfällige Rechnung(en)“ mit Betrag in Auftrag, Lieferung und Kunde; Kachel „Überfällige Rechnungen“. Bestätigen und Liefern bleiben möglich. Nach Zahlung verschwindet der Hinweis. |

Alle Regeln (außer den festen) lassen sich in den Einstellungen abschalten. Die Grenze für Kurzläufer ist dort einstellbar (Standard 3 Monate).
