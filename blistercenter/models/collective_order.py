from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class BlistercenterCollectiveOrder(models.Model):
    """Sammelbestellung: bündelt Mengen aus Kundenaufträgen zu einer Anfrage beim Hersteller.

    Speichert nur die Zuordnung (welche Auftragsposition mit welcher Menge),
    keine Kopie von Auftrags- oder Bestelldaten. Die Bestellung beim Hersteller
    ist eine normale Odoo-Einkaufsbestellung.
    """

    _name = "blistercenter.collective.order"
    _description = "Sammelbestellung"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char("Nummer", required=True, readonly=True, copy=False, default="/")
    company_id = fields.Many2one(
        "res.company", string="Firma", required=True, readonly=True, default=lambda self: self.env.company,
    )
    supplier_id = fields.Many2one(
        "res.partner", string="Hersteller", required=True, tracking=True, check_company=True,
        domain="[('bc_is_supplier', '=', True)]",
    )
    user_id = fields.Many2one(
        "res.users", string="Verantwortlich", tracking=True,
        default=lambda self: self.env["blistercenter.config"]._get().purchase_user_id or self.env.user,
    )
    state = fields.Selection(
        [
            ("draft", "Entwurf"),
            ("ordered", "Anfrage erstellt"),
            ("done", "Erledigt"),
            ("cancel", "Storniert"),
        ],
        string="Status", default="draft", required=True, readonly=True, tracking=True, copy=False,
    )
    line_ids = fields.One2many("blistercenter.collective.order.line", "order_id", string="Kundenmengen", copy=False)
    purchase_id = fields.Many2one("purchase.order", string="Bestellung beim Hersteller", readonly=True, copy=False, tracking=True)
    purchase_state = fields.Selection(related="purchase_id.state", string="Status Bestellung")
    receipt_status = fields.Selection(related="purchase_id.receipt_status", string="Wareneingang")
    note = fields.Text("Notiz")
    customer_count = fields.Integer("Kunden", compute="_compute_summary")
    product_count = fields.Integer("Artikel", compute="_compute_summary")

    @api.depends("line_ids.partner_id", "line_ids.product_id")
    def _compute_summary(self):
        for order in self:
            order.customer_count = len(order.line_ids.partner_id)
            order.product_count = len(order.line_ids.product_id)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                company = self.env["res.company"].browse(vals.get("company_id")) if vals.get("company_id") else self.env.company
                vals["name"] = self.env["ir.sequence"].with_company(company).next_by_code("blistercenter.collective.order") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if "supplier_id" in vals and any(o.state != "draft" for o in self):
            raise UserError(self.env._("Der Hersteller lässt sich nur im Entwurf ändern."))
        return super().write(vals)

    def unlink(self):
        if any(o.state != "draft" for o in self):
            raise UserError(self.env._("Nur Sammelbestellungen im Entwurf können gelöscht werden. Sonst bitte stornieren."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Abläufe
    # ------------------------------------------------------------------
    def action_create_purchase(self):
        """Erzeugt die Anfrage beim Hersteller (Einkauf, Status Anfrage). Bestellt wird manuell."""
        for order in self:
            if order.state != "draft":
                raise UserError(self.env._("%(name)s ist nicht im Entwurf.", name=order.name))
            if not order.line_ids:
                raise UserError(self.env._("%(name)s enthält keine Kundenmengen.", name=order.name))
            order.line_ids._bc_check_allowed()
            qty_by_product = defaultdict(float)
            for line in order.line_ids:
                qty_by_product[line.product_id] += line.sale_line_id.product_uom_id._compute_quantity(
                    line.qty, line.product_id.uom_id,
                )
            purchase = self.env["purchase.order"].with_company(order.company_id).create({
                "partner_id": order.supplier_id.id,
                "company_id": order.company_id.id,
                "origin": order.name,
                "user_id": order.user_id.id,
                "order_line": [
                    fields.Command.create({
                        "product_id": product.id,
                        "product_qty": qty,
                        "uom_id": product.uom_id.id,
                    })
                    for product, qty in qty_by_product.items()
                ],
            })
            for line in order.line_ids:
                line.purchase_line_id = purchase.order_line.filtered(lambda pl: pl.product_id == line.product_id)[:1]
            order.write({"purchase_id": purchase.id, "state": "ordered"})
            order.message_post(body=self.env._(
                "Anfrage %(po)s beim Hersteller erstellt. Bitte prüfen und manuell bestellen.", po=purchase.name,
            ))
            config = self.env["blistercenter.config"]._get(order.company_id)
            if config.notify_purchase_created and order.user_id:
                order.with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=self.env._("Anfrage %(po)s beim Hersteller bestellen", po=purchase.name),
                    user_id=order.user_id.id,
                )
        return self.action_open_purchase() if len(self) == 1 else True

    def action_open_purchase(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": self.purchase_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_cancel(self):
        for order in self:
            if order.state == "cancel":
                continue
            if order.purchase_id and order.purchase_id.state not in ("draft", "sent", "cancel"):
                raise UserError(self.env._(
                    "Die Bestellung %(po)s ist schon bestätigt. Bitte erst dort stornieren.", po=order.purchase_id.name,
                ))
            if order.purchase_id and order.purchase_id.state != "cancel":
                order.purchase_id.button_cancel()
            order.state = "cancel"
        return True

    def action_set_draft(self):
        for order in self:
            if order.state != "cancel":
                raise UserError(self.env._("Nur stornierte Sammelbestellungen können zurück in den Entwurf."))
            order.line_ids._bc_check_allowed()
            order.write({"state": "draft", "purchase_id": False})
            order.line_ids.purchase_line_id = False
        return True

    def _bc_update_done(self):
        """Auf Erledigt setzen, wenn der Wareneingang der Herstellerbestellung vollständig ist."""
        for order in self:
            if order.state == "ordered" and order.purchase_id.receipt_status == "full":
                order.state = "done"


class BlistercenterCollectiveOrderLine(models.Model):
    _name = "blistercenter.collective.order.line"
    _description = "Sammelbestellung – Kundenmenge"
    _order = "order_id, product_id, id"

    order_id = fields.Many2one("blistercenter.collective.order", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="order_id.company_id", store=True)
    sale_line_id = fields.Many2one(
        "sale.order.line", string="Auftragsposition", required=True, ondelete="restrict", index=True,
        domain="[('bc_is_blistercenter', '=', True), ('state', '=', 'sale'), ('bc_qty_open', '>', 0)]",
    )
    sale_order_id = fields.Many2one(related="sale_line_id.order_id", string="Kundenauftrag", store=True)
    partner_id = fields.Many2one(related="sale_line_id.order_id.partner_id", string="Kunde", store=True)
    product_id = fields.Many2one(related="sale_line_id.product_id", string="Artikel", store=True)
    uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="Einheit")
    qty_ordered_customer = fields.Float(related="sale_line_id.product_uom_qty", string="Menge im Auftrag")
    qty = fields.Float("Menge", required=True, digits="Product Unit")
    purchase_line_id = fields.Many2one("purchase.order.line", string="Position Bestellung", readonly=True, copy=False)
    state = fields.Selection(related="order_id.state")

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if any(line.order_id.state != "draft" for line in lines):
            raise UserError(self.env._("Kundenmengen lassen sich nur im Entwurf hinzufügen."))
        lines._bc_check_allowed()
        return lines

    def write(self, vals):
        if set(vals) - {"purchase_line_id"} and any(line.order_id.state != "draft" for line in self):
            raise UserError(self.env._("Kundenmengen lassen sich nur im Entwurf ändern."))
        res = super().write(vals)
        if {"qty", "sale_line_id"} & set(vals):
            self._bc_check_allowed()
        return res

    def unlink(self):
        if any(line.order_id.state != "draft" for line in self):
            raise UserError(self.env._("Kundenmengen lassen sich nur im Entwurf entfernen."))
        return super().unlink()

    @api.constrains("qty", "sale_line_id")
    def _check_qty(self):
        for line in self:
            if line.qty <= 0:
                raise ValidationError(self.env._("Die Menge muss größer als 0 sein."))
            sale_line = line.sale_line_id
            allocated = sum(
                c.qty for c in sale_line.bc_collective_line_ids if c.order_id.state != "cancel"
            )
            if sale_line.product_uom_id.compare(allocated, sale_line.product_uom_qty) > 0:
                raise ValidationError(self.env._(
                    "%(order)s / %(product)s: In Sammelbestellungen wären %(alloc)s, im Auftrag stehen nur %(qty)s.",
                    order=sale_line.order_id.name, product=sale_line.product_id.display_name,
                    alloc=allocated, qty=sale_line.product_uom_qty,
                ))

    def _bc_check_allowed(self):
        """Prozessregeln für Kundenmengen in Sammelbestellungen."""
        for line in self:
            sale_line = line.sale_line_id
            order = sale_line.order_id
            partner = order.partner_id.commercial_partner_id
            if order.company_id != line.order_id.company_id:
                raise ValidationError(self.env._("Auftrag und Sammelbestellung gehören zu verschiedenen Firmen."))
            if not partner.bc_is_customer:
                raise ValidationError(self.env._("%(order)s ist keine Blistercenter-Bestellung.", order=order.name))
            if order.state != "sale":
                raise ValidationError(self.env._("%(order)s ist nicht bestätigt.", order=order.name))
            if not sale_line._bc_is_goods_line():
                raise ValidationError(self.env._("Nur Warenpositionen können gebündelt werden."))
            config = self.env["blistercenter.config"]._get(order.company_id)
            if partner.bc_payment_block and config.rule_block_confirm:
                raise ValidationError(self.env._(
                    "%(customer)s hat eine Zahlungssperre. %(order)s kann nicht gebündelt werden.",
                    customer=partner.display_name, order=order.name,
                ))
            if config.rule_prepayment_before_collective and order._bc_needs_prepayment():
                raise ValidationError(self.env._(
                    "%(order)s ist Vorkasse und noch nicht bezahlt. Erst nach Zahlungseingang bündeln.",
                    order=order.name,
                ))
