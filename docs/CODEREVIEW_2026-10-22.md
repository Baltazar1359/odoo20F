# Code-Review 22.10.2026 zu Blistercenter 1.6.2 – Bewertung und Umsetzung (1.6.4)

Geprüfter Commit laut Review: `7230cad` (1.6.2, Kamera als Scanner). Bewertet und korrigiert auf dem aktuellen
Stand `7c71939` (1.6.3); 1.6.3 hat `receipt_scan.py` und `scan_input.js` nicht verändert.
Grundlage der Bewertung: Originalcode und Odoo-20.0-Quellcode (`web/static/src/model/relational_model`).

| Befund | Bewertung | Ursache | Korrektur |
|---|---|---|---|
| 1 Verfall beim Vorbelegen verlängert | **bestätigt** (beide Wege) | `strftime("%m/%Y")` beim Vorbelegen; „MM/JJJJ“ wird beim Hinzufügen als Monatsende gelesen: 15.03.2028 → 31.03.2028 | `expiry_text()`: TT.MM.JJJJ, MM/JJJJ nur wenn das Datum ohnehin Monatsende ist (verlustfrei) |
| 2 Übernehmen wartet nicht auf die Warteschlange | **bestätigt**, schwerer als beschrieben | Odoo wartet vor dem Speichern nur auf `NEED_LOCAL_CHANGES` und den Model-Mutex. Die eigene Promise-Kette ruft Scan 2/3 erst nach Scan 1 auf; ein Scan ohne Enter steht nur im Eingabefeld. „Hinzufügen“ ist im Test zufällig gut gegangen (Fenster lädt neu), garantiert war es nicht | Feld meldet sich auf `NEED_LOCAL_CHANGES`: schickt einen Scan ohne Enter sofort ab und wartet, bis die ganze Kette leer ist. Bleiben Scans unverarbeitet oder endete der eben verarbeitete Scan rot, setzt es `scan_hold`; der Server lehnt „Übernehmen“/„Hinzufügen“ mit diesem Text ab |
| 3 Kamera zählt dieselbe Packung mehrfach | **bestätigt** (gilt auch für den Handscanner) | Liste kennt nur Charge + Menge, keine Seriennummern | Seriennummern je Zeile (`serials`); gleicher Artikel + gleiche Seriennummer → gelb „schon gezählt“, Kamera läuft weiter (R66). Andere Seriennummer zählt, Menge von Hand und Handeingabe unverändert |
| 4 Alt+029 geht bei schnellen Folgezeichen verloren | **bestätigt** | GS per `setTimeout` und nur bei unverändertem Feldinhalt | GS sofort beim Loslassen von Alt; ein vom Browser selbst eingesetztes Zeichen (`↔`, GS) wird in `beforeinput` verworfen, Rückfall in `input` |
| 5 Fehler bei `record.update` ohne Wiederherstellung | **bestätigt**, zusätzlicher Fall gefunden | `.catch(() => {})`. Zusätzlich: bei Verbindungsabbruch verschluckt Odoo den Onchange-Fehler selbst (`onError` gibt `false` zurück), `update` endet ohne Fehler, der Scan bleibt unverarbeitet | Gescheiterte Scans bleiben erhalten (rote Box, Anzahl, Grund, „Wiederholen“/„Verwerfen“), Meldung wird rot, Kamera hält an. Unverarbeitet erkannt auch am Feldwert (Server leert `scan` immer). Wiederholen zählt nichts doppelt: die gescheiterte Anfrage hat nichts geändert, und R66 greift |

## Tests

- Neu: `blistercenter/tests/test_codereview_1022.py` – 10 Servertests, 2 Browser-Tours
  (`blistercenter_receipt_scan_race`: erste Serverantwort 800 ms verzögert, Scan ohne Enter, sofort
  „Hinzufügen“ bzw. „Übernehmen“; `blistercenter_receipt_scan_failure`: Verbindungsabbruch und Serverfehler,
  Kamera hält an, „Übernehmen“ abgelehnt, „Wiederholen“, erneuter Scan nicht doppelt).
- Erweitert: Tour `blistercenter_receipt_scan` (Alt+029 ohne Pause; Browser setzt „↔“ selbst ein;
  Kamera liest K1 zweimal), `test_wareneingang_1_6` (Vorbelegung mit vollem Datum).
- Lokal, Odoo 20.0 (`58f73b2`, 23.10.2026), Chrome headless, frische Installation GreenRX + Blistercenter +
  plcs_layout: **343 Tests, 0 Fehler**. Browser-Tests zusätzlich zweimal wiederholt: grün.
- Gegenprobe gegen 1.6.3 (neue Tests, alter Code): 12 von 27 scheitern; die 2 neuen Gutfälle laufen auch dort.
  Race-Test auf altem Code: CH-RACE-B Menge 1 statt 3, CH-RACE-C fehlt – wie im Review beschrieben.

## Noch am Gerät zu prüfen (Prüfanleitung T51–T54)

Die Tests simulieren Tastatur-, Kamera- und Netzwerkereignisse. Nicht geprüft sind echte Handscanner
(Alt+029-Timing, Browser-Eigenheiten unter Windows), die echte Kamera auf Handy/Tablet und ein echter
Netzabbruch.

## Verbleibende Risiken

- Doppelte Seriennummer wird nur innerhalb eines Fensters erkannt; wer nach „Übernehmen“ ein neues Fenster
  öffnet und dieselbe Packung erneut scannt, zählt sie wieder (Seriennummern werden im Blistercenter-Eingang
  nicht gebucht).
- Wird die Menge von Hand verringert, bleiben die Seriennummern der Zeile vermerkt.
- Scans, die nach dem Klick auf „Übernehmen“ ankommen (während Odoo schon speichert), zählen nicht mehr.
- Auf einem Browser, der für Alt+29 ein anderes Zeichen als GS/„↔“ einsetzt, entstünde ein Zusatzzeichen;
  bisher nicht beobachtet.
