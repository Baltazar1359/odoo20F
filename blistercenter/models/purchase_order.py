from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools import bc_auth


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

    def button_cancel(self):
        """Prüfbericht 21.10.2026 Befund 7: Storno direkt im Einkauf hält die Sammelbestellung aktuell.

        Ohne Wareneingang: Sammelbestellung wird mit storniert, die Kundenmengen stehen wieder unter
        „Zu bestellen“. Mit gebuchtem Wareneingang: Storno gesperrt (sonst würde die schon gelieferte
        Menge erneut bestellt); den Rest storniert man über den Rückstand des Wareneingangs.
        """
        if bc_auth.has(self.env, "collective"):
            return super().button_cancel()  # Storno aus der Sammelbestellung heraus
        collective = self.bc_collective_ids.filtered(lambda c: c.state not in ("cancel", "done"))
        for order in self.filtered("bc_is_collective"):
            if order.picking_ids.filtered(lambda p: p.state == "done"):
                raise UserError(self.env._(
                    "%(po)s stammt aus einer Sammelbestellung und ist schon teilweise eingegangen. Stornieren "
                    "würde die gelieferte Menge erneut unter „Zu bestellen“ stellen. Bitte nur den offenen Rest "
                    "im Wareneingang (Rückstand) stornieren.", po=order.name))
        res = super().button_cancel()
        for coll in collective.filtered(lambda c: c.purchase_id.state == "cancel"):
            bc_auth.grant(coll.sudo(), "collective").state = "cancel"
            coll.message_post(body=self.env._(
                "Bestellung %(po)s beim Hersteller wurde storniert. Sammelbestellung mit storniert; die "
                "Kundenmengen stehen wieder unter „Zu bestellen“.", po=coll.purchase_id.name))
        return res

    def button_draft(self):
        """Stornierte Herstellerbestellung einer stornierten Sammelbestellung nicht wiederbeleben.

        Sonst stünde die Kundenmenge doppelt im Einkauf (wieder offen unter „Zu bestellen“ und in der
        reaktivierten Bestellung). Neu bestellen über die Sammelbestellung („Zurück in den Entwurf“).
        """
        for order in self.filtered("bc_is_collective"):
            if all(c.state == "cancel" for c in order.bc_collective_ids):
                raise UserError(self.env._(
                    "%(po)s gehört zu einer stornierten Sammelbestellung. Bitte die Sammelbestellung zurück in "
                    "den Entwurf setzen und neu anfragen.", po=order.name))
        return super().button_draft()

    def _bc_check_collective_quantities(self):
        """Herstellerbestellung muss die Kundenmengen der Sammelbestellung decken (Prüfbericht 19.10.2026 R05).

        1.6.3 (GPT-Prüfbericht 23.10.2026): Je Bestellposition geprüft, nicht je Artikel. Jede Kundenmenge
        muss mit einer Position dieser Bestellung verbunden sein; sonst findet die Freigabe die Lieferung
        des Kunden nicht (feste Zuordnung).
        """
        self.ensure_one()
        needed = {}
        for line in self.bc_collective_ids.filtered(lambda c: c.state != "cancel").line_ids:
            pline = line.purchase_line_id
            if not pline or pline.order_id != self or pline.product_id != line.product_id:
                raise UserError(self.env._(
                    "%(po)s: Die Kundenmenge %(order)s / %(product)s ist mit keiner Position dieser Bestellung "
                    "verbunden. Bitte die Sammelbestellung zurück in den Entwurf setzen und neu anfragen.",
                    po=self.name, order=line.sale_order_id.name, product=line.product_id.display_name))
            qty = line.sale_line_id.product_uom_id._compute_quantity(line.qty, line.product_id.uom_id)
            needed[pline] = needed.get(pline, 0.0) + qty
        for pline, qty in needed.items():
            product = pline.product_id
            ordered = pline.uom_id._compute_quantity(pline.product_qty, product.uom_id)
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


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _bc_collective_lines(self):
        return self.env["blistercenter.collective.order.line"].sudo().search([
            ("purchase_line_id", "in", self.ids), ("order_id.state", "!=", "cancel")])

    def write(self, vals):
        # 1.6.3: Artikel einer Position mit Kundenmengen nicht austauschen (Zuordnung ginge verloren)
        if "product_id" in vals and not bc_auth.has(self.env, "collective"):
            linked = self._bc_collective_lines().purchase_line_id
            if linked.filtered(lambda pl: pl.product_id.id != vals["product_id"]):
                raise UserError(self.env._(
                    "%(products)s gehört zu einer Sammelbestellung. Der Artikel kann hier nicht getauscht werden; "
                    "bitte über die Sammelbestellung ändern.",
                    products=", ".join(linked.product_id.mapped("display_name"))))
        return super().write(vals)

    def unlink(self):
        # 1.6.3: Position mit Kundenmengen nicht löschen oder ersetzen; Menge ändern geht weiter
        if not bc_auth.has(self.env, "collective"):
            linked = self._bc_collective_lines()
            if linked:
                raise UserError(self.env._(
                    "%(products)s gehört zu Sammelbestellung %(collective)s. Die Position kann nicht gelöscht oder "
                    "ersetzt werden (sonst fehlt die Zuordnung zum Kunden). Menge ändern ist möglich; sonst die "
                    "Sammelbestellung zurück in den Entwurf setzen und neu anfragen.",
                    products=", ".join(linked.product_id.mapped("display_name")),
                    collective=", ".join(linked.order_id.mapped("name"))))
        return super().unlink()
