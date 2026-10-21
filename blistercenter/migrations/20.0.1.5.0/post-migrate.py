"""1.5.0: Export 0 % automatisch erkennen, falls der deutsche Kontenrahmen schon geladen ist."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env["res.company"].search([]):
        env["blistercenter.config"]._bc_setup_tax_positions(company)
