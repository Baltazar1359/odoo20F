from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    bc_picking_id = fields.Many2one(
        "stock.picking", string="Lieferung (Blistercenter)", readonly=True, copy=False, index="btree_not_null",
        help="Rechnung je Lieferung: Lieferung, zu der Odoo diese Rechnung angelegt hat.",
    )
