from odoo import fields, models
from odoo.exceptions import UserError


class BlistercenterPartialWizard(models.TransientModel):
    """Teillieferung erlauben – nur nach Rücksprache mit dem Kunden, mit Vermerk."""

    _name = "blistercenter.partial.wizard"
    _description = "Teillieferung erlauben"

    order_id = fields.Many2one("sale.order", string="Bestellung", required=True, readonly=True)
    note = fields.Char(
        "Vermerk", required=True,
        help="Mit wem und wann die Teillieferung abgestimmt ist, z. B. „Telefonat mit Frau Muster, 15.10.“.",
    )

    def action_apply(self):
        self.ensure_one()
        if not self.order_id.bc_is_blistercenter:
            raise UserError(self.env._("Nur für Blistercenter-Bestellungen."))
        self.order_id._bc_set_partial(True, self.note)
        return {"type": "ir.actions.act_window_close"}


class BlistercenterShortWizard(models.TransientModel):
    """Zusätzliche VP-Freigabe für Kurzläufer (Restlaufzeit unter der Grenze)."""

    _name = "blistercenter.short.wizard"
    _description = "Kurzläufer freigeben"

    picking_id = fields.Many2one("stock.picking", string="Vorgang", required=True, readonly=True)
    lot_ids = fields.Many2many("stock.lot", string="Kurzläufer", related="picking_id.bc_short_lot_ids")
    reason = fields.Char("Begründung", required=True)

    def action_apply(self):
        self.ensure_one()
        self.picking_id._bc_approve_short(self.reason)
        return {"type": "ir.actions.act_window_close"}
