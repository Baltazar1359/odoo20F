# Testplan plcs_greenrx 1.8.0 (Code-Review 14.10.2026)

Zum Ausdrucken: **`docs/testpaket_VTEST1801.pdf`** – Teil A Testplan T1–T9 mit Feldern zum Abhaken,
Teil B alle Codes (Retourenschein-QR, Boxen GX181–GX186, securPharm-DataMatrix).

## Ablauf

| Fall | Rolle | Was geprüft wird | Befund |
|---|---|---|---|
| Vorbereitung | Administrator | Benutzer lager.test und vp.test, Boxen GX181–GX186 leer, PC mit Handscanner | – |
| T1 | lager.test | 3 Packungen ohne Pause scannen, sofort F9: alle in der Box. Entfernen/neu scannen: Paketübersicht stimmt. Formblatt: PZN/Bezeichnung/Charge = ja, Prüfer und Erfasser = lager.test | R03, R16, R10, R18 |
| T2 | lager.test | Vorschlag Gelb, Rot gewählt: ohne Grund kein Weiter | R09 |
| T3 | lager.test | Artikel nicht auf dem Schein: Formblatt „nein – keine Position im Ticket“ | R10 |
| T4 | lager.test, vp.test | VP übernimmt die Vorprüfung: Formblatt Prüfer = vp.test, Erfasser = lager.test | R18 |
| T5 | lager.test, Administrator | Lagertemperatur nach dem Druck geändert: Abschluss erst nach Neudruck und neuer Bestätigung | R11 |
| T6 | lager.test | Packung aus T1 noch einmal: „Diese Packung ist schon im System“ | R08 |
| T7 | lager.test | Paket fertig mit Begründung (nicht angemeldeter Artikel) | – |
| T8 | Administrator | Box in Quarantäne auspacken und Seriennummer umbenennen: abgelehnt | R02, R08 |
| T9 | vp.test, Administrator | Freigaben, „nur sperren“ für beteiligte VP, Lagerorte stimmen | 4-Augen |

Nur automatisch geprüft (Testklasse `TestReviewFixes`): R01, R05, R06, R07, R12, R13, R14, R15.

## Automatische Prüfung des Testpakets

`docs/check_testpack_1_8.py` liest alle Codes aus dem PDF zurück und spielt T1–T9 mit genau diesen Codes
in einer **lokalen** Datenbank durch (nicht in Production):

```bash
TESTPACK=docs/testpaket_VTEST1801 odoo-bin shell -c odoo.conf -d <lokale_db> --no-http < docs/check_testpack_1_8.py
```

Ergebnis am 15.10.2026: 41 Prüfungen bestanden – auf einer frischen Datenbank und auf einer von 1.3.2
(Stand Production) auf 1.8.0 aktualisierten Datenbank mit offenen, wartenden, gesperrten und freigegebenen Boxen.

## Neues Testpaket

Jede Seriennummer lässt sich nur einmal erfassen. Für einen zweiten Durchlauf ein neues Paket erzeugen:

```bash
python3 docs/make_testpack_1_8.py --ref VTEST1802 --box 171
```
