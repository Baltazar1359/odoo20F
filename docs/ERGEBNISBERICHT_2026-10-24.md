# Ergebnisbericht: VP-Auto-Freigabe, geschützter Shop, Geschäftsführungs-Dashboard

Stand 24.10.2026 · Branch `feat/vp-autofreigabe-shop-dashboard` (Basis `main` d2b8aaf) · Module **blistercenter 20.0.1.3.0**, **plcs_dashboard 20.0.1.0.0** (neu) · GreenRX unverändert.

## 1. Analyse (Phase 1)

| Thema | Vorgefunden | Betroffene Stellen |
|---|---|---|
| VP-Freigabe Blistercenter | Jeder Eingang aus einer Sammelbestellung (BCIN) landet in der Quarantäne; `_bc_create_release` legt einen Freigabevorgang BCFRG an, den nur die Rolle Freigabe bucht (Vier-Augen gegen den Wareneingang, Kurzläufer-Zusatzfreigabe, Chargensperre). Es gab **keine erfasste Wareneingangsprüfung** und **keine Lieferantenqualifizierung** in Odoo. | `blistercenter/models/stock.py`: `StockPicking._bc_create_release`, `_bc_check_release`, `_bc_check_lots`, `_action_done`, `StockMoveLine._bc_check_quarantine_exit`; `blistercenter_config.py`; `res_partner.py` |
| Abgrenzung GreenRX/Retouren | GreenRX (Retouren für ATIV) ist ein eigenes Modul mit eigenen Modellen (`plcs.greenrx.box`, eigene VP-Entscheidung). Blistercenter-Eingänge sind eindeutig über Vorgangstyp BCIN + Sammelbestellung erkennbar. | keine Änderung an `plcs_greenrx` |
| Shop | Warenkorb (`_cart_add`) und Bestätigung waren für nicht freigeschaltete Kunden gesperrt; **Katalog, Preise, Artikelseiten, Menü und JSON-Schnittstellen waren für jeden sichtbar**. Freigabe nur auf Kundenebene, nicht je Benutzer. | `sale_order.py` (`_bc_check_shop_access`, `_confirmation_error_message`), `res_partner.py` |
| Dashboard | Vorhanden: Kachel-Übersicht `blistercenter.overview` (Arbeitslisten). Kein Geschäftsführungs-Dashboard, keine Umsatz-/Zeitraumauswertung. | neu: Modul `plcs_dashboard` |
| Sicherheitsmuster | Nachweisfelder sind über `tools/bc_auth.py` geschützt (interne Freigabe als Python-Objekt, per RPC nicht fälschbar). Wird für alle neuen Nachweisfelder genutzt. | `tools/bc_auth.py` (Freigaben jetzt verschachtelbar) |
| Odoo 20 | Rechte als `ir.access` (ACL + Regeln zusammengeführt), `website.has_ecommerce_access()` als zentraler Shop-Hook, OWL 3. | – |

**Befund außerhalb des Auftrags (nicht geändert):** Rücksendungen von Blistercenter-Kunden (Rücklieferung zu einer Lieferung) laufen über den Standard-Eingangstyp direkt in den Bestand, nicht in eine Quarantäne mit VP-Entscheidung. Siehe Abschnitt 5.

## 2. Umsetzung (Phase 2)

### 2.1 VP-Freigabe für reguläre Blistercenter-Lieferungen (Regel BC-AUTO-1)

