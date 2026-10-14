# Blistercenter – Regelkatalog (Entwurf, Stand 14.10.2026)

Blistercenter sind die Kunden des PLCS-Großhandels. Sie bestellen bei PLCS (Shop oder Auftrag). PLCS bündelt die Mengen in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

**Status:** Etappe 1 (Modulgerüst) ist umgesetzt. Die Regeln in Teil A sind im Modul aktiv. Sie stammen aus dem Auftrag und aus dem QM-Handbuch (Vorkasse-Ablauf, Sperren/Freigeben). **Bitte jede Regel bestätigen oder korrigieren.** Teil B sind offene Fragen für Etappe 2.

## A. Umgesetzte Regeln (bitte bestätigen)

| Nr. | Regel | Abschaltbar | Quelle |
|---|---|---|---|
| R1 | Die Regeln gelten nur für Kunden mit Kennzeichen „Blistercenter“. | – | Auftrag |
| R2 | Shopfreischaltung setzt nur die Rolle **Freigabe**. Odoo merkt sich wer und wann. | – | Auftrag (Rollenrechte) |
| R3 | Warenkorb und Bestellbestätigung im Shop nur für **freigeschaltete Blistercenter**. Andere Besucher können nichts in den Warenkorb legen. | ja | Auftrag |
| R4 | Zahlungsmodell, Zahlungsbedingung und Preisliste (individuelle Preise/Rabatte) eines Blistercenters ändert nur die Rolle **Freigabe**. | – | Auftrag (Rollenrechte) |
| R5 | Zahlungssperre setzt und löst nur die Rolle **Freigabe**. Ein Grund ist Pflicht. | – | Annahme |
| R6 | Zahlungssperre verhindert **Auftragsbestätigung** und **Bündelung** in eine Sammelbestellung. | ja | Annahme |
| R7 | Zahlungssperre verhindert den **Versand**. | ja | Annahme |
| R8 | **Vorkasse:** Positionen kommen erst nach vollständiger Zahlung in eine Sammelbestellung. Bezahlt heißt: gebuchte Rechnungen bzw. Anzahlungsrechnungen zum Auftrag sind bezahlt und decken den Auftragsbetrag. | ja | QM-Handbuch AA 3.4.1.2 |
| R9 | **Vorkasse:** Versand erst nach vollständiger Zahlung. | ja | Annahme |
| R10 | In eine Sammelbestellung kommen nur Warenpositionen **bestätigter** Blistercenter-Aufträge. | – | Auftrag |
| R11 | Eine Auftragsposition kann aufgeteilt werden, aber **nie mehr als die bestellte Menge** ist in Sammelbestellungen. Stornierte Sammelbestellungen geben die Menge wieder frei. | – | Auftrag (keine doppelten Daten) |
| R12 | Aus einer Sammelbestellung entsteht **genau eine Anfrage** beim Hersteller. Gleiche Artikel werden summiert. Der Preis kommt aus den Einkaufskonditionen. Die Anfrage bleibt Entwurf; **bestellt wird manuell** im Einkauf. | – | Auftrag |
| R13 | Nach Erstellung der Anfrage sind die Kundenmengen gesperrt. Storno ist nur möglich, solange die Bestellung beim Hersteller nicht bestätigt ist (die Anfrage wird mit storniert). | – | Annahme |
| R14 | Die Sammelbestellung ist **erledigt**, wenn der Wareneingang der Herstellerbestellung vollständig gebucht ist. | – | Annahme |
| R15 | Eine Charge **sperren darf jeder** Benutzer (Grund Pflicht). **Entsperren nur die Rolle Freigabe.** Gesperrte Chargen werden **nie versendet** (alle Lieferungen). | – | QM-Handbuch (sperren jeder, freigeben VP) |
| R16 | Einstellungen und Prozessregeln ändert nur die Rolle **Administrator**. | – | Auftrag |
| R17 | Benachrichtigungen sind **Aufgaben in Odoo**, keine E-Mails. | ja (je Ereignis) | Arbeitsregel „E-Mail nur mit Freigabe“ |

## B. Offene Fragen für Etappe 2

1. **Route „Auf Auftrag beschaffen“:** Ist sie bei einem Artikel aktiv, legt Odoo bei der Auftragsbestätigung automatisch eine Bestellung an. Das würde die Sammelbestellung doppeln. Soll das Modul die Route für Blistercenter-Artikel verbieten oder nur warnen?
2. **Zuordnung der Ware:** Nach dem Wareneingang reserviert Odoo nach FEFO für alle offenen Lieferungen. Soll die Ware stattdessen fest den Kunden der jeweiligen Sammelbestellung zugeordnet werden?
3. **Wareneingang:** Gilt wie bei GreenRX Quarantäne + Freigabe durch die VP? Restlaufzeit beim Eingang mind. 12 Monate (QM-Handbuch) – sperren oder nur warnen?
4. **Versandfreigabe:** Eigener Schritt mit Vier-Augen-Endkontrolle (QM-Handbuch)? Wer darf freigeben?
5. **Teillieferungen:** Immer erlaubt, nur nach Rücksprache mit dem Kunden oder nie?
6. **Zahlungssperre:** Nur manuell oder automatisch bei überfälliger Rechnung (ab wie vielen Tagen)? Kreditlimit für Kunden mit Zahlungsmodell „Rechnung“?
7. **Shop:** Selbstregistrierung aus, Preise erst nach Anmeldung? Ist die GDP-Kundenqualifizierung Voraussetzung für die Freischaltung?
8. **Verfall:** Verfallskontrolle < 6 Monate → automatisch sperren oder nur Liste?
9. **Vorkasse ohne Anzahlung:** Reicht die normale Rechnung vor Lieferung (so prüft das Modul heute), oder muss es eine Anzahlungsrechnung sein?
