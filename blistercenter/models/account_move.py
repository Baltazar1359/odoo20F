from odoo import api, fields, models

from ..tools import bc_auth


class AccountMove(models.Model):
    _inherit = "account.move"

    bc_picking_id = fields.Many2one(
        "stock.picking", string="Lieferung (Blistercenter)", readonly=True, copy=False, index="btree_not_null",
        help="Rechnung je Lieferung: Lieferung, zu der Odoo diese Rechnung angelegt hat.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            bc_auth.check_protected(self, vals, ("bc_picking_id",))
        return super().create(vals_list)

    def write(self, vals):
        bc_auth.check_protected(self, vals, ("bc_picking_id",))
        return super().write(vals)
