from odoo import api, fields, models
from odoo.exceptions import UserError


class BlistercenterCollectiveAdd(models.TransientModel):
    """Ausgewählte offene Kundenmengen einer Sammelbestellung zuordnen."""

    _name = "blistercenter.collective.add"
    _description = "Kundenmengen zur Sammelbestellung"

    sale_line_ids = fields.Many2many("sale.order.line", string="Auftragspositionen", required=True)
    mode = fields.Selection(
        [("new", "Neue Sammelbestellung"), ("existing", "Vorhandene Sammelbestellung (Entwurf)")],
        string="Ziel", default="new", required=True,
    )
    supplier_id = fields.Many2one("res.partner", string="Hersteller", domain="[('bc_is_supplier', '=', True)]")
    collective_id = fields.Many2one(
        "blistercenter.collective.order", string="Sammelbestellung", domain="[('state', '=', 'draft')]",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ids = self.env.context.get("active_ids") or []
        if self.env.context.get("active_model") == "sale.order.line" and ids:
            lines = self.env["sale.order.line"].browse(ids).filtered(lambda line: line.bc_qty_open > 0)
            res["sale_line_ids"] = [fields.Command.set(lines.ids)]
            # Hersteller vorschlagen, wenn alle Artikel denselben ersten Lieferanten haben
            sellers = self.env["res.partner"]
            for product in lines.product_id:
                sellers |= product.seller_ids[:1].partner_id
            if len(sellers) == 1 and sellers.bc_is_supplier:
                res["supplier_id"] = sellers.id
        return res

    def action_apply(self):
        self.ensure_one()
        lines = self.sale_line_ids.filtered(lambda line: line.bc_qty_open > 0)
        if not lines:
            raise UserError(self.env._("Keine offenen Kundenmengen ausgewählt."))
        if self.mode == "existing":
            if not self.collective_id:
                raise UserError(self.env._("Bitte eine Sammelbestellung wählen."))
            collective = self.collective_id
        else:
            if not self.supplier_id:
                raise UserError(self.env._("Bitte einen Hersteller wählen."))
            companies = lines.company_id
            if len(companies) > 1:
                raise UserError(self.env._("Die Positionen gehören zu verschiedenen Firmen."))
            collective = self.env["blistercenter.collective.order"].create({
                "supplier_id": self.supplier_id.id,
                "company_id": companies.id,
            })
        self.env["blistercenter.collective.order.line"].create([
            {"order_id": collective.id, "sale_line_id": line.id, "qty": line.bc_qty_open}
            for line in lines
        ])
        return {
            "type": "ir.actions.act_window",
            "res_model": "blistercenter.collective.order",
            "res_id": collective.id,
            "view_mode": "form",
            "target": "current",
        }
