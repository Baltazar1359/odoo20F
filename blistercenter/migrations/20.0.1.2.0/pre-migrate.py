"""1.2.0: Vorkasse entfällt (Rechnung je Lieferung mit Zahlungsziel, Lastschrift über DATEV).

- Zahlungsmodell der Kunden: „Vorkasse“ → „Lastschrift (DATEV)“, „Rechnung“ → „Überweisung“.
- Kacheln „Anzahlungsrechnungen zu buchen“ und „Warten auf Zahlung“ entfernen.
Bestehende Anzahlungsrechnungen bleiben unverändert (Buchhaltung entscheidet).
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE res_partner SET bc_payment_model = CASE bc_payment_model
            WHEN 'prepayment' THEN 'direct_debit' WHEN 'invoice' THEN 'transfer' END
         WHERE bc_payment_model IN ('prepayment', 'invoice')
    """)
    cr.execute("""
        UPDATE blistercenter_config SET default_payment_model = CASE default_payment_model
            WHEN 'prepayment' THEN 'direct_debit' WHEN 'invoice' THEN 'transfer' END
         WHERE default_payment_model IN ('prepayment', 'invoice')
    """)
    cr.execute("DELETE FROM blistercenter_overview WHERE key IN ('downpayments_to_post', 'awaiting_payment')")
    cr.execute("""
        DELETE FROM ir_model_data WHERE module = 'blistercenter'
           AND name IN ('tile_downpayments_to_post', 'tile_awaiting_payment')
    """)
