# Blistercenter – Regelkatalog (Stand 21.10.2026, Modul 20.0.1.5.0)

Blistercenter sind die Kunden des PLCS-Großhandels. Sie bestellen bei PLCS im Shop, per Telefon, E-Mail oder Fax. PLCS bündelt die Mengen in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

**Status:** Etappe 1 (Gerüst) und Etappe 2 (Geschäftsregeln) sind umgesetzt. Entscheidungen von Mehrdad am 15.10.2026 und 19.10.2026 (Gespräch mit Herrn Krebs) sind in Teil B eingetragen. **Seit 1.2.0 entfällt die Vorkasse.** Alle Regeln mit Schalter lassen sich unter Blistercenter › Einstellungen ändern (Rolle Administrator).

## Ablauf

1. **Kunde anlegen** (von Hand): Kennzeichen „Blistercenter“, Zahlungsart (Lastschrift über DATEV), **Zahlungsziel 7 oder 14 Tage**, **Rabatt 0, 1 oder 2 % auf den HAP**. Portalzugang über den Knopf „Portalzugänge“ am Kunden (Standard-Assistent von Odoo; er verschickt die Einladung, wenn man sie dort auslöst).
2. **Bestellung**: im Shop (nur freigeschaltete Blistercenter) oder als **Telefonbestellung** (Menü Kundenbestellungen › Telefonbestellung erfassen, oder Knopf am Kunden).
3. **Proforma-Rechnung** (ab 1.5.0): Bei der Bestätigung schickt Odoo die Proforma als PDF per E-Mail an das Blistercenter. **Zahlbar bis = Bestätigung + 7/14 Tage.** Der Versand wartet nicht auf die Zahlung. Keine Buchung.
4. **Zu bestellen / Sammelbestellung**: PLCS sieht offene Kundenmengen je Hersteller und entscheidet selbst, wann bestellt wird: bündeln → Anfrage beim Hersteller → manuell bestellen. Odoo bestellt nie automatisch.
5. **Wareneingang** in die **Blistercenter-Quarantäne** (Vorgangstyp BCIN).
6. **Freigabe durch die VP** (Vorgangstyp BCFRG) in den Bestand. Kurzläufer brauchen eine zusätzliche Freigabe.
7. **Ware fest je Kunde**: Nach der Freigabe reservieren die Lieferungen genau die Ware aus ihrer Sammelbestellung.
8. **Kommissionierung** (Person 1) → **Endkontrolle** und Bestätigung (Person 2) → Lieferung (komplett oder, nach Rücksprache, als Teillieferung).
9. **Rechnung je Lieferung**: Nach jeder Lieferung legt Odoo die Rechnung über die gelieferte Menge als Entwurf an (19 % USt, Export 0 %; Fälligkeit wie Proforma). Die Buchhaltung bucht und versendet sie.
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
| R29 | **Proforma-Rechnung** (ab 1.5.0 siehe R44): Knopf „Proforma drucken“ bzw. „Proforma senden“ am bestätigten Auftrag. Aufdruck „Proforma – keine Rechnung“ mit „Zahlbar bis“. Keine Buchung. Standard-Proforma von Odoo, für die Rolle Blistercenter freigegeben. | – |
| R30 | **Zu bestellen:** Kein automatischer Einkauf. Liste und Kachel „Zu bestellen“ zeigen offene Kundenmengen **je Hersteller** (aus der Einkaufskondition). PLCS bündelt und bestellt manuell. | – |
| R31 | **Zahlungsziel Pflicht:** Ohne Zahlungsbedingung am Kunden lässt sich die Bestellung nicht bestätigen. Das Modul legt „7 Tage netto“ und „14 Tage netto“ an; der Auftrag übernimmt das Ziel des Kunden. | ja |
| R32 | **Rechnung je Lieferung:** Nach jeder Lieferung legt Odoo die Rechnung über die **gelieferte Menge** als Entwurf an (mit Verweis auf die Lieferung) und gibt der Buchhaltung eine Aufgabe. Kein Buchen, keine E-Mail. Artikel müssen „Fakturierung: gelieferte Mengen“ haben, sonst ist die Bestätigung gesperrt. | ja |
| R33 | **Lastschrift über DATEV:** Zahlungsart „Lastschrift (DATEV)“ am Kunden. Rechnung (und Proforma) tragen den Hinweis auf den SEPA-Lastschrifteinzug. Mandat nur in DATEV, keine Mandatsreferenz in Odoo. | – |
| R34 | **Rabatt je Kunde:** Verkaufspreis = **HAP** (Verkaufspreis am Artikel). Je Kunde **0, 1 oder 2 %** auf alle Artikel, andere Werte nicht möglich. Ein abweichender Rabatt in einer Position sperrt die Bestätigung. Der Preis zum Zeitpunkt des Auftrags gilt. | ja |
| R35 | **Preise von Hand:** keine Lauer-Taxe. HAP am Artikel (Verkaufspreis), **AVP als Information am Artikel** (ab 1.5.0; im Bestellschein und in der Shop-Beschreibung). Einkaufspreis je Hersteller und Artikel als Einkaufskondition. | – |
| R36 | **Überfällige Rechnungen:** Auftrag, Lieferung und Kunde zeigen einen deutlichen Hinweis (Anzahl, Betrag). **Keine Sperre.** Kachel „Überfällige Rechnungen“. | – |
| R37 | **Nachweise nur aus dem Ablauf** (Prüfbericht 19.10.2026): Wer kommissioniert, gebucht, Kurzläufer freigegeben, eine Teillieferung abgestimmt oder „Aus Lagerbestand“ gewählt hat, setzt nur Odoo in der jeweiligen Aktion. Status der Sammelbestellung nur über ihre Übergänge. Direkte Schreibzugriffe (z. B. über die Schnittstelle) werden abgelehnt. | – |
| R38 | **Kennzeichen „Blistercenter“** entfernt nur die Rolle Freigabe. Ein bestätigter Auftrag bleibt Blistercenter-Auftrag, auch wenn das Kennzeichen später fehlt. | – |
| R39 | **Mengen:** Die Auftragsmenge kann nicht unter die Menge in Sammelbestellungen sinken; eine stornierte Sammelbestellung lässt sich nur reaktivieren, wenn die Menge noch frei ist. Die Herstellerbestellung muss die Kundenmengen decken (mehr ist erlaubt). | – |
| R40 | **Shop-Sortiment** (1.3.0; Einstellungen zeigen die Anzahl und warnen, wenn das Sortiment leer ist): Im Shop erscheinen und bestellbar sind nur Artikel mit „Im Blistercenter-Shop“ (Artikel › Reiter Verkauf/Shop). Alle anderen – auch Odoo-Demoprodukte – sind unsichtbar: keine Liste, keine Produktseite (404), kein Warenkorb, keine Bestätigung einer Shop-Bestellung mit Fremdartikel. Telefonbestellungen dürfen jeden Artikel enthalten. Shop-Kategorien je Medikament ordnen das Sortiment. | ja |
| R41 | **Arbeitsbereiche** (1.3.0, Startseite in Spalten je Bereich: Bestellannahme, Einkauf, Lager, Freigabe (VP)): Bestellannahme, Einkauf und Lager (Einstellungen › Benutzer, je eine Zeile „Blistercenter: …“). Jeder Bereich sieht nur sein Menü und seine Kacheln auf „Meine Aufgaben“. Freigabe und Administrator sehen alles. Die Bereiche ändern **keine Rechte** (die kommen aus „Benutzer“). Ohne Bereich nur die leere Startseite mit Hinweis. | – |
| R42 | **Bestellschein** (1.4.0): Telefon-, E-Mail- und Fax-Bestellungen über den Bestellschein: Kunde, Besteller, Liste aller Artikel des Shop-Sortiments mit Mengenfeld. Odoo nimmt nur Artikel des Bestellscheins, ganze Mengen 0–9999, jeden Artikel einmal, mindestens eine Menge. Der Auftrag entsteht als Entwurf; weitere Artikel kann die Bestellannahme danach im Auftrag ergänzen. | – |
| R43 | **Online-Bestellschein** (1.4.0): Freigeschaltete Blistercenter ohne Zahlungssperre bestellen im Kundenportal über denselben Bestellschein (gleiche Prüfungen, serverseitig). Bestellweg „Online-Bestellschein“, Besteller = angemeldete Person. Der Auftrag steht als „Angebot gesendet“ unter „Neue Bestellungen“; PLCS bestätigt ihn. Die Bestellannahme bekommt eine Aufgabe. | – |
| R44 | **Proforma statt Auftragsbestätigung** (1.5.0): Bei der Bestätigung geht die Proforma-Rechnung als PDF per E-Mail an den Kunden (ohne E-Mail-Adresse: Hinweis im Verlauf). **Zahlbar bis = Tag der Bestätigung + Zahlungsziel (7/14 Tage).** Der Versand wartet nicht auf die Zahlung. Die Rechnung je Lieferung (R32) bleibt und übernimmt dieses Fälligkeitsdatum. Im Portal zeigt der bestätigte Auftrag die Proforma. | ja |
| R45 | **Umsatzsteuer** (1.5.0): Inland 19 %, Ausfuhr in Drittländer 0 %, EU mit USt-IdNr. 0 % (innergemeinschaftlich), EU ohne USt-IdNr. 19 %. Automatisch über die Steuerzuordnung nach Land des Kunden (Odoo-Kontenrahmen SKR03/SKR04; Blistercenter schaltet „Automatisch erkennen“ für Nicht-EU und EU ohne USt-IdNr. ein). Auftrag mit Warenposition ohne Steuer zeigt einen Hinweis. | – |
| R46 | **Packungsfotos** (1.5.0): Foto je PZN aus einer einstellbaren Bildquelle (Standard: Bild-CDN einer Online-Apotheke). Knopf „Packungsfotos laden“ (nur Administrator) lädt fehlende Fotos des Shop-Sortiments, nur JPEG/PNG bis 5 MB. Dev-Builds laden sie automatisch. Bildrechte klärt PLCS. | – |
| R47 | **Gleiche Charge mehrfach** (21.10.2026): Kommt eine Charge erneut vom Hersteller, bucht Odoo auf die vorhandene Charge (eine Charge, Rückverfolgung und Rückruf über alle Eingänge und Kunden). Quarantäne und VP-Freigabe gelten je Eingang. **Abweichendes Verfallsdatum** bei bekannter Charge: nur Hinweis im Wareneingang, nach dem Buchen im Verlauf und als Aufgabe für die VP; Odoo behält das Datum der vorhandenen Charge. **Gesperrte Charge** im Eingang: wird in die Quarantäne gebucht (Hinweis, Verlauf, Aufgabe VP); Freigabe und Versand bleiben gesperrt. | – |
| R48 | **DataMatrix-Scan im Wareneingang** (21.10.2026): Knopf „Scannen (DataMatrix)“. Artikel über NTIN/PZN, Charge und Verfall aus dem Code (GS1 und IFA). Jeder Scan = eine Packung; Menge pro Charge änderbar. Nur Artikel dieses Wareneingangs. Gebucht wird danach wie gewohnt. | – |
| R49 | **Verfall laut Packung** (1.5.1, Prüfbericht 21.10.2026): Der Scan speichert das Verfallsdatum aus dem Code an der Lagerzeile („Verfall laut Packung“), auch wenn Odoo auf eine vorhandene Charge mit anderem Datum bucht. Hinweis im Eingang, im Verlauf und die VP-Aufgabe (R47) entstehen aus diesem Wert. Nur der Scan-Assistent setzt ihn. | – |
| R50 | **Eine Charge, ein Verfall pro Eingang** (1.5.1): Wird dieselbe Charge mit anderem Verfall gescannt, zählt Odoo die Packung nicht und meldet die Abweichung. Auch beim Übernehmen in einen Eingang, in dem die Charge schon mit anderem Datum steht, bricht Odoo mit Meldung ab. | – |
| R51 | **Rechnung nur über die Menge dieser Lieferung** (1.5.1, ergänzt R32): Die Rechnung enthält genau die Mengen der Lieferung, auch wenn zwei Lieferungen zusammen gebucht werden oder eine ältere Menge noch nicht abgerechnet ist. Je Lieferung höchstens eine Rechnung. Bleibt gelieferte Menge ohne Rechnung (z. B. gelöschter Entwurf), steht ein Hinweis im Auftrag. | ja |
| R52 | **Storno der Herstellerbestellung** (1.5.1): Wird die Bestellung beim Hersteller direkt im Einkauf storniert, storniert Odoo die Sammelbestellung mit; die Kundenmengen stehen wieder unter „Zu bestellen“. Ist schon Ware eingegangen, ist der Storno gesperrt (nur den Rückstand stornieren). | – |
| R53 | **Preis = HAP** (1.5.1, Teil von „Kundenrabatt auf den HAP fest“): Bei der Bestätigung muss der Preis jeder Warenposition dem HAP des Artikels entsprechen und der Auftrag in Firmenwährung laufen. Eine abweichende Preisliste verhindert die Bestätigung. So stimmen Bestellschein und Auftrag überein. | ja |
| R54 | **Bestellschein nur einmal absenden** (1.5.1): Jeder angezeigte Bestellschein hat eine Einmal-Kennung. Doppelklick, Neuladen oder Wiederholung nach Verbindungsabbruch ergibt keine zweite Bestellung. Der Bestellschein zeigt vor dem Absenden Packungen und Summe netto. | – |
| R55 | **Packungen von Hand erfassen** (1.6.0): Im Fenster „Packungen erfassen (Scan oder Hand)“ trägt das Lager Artikel, Charge, Verfall (MM/JJJJ oder TT.MM.JJJJ) und Anzahl ein und klickt „Hinzufügen“. Nur Artikel dieses Eingangs, Charge ohne Leerzeichen (max. 20 Zeichen), Anzahl ≥ 1. Gleiche Charge mit anderem Verfall wird abgelehnt (wie R50). | – |
| R56 | **Handscanner-Trennzeichen** (1.6.0): Das Scan-Feld setzt Strg+] und Alt+029 (Trennzeichen GS des Handscanners) als GS ein; Enter oder Tab schickt die Packung ab. Schnelle Scans werden der Reihe nach verarbeitet. Scanner, die GS als „#“ ausgeben, gehen ebenfalls. | – |
| R57 | **Code ohne Trennzeichen** (1.6.0): Ergeben alle möglichen Lesarten denselben Artikel, dieselbe Charge und denselben Verfall, zählt die Packung (gelber Hinweis). Sonst zählt Odoo nichts, belegt Artikel und Verfall in der Handeingabe vor, und das Lager schreibt die Charge von der Packung ab. | – |
| R58 | **Übersicht im Wareneingang** (1.6.0): Je Artikel bestellt, schon im Eingang, in dieser Liste und Status (fehlt / vollständig / mehr als bestellt). | – |
| R59 | **Handscanner robust** (1.6.1): Das Scan-Feld nimmt auch Alt+29 (Windows-Zeichen „↔“), Strg+AltGr+9, die Kennung „]d2“/„+d2“ und das FNC1 am Anfang als Trennzeichen bzw. entfernt sie. Scanner ohne Enter am Ende: Odoo schickt nach kurzer Ruhe selbst ab. Steht der Cursor nicht im Scan-Feld oder in „Charge“/„Verfall“, wird der Scan trotzdem gezählt. Bei Fehlern zeigt die Meldung, was der Scanner geliefert hat („Gelesen: …“, Trennzeichen als ‹GS›), und weist auf eine falsche Tastatursprache hin. | – |
| R60 | **Kamera als Scanner** (1.6.2): Knopf „Kamera“ neben dem Scan-Feld öffnet Odoos Kamera. Jede gelesene Packung wird gezählt, die Kamera öffnet sich für die nächste wieder, bis sie geschlossen wird oder eine rote Meldung kommt. Auf Handy/Tablet springt die Tastatur nicht automatisch auf. | – |
| R61 | **Zuordnung Herstellerbestellung** (1.6.3, GPT-Prüfbericht 23.10.2026): Eine Position der Herstellerbestellung mit Kundenmengen lässt sich nicht löschen, ersetzen oder auf einen anderen Artikel ändern (Menge ändern geht). Beim Bestätigen muss jede Kundenmenge mit einer Position verbunden und je Position gedeckt sein. Sonst: Sammelbestellung zurück in den Entwurf und neu anfragen. | – |
| R62 | **Verfallene Ware** (1.6.3): Chargen mit erreichtem Verfallsdatum werden nie freigegeben und nie versendet, auch nicht mit Kurzläufer-Freigabe. Der Eingang in die Quarantäne bleibt möglich (Dokumentation). Fest, nicht abschaltbar. | – |
| R63 | **Zuordnung Blistercenter** (1.6.3): Lieferung, Bündelung und Hinweis auf offene Posten folgen der bei Bestätigung festgehaltenen Zuordnung des Auftrags, nicht dem aktuellen Kennzeichen am Kontakt. Lieferung ohne Auftrag: Kennzeichen des Kontakts. | – |
| R64 | **HAP Pflicht** (1.6.3, Teil von R53): Artikel mit Verkaufspreis 0 € lassen sich in Blistercenter-Bestellungen nicht bestätigen. | ja |
| R65 | **Kurzläufer: Menü = Kachel** (1.6.3): Menü „Kurzläufer im Bestand“ zeigt genau die Liste der Kachel (Grenze aus den Einstellungen). | – |
| R66 | **Dieselbe Packung zählt einmal** (1.6.4, Code-Review 22.10.2026): Im Fenster „Packungen erfassen“ zählt ein Code mit derselben Seriennummer (gleicher Artikel) nur einmal – z. B. wenn die Kamera die Packung nach dem Treffer noch einmal liest. Gelbe Meldung „schon gezählt“, die Kamera läuft weiter. Andere Seriennummern derselben Charge zählen normal. Menge von Hand ändern und Handeingabe bleiben wie bisher. | – |
| R67 | **Kein Scan geht verloren** (1.6.4): „Übernehmen“ und „Hinzufügen“ warten, bis alle angenommenen Scans verarbeitet sind (auch ein Scan ohne Enter). Scheitert die Verarbeitung (Verbindung/Server), bleibt der Scan erhalten, die Meldung wird rot, die Kamera hält an; „Übernehmen“/„Hinzufügen“ lehnen ab, bis „Wiederholen“ (zählt nichts doppelt) oder „Verwerfen“ gewählt ist. Bekanntes Tagesdatum wird beim Vorbelegen der Handeingabe nicht auf das Monatsende verlängert. Alt+029 kommt auch ohne Pause vor den Folgezeichen an. | – |

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