- **Wareneingangsprüfung** am Eingang (Assistent, vier Pflichtfragen: Verpackung/Zustand, Temperatur, Abgleich Bestellung/Lieferschein inkl. Charge/Verfall, Qualität/Rückruf/Fälschungsverdacht). Abweichung nur mit Beschreibung. Wer/wann wird gespeichert; eine spätere Änderung von Menge, Charge oder Artikel setzt die Prüfung zurück.
- **Lieferantenqualifizierung** am Hersteller („GDP-qualifiziert (Direktlieferant)“, gültig bis, Nachweis) – nur Rolle Freigabe, mit Nachweis wer/wann.
- **Regelbasierte Entscheidung** nach der Buchung des Eingangs (`_bc_decide_release`): Automatische Freigabe nur, wenn *alle* elf Kriterien aus tatsächlich gespeicherten Daten erfüllt sind (Verfahren freigegeben, regulärer Eingang ohne Retoure, Hersteller qualifiziert und Qualifizierung gültig, Absender = Hersteller der Bestellung, nur bestellte Artikel ohne Mehrmenge, Prüfung ohne Abweichung, Charge und Verfall vorhanden, keine gesperrte/zurückgerufene Charge, keine abgelaufene Charge, kein Kurzläufer, Freigabe = eingegangene Ware). Fehlende oder unklare Werte → Kriterium nicht erfüllt → VP.
- **Ausnahmefall:** Ware bleibt in der Quarantäne, die VP bekommt eine Aufgabe (bei Abweichung „Abweichung prüfen und entscheiden“) und gibt wie bisher frei; Vier-Augen-Prinzip, Kurzläufer-Freigabe und Chargensperre bleiben unverändert.
- **Nachweis** an jeder Freigabe: Freigabeweg (automatisch/VP), Regel mit SOP, freigebender VP und Datum, Prüfergebnis je Kriterium (✓/✗), Wareneingangsprüfung (wer/wann), Zeitpunkt. Automatische Freigaben bucht OdooBot. Neue Liste „Freigaben (Protokoll)“.
- **Verfahrensfreigabe:** Einschalten nur durch die in den Einstellungen hinterlegte zuständige VP mit SOP-Angabe und Bestätigung („Automatische Freigabe (Verfahren)“). Standard **aus**. Wechsel der VP schaltet die Automatik aus. Ausschalten: VP oder App-Administrator.
- **Schutz gegen Umgehung:** alle neuen Nachweis- und Einstellungsfelder sind gegen direkte Schreibzugriffe (RPC/API/Automatisierung) geschützt; die Buchung aus der Quarantäne erlaubt die Automatik nur mit der internen Freigabe `auto_release`, die kein RPC-Aufruf erzeugen kann (ein gleichnamiger Kontextwert wirkt nicht); interne Methoden sind privat (`_…`) und per RPC nicht aufrufbar; die automatische Buchung läuft ohne Superuser mit den Rechten der Person am Wareneingang und in einem Savepoint – scheitert sie, bleibt die Ware bei der VP.

### 2.2 Geschützter Blistercenter-Shop

- **Freigabe je Benutzer** zusätzlich zur Kundenfreischaltung: Kontakt-Feld „Shop-Zugang (Benutzer)“ (nur Rolle Freigabe, Nachweis wer/wann). Zugang = angemeldet, aktiv, Firma ist Blistercenter **und** für den Shop freigeschaltet **und** Kontakt hat Shop-Zugang. Interne PLCS-Benutzer sehen den Shop zur Pflege.
- **Serverseitige Prüfung** jeder Shop-Route (`ir.http._pre_dispatch`, vor dem Standard): `/shop…`, `/website_sale…`, `/my/orders/reorder`, `/sale/create_product_variant` – HTTP und JSON. Anonym → Anmeldung, angemeldet ohne Freigabe → 403.
- **Odoo-Standardhook** `website.has_ecommerce_access()` erweitert: blendet Shop-Menü, Warenkorb-Symbol, Produkt-Snippets, Shop-Suche und Artikelseiten aus; Sitemap listet keine Shop-Adressen.
- **Schnittstellen:** Zugriffsregel (`ir.access`, Lesen) auf `product.template`/`product.product`: Portal- und Website-Besucher ohne Freigabe lesen keine Artikel und Preise, auch nicht per JSON-RPC (`search_read`) oder Bild-URL. Entzug wirkt sofort (Cache wird geleert).
- **Warenkorb/Bestellung:** `_cart_add` prüft zusätzlich die Benutzerfreigabe und dass der Benutzer zum Kunden des Warenkorbs gehört; Zahlungssperre sperrt weiterhin das Bestellen.
- **Registrierung** erzeugt nie einen Shop-Zugang (Feld nur durch Rolle Freigabe).
- **Website-Schalter** „Blistercenter-Shop (nur freigegebene Benutzer)“ (Standard an; zusammen mit der bestehenden Firmenregel R3). Regeln gelten je Firma.

### 2.3 Geschäftsführungs-Dashboard (`plcs_dashboard`)

