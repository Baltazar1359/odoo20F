from odoo import fields, models
from odoo.exceptions import UserError


class BlistercenterRecall(models.TransientModel):
    """Rückrufauswertung: an wen wurden diese Chargen geliefert, wo liegt noch Bestand?"""

    _name = "blistercenter.recall.wizard"
    _description = "Rückrufauswertung"

    lot_ids = fields.Many2many("stock.lot", string="Chargen", required=True)
    block_lots = fields.Boolean("Chargen zugleich sperren")
    reason = fields.Char("Grund / Rückrufnummer")

    def _check(self):
        self.ensure_one()
        if not self.lot_ids:
            raise UserError(self.env._("Bitte mindestens eine Charge wählen."))
        if self.block_lots:
            if not (self.reason or "").strip():
                raise UserError(self.env._("Zum Sperren ist ein Grund Pflicht."))
            self.lot_ids.filtered(lambda lot: not lot.bc_blocked).write({
                "bc_blocked": True, "bc_block_reason": self.reason,
            })

    def _delivered_domain(self):
        return [
            ("lot_id", "in", self.lot_ids.ids),
            ("state", "=", "done"),
            ("location_dest_id.usage", "=", "customer"),
        ]

    def action_customers(self):
        """Lieferungen mit diesen Chargen, nach Kunde gruppiert."""
        self._check()
        pickings = self.env["stock.move.line"].search(self._delivered_domain()).picking_id
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Rückruf: betroffene Kunden"),
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", pickings.ids)],
            "context": {"group_by": ["partner_id"], "create": False},
        }

    def action_deliveries(self):
        """Gelieferte Mengen je Charge und Lieferung."""
        self._check()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Rückruf: gelieferte Mengen"),
            "res_model": "stock.move.line",
            "view_mode": "list",
            "views": [(self.env.ref("blistercenter.view_bc_recall_move_line_list").id, "list")],
            "domain": self._delivered_domain(),
            "context": {"group_by": ["lot_id", "picking_id"], "create": False},
        }

    def action_stock(self):
        """Bestand dieser Chargen im eigenen Lager."""
        self._check()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Rückruf: Bestand im Lager"),
            "res_model": "stock.quant",
            "view_mode": "list",
            "domain": [("lot_id", "in", self.lot_ids.ids), ("location_id.usage", "=", "internal")],
            "context": {"group_by": ["lot_id", "location_id"], "create": False},
        }