### Entscheidungen 20.10.2026 (Mehrdad)

- Oberfläche so einfach wie möglich: Startseite „Meine Aufgaben“ mit großen Knöpfen und kurzes Menü je Arbeitsbereich (Bestellannahme, Einkauf, Lager).
- Shop nur mit den 5 Medikamenten Forxiga, Xigduo, Daxas, Brilique, Tagrisso (13 Artikel), nach Medikament gruppiert; keine Demoprodukte. Übrige Artikel bleiben für Telefonbestellungen, nur nicht im Shop.

### Entscheidungen 20.10.2026, abends (Mehrdad)

- Bestellschein mit den 13 Shop-Artikeln, gleich für alle Kunden, für Telefon und online.
- Online über den Bestellschein im Kundenportal (nicht über den Shop-Warenkorb).
- PLCS-Briefpapier nach dem Vorbild der PLCS-Gutschrift für alle Odoo-Belege der Firma (Modul `plcs_layout`).
- Satz zur Großhandelserlaubnis (§52a AMG): Feld am Unternehmen, Wortlaut liefert PLCS.

### Entscheidungen 21.10.2026 (Mehrdad)

- Nur Arzneimittel als Waren: Odoo-Demoprodukte ohne PLCS-Bezug (Möbel usw.) werden in Dev-Builds archiviert.
- Tagrisso (40 mg und 80 mg) nicht mehr im Blistercenter-Shop und -Bestellschein; Artikel bleiben bestehen. Sortiment: Forxiga, Xigduo, Daxas, Brilique (11 Artikel).
- Preise: Verkaufspreis bleibt der HAP (Rabatt 0/1/2 % darauf), gerechnet aus dem aktuellen AVP nach AMPreisV (Stand ABDA Mai 2026); AVP zusätzlich anzeigen.
- Chargen: gleiche Charge vom Hersteller ist der Normalfall; abweichendes Verfallsdatum nur warnen (R47); alle Packungen haben DataMatrix → Scan im Wareneingang (R48). Gesperrte Charge im Eingang: Entscheidung offen, heute Buchung in die Quarantäne mit Hinweis.
- Umsatzsteuer 19 %, Export 0 % (R45).
- Bei Bestellung keine Auftragsbestätigung, sondern Proforma-Rechnung; Zahlung innerhalb 7 bzw. 14 Tagen ab Proforma; Versand wartet nicht; Rechnung je Lieferung bleibt (R44).
- Packungsfotos aus dem Internet per PZN (R46); Mehrdad: Quelle Online-Shops, Bildrechte bei PLCS.
- PLCS sitzt in Deutschland: Währung **Euro** und Sprache **Deutsch** als Standard. Production war bereits so eingestellt (EUR, Deutschland, alle Benutzer Deutsch, Zeitzone Europe/Berlin; geprüft 21.10.). Dev-Builds stellen Firma (Land, Währung), Benutzer, Testkunden und Website beim Laden der Demodaten auf Deutschland/EUR/Deutsch um. Der Kontenrahmen im Dev-Build bleibt der Odoo-Demokontenrahmen (siehe C.7).

