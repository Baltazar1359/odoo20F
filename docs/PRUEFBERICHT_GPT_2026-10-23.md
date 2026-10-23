# GPT-Prüfbericht 23.10.2026 – Umsetzung (blistercenter 1.6.3)

Grundlage des Berichts: `main` mit Blistercenter 1.2.1 / GreenRX 1.9.2 (Stand 19.10.2026).
Geprüft gegen den aktuellen Stand `fix/wareneingang-einfach` (Blistercenter 1.6.2, GreenRX 1.10.1).
Der Bericht lag als Zusammenfassung vor (nicht als PDF); die Befunde wurden im Code nachvollzogen.

| Befund | Stand vor 1.6.3 | Umsetzung |
|---|---|---|
| Rechnung bei mehreren Lieferungen zusammen | **schon behoben in 1.5.1** (R51, Test `test_two_deliveries_together_get_own_invoices`) | – |
| Beschaffungszuordnung: Einkaufsposition ersetzen löst Verbindung, Mengenprüfung je Artikel | bestätigt | R61: Position mit Kundenmengen nicht löschen/ersetzen/Artikel tauschen; Prüfung je Position und Verbindung beim Bestätigen |
| Verfallene Ware über Kurzläufer-Freigabe | bestätigt | R62: verfallene Chargen nie freigeben/versenden, Kurzläufer-Freigabe lehnt sie ab |
| Offene Posten: Firmen und Währungen gemischt | **schon behoben in 1.5.1** (nur eigene Firma, Betrag in Firmenwährung; Test `test_overdue_only_own_company`) | – |
| HAP-Grundpreis nicht geprüft | **teilweise behoben in 1.5.1** (Preis = HAP, R53) | R64: HAP 0 € blockiert die Bestätigung |
| Widersprüchliche Blistercenter-Zuordnung | bestätigt (Lieferung: Auftrag ODER Kontakt; Bündeln: aktuelles Kennzeichen) | R63: überall die bei Bestätigung festgehaltene Zuordnung des Auftrags |
| Menü „Kurzläufer“ ≠ Kachel | bestätigt (Menü ohne Grenze) | R65: Menü öffnet die Liste der Kachel |

Tests: `blistercenter/tests/test_pruefbericht_1023.py` (12). Gegenprobe gegen 1.6.2: 10 von 12 scheitern
(die übrigen 2 sind Gutfälle).
