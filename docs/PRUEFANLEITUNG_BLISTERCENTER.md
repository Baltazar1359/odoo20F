# Prüfanleitung Blistercenter (Modul 20.0.1.1.0)

Testumgebung: Odoo.sh-Dev-Build des Branches `feat/blistercenter-v2` (nicht Production). Jeder Push erzeugt eine neue, leere Datenbank.

## Vorbereitung (einmal, als Administrator)

1. **Benutzer:** mindestens drei Personen anlegen, damit die Vier-Augen-Regeln prüfbar sind:
   - Lager A und Lager B: Rolle Blistercenter „Benutzer“,
   - VP: Rolle Blistercenter „Freigabe“.
2. **Blistercenter › Einstellungen:** Zuständigkeiten setzen (Bestellungen, Buchhaltung, Sammelbestellungen, VP). Bereich „Lager-Einrichtung“ zeigt Quarantäne, BCIN und BCFRG.
3. **Hersteller:** Kontakt anlegen, Reiter Blistercenter › „Hersteller“ an.
4. **Artikel:** lagerfähig, „Nach Losen verfolgen“, „Ablaufdatum“ an, Einkaufskondition beim Hersteller, **ohne** Route „Auf Auftrag beschaffen“.
5. **Blistercenter-Kunde:** Kontakt anlegen, Reiter Blistercenter › „Blistercenter“ an, Zahlungsmodell „Vorkasse“ (als VP). Einen Ansprechpartner als Kontakt anlegen.

## Testfälle

| Nr. | Schritt | Erwartet |
|---|---|---|
| T1 | Am Kunden „Telefonbestellung erfassen“, Artikel eintragen, ohne Besteller bestätigen. | Odoo verlangt den Besteller. Mit Besteller: bestätigt; „Angenommen von/am“ ist gefüllt. |
| T2 | Bestellung aus T1 (Vorkasse) ansehen. | Anzahlungsrechnung 100 % im Entwurf, Aufgabe für die Buchhaltung, keine E-Mail. Kachel „Anzahlungsrechnungen zu buchen“ +1. |
| T3 | Offene Kundenmengen › Position bündeln, solange nicht bezahlt. | Odoo lehnt ab (Vorkasse). Rechnung buchen, Zahlung erfassen → Bündeln geht. |
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

Alle Regeln (außer den festen) lassen sich in den Einstellungen abschalten. Die Grenze für Kurzläufer ist dort einstellbar (Standard 3 Monate).