- Menü **Geschäftsführung**: 25 KPI-Karten in vier Bereichen, 5 Diagramme, Zeitfilter (heute/7/30/90 Tage/frei), Vergleich zum Vorzeitraum mit Farbe nach Wirkung, Klick öffnet die Datensätze.
- Nur aktive Firma; ohne `sudo()`; Finanzkennzahlen (Umsatz, Auftragswerte, Bestandswert) nur für Rolle „Finanzen“. Nicht berechenbare Kennzahlen (Umsatz GreenRX, Reklamationen, Meldebestand ohne Nachbestellregeln) erscheinen als „nicht verfügbar“ mit Begründung.
- GreenRX-Kennzahlen (VP-Freigaben der Boxen, gesperrte Boxen, gelb/rote Boxen, offene Retourenvorgänge), wenn `plcs_greenrx` installiert ist – ohne harte Abhängigkeit.
- KPI-Übersicht mit Datenquelle, Formel, Verfügbarkeit, fehlenden Feldern: [DASHBOARD_KPI.md](DASHBOARD_KPI.md).

## 3. Wichtige Entscheidungen

1. **Automatik standardmäßig aus, Einschalten nur durch die zuständige VP** mit SOP – die technische Regel ersetzt nicht die Verfahrensfreigabe; die übergeordnete VP-Verantwortung bleibt.
2. **Fail-safe statt Annahmen:** Jedes Kriterium braucht einen positiv gespeicherten Wert. Die Wareneingangsprüfung ist keine Pflicht für die Buchung (bestehende Abläufe brechen nicht), aber ohne sie gibt es keine Automatik.
3. **Kurzläufer bleiben VP-Sache**, Retouren und Sonderfälle ebenso; GreenRX unberührt.
4. **Benutzerfreigabe zusätzlich zur Kundenfreischaltung** (Anforderung „Kunden bzw. berechtigte Benutzer“); bewusst fail-closed: bestehende Portalbenutzer brauchen nach dem Update den Shop-Zugang.
5. **Shop-Sperre dreifach:** Routenprüfung (alle Shop-Endpunkte), Odoo-Hook (Oberfläche), Zugriffsregel (Schnittstellen) – Ausblenden allein reicht nicht.
6. **Dashboard als eigenes Modul**, weil es Blistercenter und GreenRX zusammenführt und eigene Rollen braucht; keine neuen Datenfelder für das Dashboard selbst.
7. Neue Felder nur, wo Daten fehlten (Wareneingangsprüfung, Freigabeentscheidung, Lieferantenqualifizierung, Shop-Zugang, Verfahrensfreigabe, Website-Schalter).

## 4. Tests (Phase 3)

Lokal mit Odoo 20.0 (Community-Quellen, Stand 24.10.2026), PostgreSQL 16, Windows. Ergebnis siehe Abschnitt 4.1.

| Bereich | Tests | Fälle |
|---|---|---|
| VP-Freigabe (`test_auto_release.py`) | 19 | reguläre Herstellerlieferung (automatisch, Nachweis, Versand mit Endkontrolle); Automatik aus; beschädigte Ware (inkl. Pflichtbeschreibung, VP-Freigabe bleibt); Temperaturabweichung; nicht qualifizierter Hersteller; abgelaufene Qualifizierung; fehlende Prüfung; fehlendes Verfallsdatum; Kurzläufer; gesperrte Charge; Mehrmenge; Absender ≠ Hersteller; Retoure; Rücksetzung der Prüfung; Prüfung nur vor Buchung; Manipulationsversuche (Felder direkt, Kontextwert, private Methoden per RPC); Verfahrensfreigabe nur zuständige VP; Lieferantenqualifizierung nur Rolle Freigabe |
| Shop (`test_shop_access.py`) | 11 | Zugriffsmatrix (anonym, nicht freigegeben, fremd, freigegeben, intern, Entzug Kunde/Benutzer, gesperrter Zugang); Website ohne Schutz; Katalog/Preise per API/JSON-RPC; Registrierung; Warenkorb serverseitig; Mandantentrennung; Menü; HTTP: anonym → Anmeldung (Shop, Artikel, Warenkorb, Kasse, JSON), Startseite/Sitemap, nicht freigegeben → 403, freigegeben → Shop und Warenkorb, Entzug wirkt sofort |
| Dashboard (`test_dashboard.py`) | 9 | Umsatz/Gutschrift/Vergleich/Segmente/Trend; Drill-down = Kennzahl; Lager/MHD/Sperren/Quarantäne/VP; fehlende Daten; Zeitfilter und Validierung; Rechte (ohne Rolle, ohne Finanzen, ohne Lager-Admin, ohne Blistercenter-Rolle); Mandantentrennung; Abfragen wachsen nicht mit der Datenmenge; Browser-Tour (OWL-Oberfläche, Filter, Diagramm, Klick) |
| Bestehende Tests | 58 + GreenRX | unverändert grün; ein Test an das neue Verhalten angepasst (Portalzugang allein gibt keinen Shop-Zugang) |