## C. Noch offen

1. ~~Zahlungssperre automatisch?~~ Entschieden 19.10.: bei überfälligen Rechnungen nur Hinweis (R36). Die manuelle Zahlungssperre bleibt.
2. **Shop:** Selbstregistrierung aus, Preise erst nach Anmeldung (Website-Einstellung)? GDP-Kundenqualifizierung als Voraussetzung für die Freischaltung?
3. **Verfall < 6 Monate im Bestand:** Heute Liste „Kurzläufer im Bestand“ und Kachel (Grenze aus R25). Automatisch sperren?
4. **Online-Zahlung im Shop:** nicht vorgesehen (Lastschrift über DATEV). Im Shop keinen Zahlungsanbieter aktivieren.
5. **Zu wenig geliefert:** Liefert der Hersteller weniger als bestellt, reicht die Ware nicht für alle Kunden der Sammelbestellung. Die Reihenfolge legt heute Odoo fest. Soll PLCS die Verteilung selbst bestimmen?
6. Aufbewahrung und Validierung (Change Control) mit QMB abstimmen.
7. **DATEV-Export und Kontenrahmen:** Der Export ist Teil von Odoo Enterprise (Buchhaltungsberichte). Production (Firma in Deutschland, EUR): Mit dem Merge installiert Odoo die Buchhaltung und dabei automatisch die deutsche Lokalisierung `l10n_de` mit **SKR03** (EUR, USt 19 %/7 %); lokal nachgestellt am 21.10. Dev-Builds nutzen den Odoo-Demokontenrahmen, auf Euro umgestellt und mit deutschen Sätzen ergänzt (19 % USt, 0 % Ausfuhr, 0 % EU mit USt-IdNr., automatische Zuordnung wie im SKR03; `_bc_demo_taxes`). **Vor dem Merge mit dem Steuerberater klären: SKR03 oder SKR04?** Ein Wechsel nach den ersten Buchungen ist aufwendig.
8. **HAP:** Testwerte aus dem AVP gerechnet (Shop Apotheke, 21.10.2026; Formel AMPreisV, Abweichung ±1 Cent). Bydureon, Byetta und Bambec ohne AVP (außer Handel) – Preis fiktiv. Für den Echtbetrieb echte HAP von PLCS übernehmen.
