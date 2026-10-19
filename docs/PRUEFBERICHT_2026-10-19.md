# Prüfbericht 19.10.2026 (externes Audit) – Umsetzung

Grundlage: Prüfbericht zu `main` = edd5364 (GreenRX 20.0.1.9.1, Blistercenter 20.0.1.1.1).
Umgesetzt auf Branch `fix/pruefbericht-1019` (auf Basis von `feat/blistercenter-rechnung`, Blistercenter 1.2.0).
Freigabe Mehrdad 19.10.2026. Versionen: **Blistercenter 20.0.1.2.1**, **GreenRX 20.0.1.9.2**.

## Ergebnis

| Nr. | Befund | Prio (Bericht → umgesetzt) | Umsetzung | Test |
|---|---|---|---|---|
| R01 | Kommissionierer-Name überschreibbar | P1 | Nachweisfelder der Lieferung (gebucht/kommissioniert von, am) nur über die geprüften Aktionen; direkter Schreibzugriff → Fehler | `test_r01_picker_cannot_forge_second_person` |
| R02 | Kontextwert `bc_in_done` erhält Endkontrolle nach Chargenwechsel | P1 | Kontextwert ersetzt durch interne Freigabe (`tools/bc_auth.py`, gleiches Muster wie `grx_auth`) | `test_r02_rpc_context_cannot_keep_check_after_lot_change` |
| R03 | Kurzläufer-Freigabe ohne VP | P1 | Freigabefelder (Chargen, VP, Zeit, Begründung) nur über „Kurzläufer freigeben“ | `test_r03_warehouse_user_cannot_forge_short_approval` |
| R04 | Kundenkennzeichen entfernen hebt Zahlungssperre auf | P1 | Kennzeichen entfernen nur Rolle Freigabe; Auftrag hält seine Blistercenter-Zuordnung ab Bestätigung fest | `test_r04_customer_flag_does_not_bypass_payment_block` |
| R05 | Status der Sammelbestellung zurücksetzbar | P1 | Status und Herstellerbestellung nur über die Übergänge; Herstellerbestellung muss die Kundenmengen decken (mehr ist erlaubt) | `test_r05_collective_state_cannot_unlock_ordered_lines` |
| R06 | Sammelmenge > Auftragsmenge | P2 | Prüfung auch bei Änderung der Auftragsmenge und beim Reaktivieren; Positionen werden beim Bündeln gesperrt (gleichzeitige Arbeit) | `test_r06_*` (2) |
| R07 | Teillieferung ohne Vermerk | P2 → **P1** | Teillieferungs-Nachweis nur über den Assistenten; Versand prüft zusätzlich Vermerk und Person | `test_r07_partial_cannot_be_forged_without_agreement` |
| R08 | GreenRX-Override firmenübergreifend | P1 → P2 (eine Firma) | Rechte- und Firmenprüfung vor `sudo()` in beiden Aktionen | `test_r08_override_cannot_touch_other_company` |
| R09 | Boxen je Firma | P2 → P3 | **Nicht geändert.** Odoo 20 berechnet die Firma einer Box aus ihrem Inhalt, leere Boxen haben keine Firma. Boxen bleiben firmenübergreifend eindeutig; eine zweite Firma bekommt ein eigenes Präfix (Einstellung vorhanden). | `test_r09_second_company_uses_own_box_prefix` |
| R10 | Widersprüchliche/beschädigte Codes | P2 | IFA-GTIN: Länge vor dem Auffüllen; GTIN↔PPN und NTIN↔NHRN müssen dieselbe PZN ergeben; Klammer-Schreibweise muss vollständig lesbar sein | `test_r10_*` (4) |
| R11 | Auto-Artikel mit falscher PZN | P2 | Gleiche PZN- und GTIN-Prüfung wie bei Scan und Handeingabe | `test_r11_auto_create_rejects_invalid_pzn` |

Zusätzlich geschützt (gleiches Muster): „Aus Lagerbestand liefern“ (Position und Bewegung), Sperrnachweis der Charge,
Nachweis der Shopfreischaltung, Verknüpfung Rechnung → Lieferung (1.2.0). Test `test_other_protected_fields`.

## Prüfungen

- Lokal (Odoo 20.0 Community, Quellstand dc660eb2, Chrome headless): **207 Tests, 0 Fehler** (Blistercenter 70, GreenRX 189 inkl. Browser-Tests).
- Gegenprobe: Die 16 neuen Tests gegen den Stand vor der Korrektur → **15 scheitern** (R09 ist eine Dokumentationsprobe).
- Update 1.2.0/1.9.1 → 1.2.1/1.9.2 ohne Warnung.
- Die Testdateien des Prüfers lagen nicht vor; die Tests bilden seine beschriebenen Fälle nach.

## Weitere Vorschläge des Berichts

| Vorschlag | Stand |
|---|---|
| Automatische Prüfungen je Änderung | Odoo.sh testet jeden Push. GitHub Actions optional. |
| Einheitliches Schutzmuster | Umgesetzt (`tools/bc_auth.py`). |
| Rechnungsfälle (Vorkasse) | Durch 1.2.0 überholt (Vorkasse entfällt, Rechnung je Lieferung). |
| Gleichzeitige Arbeit | Zeilensperre beim Bündeln; eigene Tests mit getrennten Transaktionen offen. |
| Upgrade mit anonymisierter Datenkopie | Offen, vor Echtbetrieb GreenRX. |
| Prüfanleitungen aktualisieren | Blistercenter in 1.2.0 aktualisiert; GreenRX-Prüfanleitung offen. |