### 4.1 Ergebnis

| Lauf | Module | Ergebnis |
|---|---|---|
| Gesamtlauf neue Datenbank, alle drei Module installiert | blistercenter, plcs_dashboard, plcs_greenrx | **245 Tests, 0 fehlgeschlagen** |
| Abschlusslauf nach letzten Änderungen | blistercenter, plcs_dashboard | **96 Tests, 0 fehlgeschlagen** |
| Installation mit Odoo-Demodaten (inkl. Demo-Shopbestellungen) | blistercenter, plcs_dashboard | fehlerfrei |
| Sichtprüfung im Browser (Demodaten) | plcs_dashboard | alle vier Bereiche, Diagramme, „nicht verfügbar“-Karten, Zeitfilter, Drill-down (32 offene Aufträge = 32 Datensätze) |

Alle als „Error“ gezählten Meldungen betreffen `tearDownClass` (Aufräumen des Dateispeichers unter Windows, siehe Punkt 11); sie treten genauso bei Odoos eigener `web`-Testsuite auf. Nicht getestet: Odoo Enterprise / Odoo.sh-Build (Punkt 7).

## 5. Offene Punkte und Risiken

| # | Kritikalität | Punkt | Empfehlung |
|---|---|---|---|
| 1 | **Hoch** (organisatorisch) | Automatische Freigabe darf erst nach SOP, Change Control/Validierung des computergestützten Systems (GDP 3.3) und VP-Freigabe eingeschaltet werden. | SOP „Wareneingang Blistercenter“ mit Regel BC-AUTO-1 erstellen; Tests dieses Berichts als Teil der Validierung nutzen; VP schaltet ein. |
| 2 | **Hoch** (vorbestehend, nicht Teil dieser Änderung) | Kundenrücksendungen (Rücklieferung zu einer Blistercenter-Lieferung) gehen über den Standard-Eingangstyp direkt in den Bestand, ohne Quarantäne und VP-Entscheidung (GDP 6.3 / Aufgaben der VP). | Rückgabetyp der Lieferungen auf eine Retouren-Quarantäne mit VP-Freigabe umstellen (eigener Auftrag). |
| 3 | **Hoch** (Aktivierung) | Bestehende Portalbenutzer verlieren nach dem Update den Shop-Zugang, bis die Rolle Freigabe „Shop-Zugang (Benutzer)“ am Kontakt setzt (gewollt). | Vor dem Livegang Liste der Shop-Kontakte freigeben. |
| 4 | Mittel | Automatische Freigabe beruht auf der Prüfung einer Person; das Vier-Augen-Prinzip (R24) gilt weiter nur für VP-Freigaben. | VP entscheidet, ob eine zweite Person die Wareneingangsprüfung bestätigen soll (Erweiterung möglich). |
| 5 | Mittel | Selbstregistrierung im Portal gibt keinen Shop-Zugang, legt aber Konten an. | Website-Einstellung „Kundenkonten: auf Einladung“. |
| 6 | Mittel | Die Artikel-Zugriffsregel wirkt je Firma: ein zusätzlicher öffentlicher Shop in derselben Firma zeigte Besuchern keine Artikel. Heute nicht relevant (die bisherige Shop-Regel galt schon für alle Shops der Firma). | Bei Bedarf Regel je Website verfeinern. |
| 7 | Mittel | Getestet mit Odoo 20 Community-Quellen; Odoo.sh nutzt Enterprise (anderes Web-Client-Theme). | Tests und Tour im Odoo.sh-Dev/Staging-Build wiederholen. |
| 8 | Niedrig | Systemadministratoren (Superuser, z. B. Serveraktionen mit `sudo()`) können Nachweisfelder weiterhin setzen – bewusst wie bisher. | Administratorrechte auf IT beschränken. |
| 9 | Niedrig | Google-Merchant-Feed (`/gmc.xml`) würde bei Aktivierung den Katalog mit Token veröffentlichen. | Funktion nicht aktivieren. |
| 10 | Niedrig | Umsatz GreenRX und Reklamationen nicht berechenbar (keine Daten in Odoo). | GreenRX-Abrechnung bzw. Reklamations-/CAPA-Prozess in Odoo, dann Kennzahl ergänzen. |
| 11 | Niedrig | Lokale Testumgebung (Windows): `tearDownClass` meldet Fehler beim Aufräumen des Dateispeichers (`__gc`-Dateien gesperrt). Nicht modulbedingt, unter Linux/Odoo.sh nicht zu erwarten. | – |

