from odoo import api, fields, models
from odoo.exceptions import UserError

BC_STATES = [
    ("new", "Eingegangen"),
    ("blocked", "Zahlungssperre"),
    ("awaiting_payment", "Wartet auf Zahlung"),
    ("to_procure", "Zu beschaffen"),
    ("procuring", "In Beschaffung"),
    ("ready", "Lieferbereit"),
    ("partial", "Teilweise geliefert"),
    ("done", "Geliefert"),
    ("cancel", "Storniert"),
]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    bc_is_blistercenter = fields.Boolean(
        "Blistercenter-Bestellung", related="partner_id.commercial_partner_id.bc_is_customer",
        store=True, index=True,
    )
    bc_payment_model = fields.Selection(related="partner_id.commercial_partner_id.bc_payment_model", string="Zahlungsmodell")
    bc_payment_block = fields.Boolean(related="partner_id.commercial_partner_id.bc_payment_block", string="Zahlungssperre")
    bc_paid_amount = fields.Monetary("Bezahlt", compute="_compute_bc_payment", currency_field="currency_id")
    bc_is_paid = fields.Boolean("Vollständig bezahlt", compute="_compute_bc_payment")
    bc_state = fields.Selection(BC_STATES, string="Bearbeitungsstatus", compute="_compute_bc_state")
    bc_collective_ids = fields.Many2many(
        "blistercenter.collective.order", string="Sammelbestellungen", compute="_compute_bc_collective_ids",
    )
    bc_collective_count = fields.Integer(compute="_compute_bc_collective_ids")

    @api.depends("invoice_ids.state", "invoice_ids.payment_state", "invoice_ids.amount_total", "amount_total")
    def _compute_bc_payment(self):
        for order in self:
            posted = order.invoice_ids.filtered(lambda m: m.state == "posted")
            paid = sum(
                m.amount_total for m in posted
                if m.move_type == "out_invoice" and m.payment_state in ("paid", "in_payment")
            )
            refunded = sum(m.amount_total for m in posted if m.move_type == "out_refund")
            order.bc_paid_amount = paid - refunded
            order.bc_is_paid = order.currency_id.compare_amounts(paid - refunded, order.amount_total) >= 0

    def _bc_needs_prepayment(self):
        self.ensure_one()
        return self.bc_payment_model == "prepayment" and not self.bc_is_paid

    def _compute_bc_collective_ids(self):
        for order in self:
            collective = order.order_line.bc_collective_line_ids.order_id
            order.bc_collective_ids = collective
            order.bc_collective_count = len(collective)

    @api.depends("state", "delivery_status", "bc_is_paid", "order_line.bc_qty_open")
    def _compute_bc_state(self):
        for order in self:
            if order.state == "cancel":
                state = "cancel"
            elif order.state in ("draft", "sent"):
                state = "new"
            elif order.bc_payment_block:
                state = "blocked"
            elif order._bc_needs_prepayment():
                state = "awaiting_payment"
            elif order.delivery_status == "full":
                state = "done"
            elif order.delivery_status in ("partial", "started"):
                state = "partial"
            elif any(
                p.state == "assigned" and p.picking_type_code == "outgoing" for p in order.picking_ids
            ):
                state = "ready"
            elif any(line.bc_qty_open > 0 for line in order.order_line):
                state = "to_procure"
            else:
                state = "procuring"
            order.bc_state = state

    # ------------------------------------------------------------------
    # Prozessregeln
    # ------------------------------------------------------------------
    def _confirmation_error_message(self):
        message = super()._confirmation_error_message()
        if message:
            return message
        partner = self.partner_id.commercial_partner_id
        config = self.env["blistercenter.config"]._get(self.company_id)
        if partner.bc_is_customer and partner.bc_payment_block and config.rule_block_confirm:
            return self.env._(
                "%(customer)s hat eine Zahlungssperre (%(reason)s). Die Bestellung kann nicht bestätigt werden.",
                customer=partner.display_name, reason=partner.bc_payment_block_reason or "-",
            )
        if self.website_id and config.rule_shop_requires_enable and not (partner.bc_is_customer and partner.bc_shop_enabled):
            return self.env._(
                "Shop-Bestellungen sind nur für freigeschaltete Blistercenter möglich (%(customer)s ist nicht freigeschaltet).",
                customer=partner.display_name,
            )
        return False

    def _cart_add(self, product_id, quantity=1.0, **kwargs):
        self._bc_check_shop_access()
        return super()._cart_add(product_id, quantity, **kwargs)

    def _bc_check_shop_access(self):
        for order in self:
            config = self.env["blistercenter.config"]._get(order.company_id)
            if not config.rule_shop_requires_enable:
                continue
            partner = order.partner_id.commercial_partner_id
            if not (partner.bc_is_customer and partner.bc_shop_enabled):
                raise UserError(self.env._(
                    "Der Shop ist nur für freigeschaltete Blistercenter. Bitte melden Sie sich an "
                    "oder wenden Sie sich an PLCS für die Freischaltung."
                ))
            if partner.bc_payment_block and config.rule_block_confirm:
                raise UserError(self.env._(
                    "Für Ihr Kundenkonto ist eine Zahlungssperre hinterlegt. Bitte wenden Sie sich an PLCS."
                ))

    def _action_confirm(self):
        res = super()._action_confirm()
        for order in self.filtered("bc_is_blistercenter"):
            config = self.env["blistercenter.config"]._get(order.company_id)
            if config.notify_new_order and config.sales_user_id:
                # Aufgabe ohne E-Mail-Benachrichtigung (mail_activity_quick_update)
                order.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=self.env._("Neue Blistercenter-Bestellung bearbeiten"),
                    user_id=config.sales_user_id.id,
                )
        return res

    def action_bc_open_collective(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_collective_orders")
        action["domain"] = [("id", "in", self.bc_collective_ids.ids)]
        return action


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    bc_is_blistercenter = fields.Boolean(related="order_id.bc_is_blistercenter", store=True, index=True)
    bc_collective_line_ids = fields.One2many(
        "blistercenter.collective.order.line", "sale_line_id", string="Sammelbestellungs-Positionen",
    )
    bc_qty_allocated = fields.Float(
        "In Sammelbestellung", compute="_compute_bc_qty", store=True, digits="Product Unit",
    )
    bc_qty_open = fields.Float(
        "Offen zu bündeln", compute="_compute_bc_qty", store=True, digits="Product Unit",
    )

    def _bc_is_goods_line(self):
        self.ensure_one()
        return bool(
            self.product_id and self.product_id.type == "consu" and not self.display_type
            and not self.is_downpayment and not getattr(self, "is_delivery", False)
        )

    @api.depends(
        "product_uom_qty", "state", "product_id", "display_type",
        "bc_collective_line_ids.qty", "bc_collective_line_ids.order_id.state",
    )
    def _compute_bc_qty(self):
        for line in self:
            allocated = sum(
                c.qty for c in line.bc_collective_line_ids if c.order_id.state != "cancel"
            )
            line.bc_qty_allocated = allocated
            if line.state == "sale" and line._bc_is_goods_line():
                line.bc_qty_open = max(line.product_uom_qty - allocated, 0.0)
            else:
                line.bc_qty_open = 0.0

    def action_bc_add_to_collective(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Zur Sammelbestellung"),
            "res_model": "blistercenter.collective.add",
            "view_mode": "form",
            "target": "new",
            "context": {"active_model": "sale.order.line", "active_ids": self.ids},
        }
