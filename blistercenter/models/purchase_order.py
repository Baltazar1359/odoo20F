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
            order._bc_check_collective_quantities()
        return super().button_confirm()

    def _bc_check_collective_quantities(self):
        """Prüfbericht 19.10.2026 R05: Herstellerbestellung muss die Kundenmengen der Sammelbestellung decken."""
        self.ensure_one()
        needed = {}
        for line in self.bc_collective_ids.filtered(lambda c: c.state != "cancel").line_ids:
            qty = line.sale_line_id.product_uom_id._compute_quantity(line.qty, line.product_id.uom_id)
            needed[line.product_id] = needed.get(line.product_id, 0.0) + qty
        for product, qty in needed.items():
            ordered = sum(
                pl.uom_id._compute_quantity(pl.product_qty, product.uom_id)
                for pl in self.order_line.filtered(lambda pl: pl.product_id == product))
            if product.uom_id.compare(ordered, qty) < 0:
                raise UserError(self.env._(
                    "%(po)s: Für %(product)s sind in der Sammelbestellung %(qty)s Kundenmengen, bestellt werden "
                    "nur %(ordered)s. Bitte Menge anpassen oder die Sammelbestellung prüfen.",
                    po=self.name, product=product.display_name, qty=qty, ordered=ordered))

    def action_bc_open_collective(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_collective_orders")
        action["domain"] = [("id", "in", self.bc_collective_ids.ids)]
        return action
