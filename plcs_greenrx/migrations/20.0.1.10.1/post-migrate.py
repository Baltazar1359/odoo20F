"""1.10.1: Spalte „Charge“ im Bestand auch für Nicht-GreenRX-Ware (Los = Charge) füllen."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    quants = env["stock.quant"].search([("lot_id", "!=", False)])
    quants._compute_plcs_batch()
    quants.flush_recordset(["plcs_batch"])
