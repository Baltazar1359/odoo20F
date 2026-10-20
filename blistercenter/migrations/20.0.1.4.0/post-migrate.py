"""1.4.0: PLCS-Farben für Website und Kundenportal einmalig setzen (spätere Editor-Änderungen bleiben)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["blistercenter.config"]._bc_apply_website_branding()
