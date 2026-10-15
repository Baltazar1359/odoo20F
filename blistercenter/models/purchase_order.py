from odoo import api, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    bc_collective_ids = fields.One2many(
        "blistercenter.collective.order", "purchase_id", string="Sammelbestellungen",
    )
    bc_is_collective = fields.Boolean(
        "Aus Sammelbestellung", compute="_compute_bc_is_collective", store=True, index=True,
    )

    @api.depends("bc_collective_ids")
    def _compute_bc_is_collective(self):
        for order in self:
            order.bc_is_collective = bool(order.bc_collective_ids)

    def button_confirm(self):
        # Ware aus Sammelbestellungen geht immer in die Blistercenter-Quarantäne
        for order in self.filtered("bc_is_collective"):
            config = self.env["blistercenter.config"]._get(order.company_id)._bc_check_setup()
            if order.picking_type_id != config.receipt_type_id:
                raise UserError(self.env._(
                    "%(po)s stammt aus einer Sammelbestellung. Der Wareneingang muss über "
                    "„%(type)s“ laufen (Quarantäne und Freigabe durch die VP).",
                    po=order.name, type=config.receipt_type_id.display_name))
        return super().button_confirm()

    def action_bc_open_collective(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_collective_orders")
        action["domain"] = [("id", "in", self.bc_collective_ids.ids)]
        return action