## 6. GDP- und Compliance-Einschätzung (fachlich, keine Rechtsberatung – Bewertung durch VP/QMB erforderlich)

- **EU-GDP-Leitlinien (2013/C 343/01) Kap. 5.5 Wareneingang:** verlangt die Prüfung, dass die Sendung korrekt ist, von zugelassenen Lieferanten stammt und keine sichtbaren Transportschäden hat, und dass Ware erst nach Sicherstellung **gemäß schriftlichen Verfahren** in den verkaufsfähigen Bestand gelangt. Eine Einzelfreigabe jeder Lieferung durch die VP wird dort nicht verlangt. Die Kriterien von BC-AUTO-1 bilden diese Prüfpunkte ab; das schriftliche Verfahren (SOP) ist Voraussetzung für das Einschalten.
- **Kap. 2.2 Aufgaben der VP:** u. a. Verfahren genehmigen, Lieferanten-/Kundenqualifizierung sicherstellen, über zurückgegebene, zurückgewiesene, zurückgerufene oder gefälschte Ware entscheiden. → Verfahrensfreigabe nur durch die VP (R44), Lieferantenqualifizierung nur Rolle Freigabe (R45), Retouren/Abweichungen bleiben bei der VP (R42).
- **Kap. 5.2 Qualifizierung von Lieferanten:** in Odoo jetzt als Voraussetzung mit Gültigkeitsdatum abgebildet.
- **Delegierte Verordnung (EU) 2016/161 (Fälschungsschutz):** Großhändler müssen Sicherheitsmerkmale u. a. bei Ware prüfen, die nicht direkt vom Hersteller/Zulassungsinhaber kommt, sowie bei Rücksendungen. Das Kriterium „direkt vom Hersteller der Bestellung, keine Retoure“ beschränkt die Automatik auf Fälle ohne diese Pflicht – bitte mit der VP bestätigen.
- **Computergestützte Systeme (GDP 3.3):** Änderung über Change Control dokumentieren und validieren (siehe Punkt 1).
- Ergebnis: Die vereinfachte Behandlung ist nach dieser Einschätzung mit GDP vereinbar, **wenn** SOP, Lieferantenqualifizierung und Validierung vorliegen und die VP das Verfahren freigibt. Die PLCS-SOPs lagen mir nicht vor; der Abgleich mit ihnen ist offen.

## 7. Migration, Konfiguration, Aktivierung

1. **Update:** `blistercenter` aktualisieren (20.0.1.3.0), `plcs_dashboard` installieren. Keine Migrationsskripte; neue Felder sind leer, der Website-Schalter wird für bestehende Websites mit „an“ belegt. Bestehende Wareneingänge erscheinen als „nicht geprüft“.
2. **Rollen:** Geschäftsführung → „Geschäftsführungs-Dashboard: Lesen“ oder „Finanzen“ (Bestandswert zusätzlich „Lager: Administrator“).
3. **Shop:** Website-Schalter prüfen; je berechtigtem Kontakt „Shop-Zugang (Benutzer)“ setzen (Rolle Freigabe); Portalregistrierung „auf Einladung“; Test mit einem freigegebenen und einem nicht freigegebenen Portalbenutzer.
4. **Automatische Freigabe:** Hersteller qualifizieren (Rolle Freigabe), zuständige VP in den Einstellungen prüfen, SOP freigeben, Validierung, dann VP: Lager und Versand › Automatische Freigabe (Verfahren) › einschalten.

## 8. Hinweise für die Übernahme in die Produktion

- Push auf `main` aktualisiert die Produktionsdatenbank auf Odoo.sh. Deshalb zuerst den Feature-Branch auf Odoo.sh als **Staging mit Kopie der Produktionsdaten** bauen, Tests und Tour dort laufen lassen und die Prüfanleitung durchgehen.
- Die Automatik bleibt nach dem Update aus; es ändert sich für den Wareneingang nichts, bis die VP einschaltet.
- Der Shop ist nach dem Update für Portalbenutzer ohne Shop-Zugang gesperrt – Freigaben vor dem Merge vorbereiten und den Zeitpunkt mit dem Vertrieb abstimmen.
- Merge nach `main` erst nach Freigabe durch VP/QMB (Change Control).
