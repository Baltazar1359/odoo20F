from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError


class StockLot(models.Model):
    _inherit = "stock.lot"

    bc_blocked = fields.Boolean("Charge gesperrt", tracking=True, copy=False)
    bc_block_reason = fields.Char("Sperrgrund", tracking=True, copy=False)
    bc_blocked_by = fields.Many2one("res.users", "Gesperrt von", readonly=True, copy=False)
    bc_blocked_date = fields.Datetime("Gesperrt am", readonly=True, copy=False)
    bc_can_unblock = fields.Boolean(compute="_compute_bc_can_unblock")

    @api.depends_context("uid")
    def _compute_bc_can_unblock(self):
        allowed = self.env.user.has_group("blistercenter.group_bc_approver")
        for lot in self:
            lot.bc_can_unblock = allowed

    def write(self, vals):
        if "bc_blocked" in vals:
            vals = dict(vals)
            if vals["bc_blocked"]:
                # Sperren darf jeder; ein Grund ist Pflicht
                reasons = [vals.get("bc_block_reason")] if "bc_block_reason" in vals else self.mapped("bc_block_reason")
                if not all((r or "").strip() for r in reasons or [False]):
                    raise UserError(self.env._("Für die Sperre einer Charge ist ein Grund Pflicht."))
                to_block = self.filtered(lambda lot: not lot.bc_blocked)
                res = super().write(vals)
                to_block.write({"bc_blocked_by": self.env.uid, "bc_blocked_date": fields.Datetime.now()})
                return res
            # Entsperren nur durch die Rolle Freigabe
            if any(lot.bc_blocked for lot in self) and not (
                self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")
            ):
                raise AccessError(self.env._("Eine gesperrte Charge darf nur die Rolle „Blistercenter Freigabe“ entsperren."))
            vals.update({"bc_block_reason": False, "bc_blocked_by": False, "bc_blocked_date": False})
        return super().write(vals)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bc_is_blistercenter = fields.Boolean(
        "Blistercenter", compute="_compute_bc_is_blistercenter", store=True, index=True,
    )

    @api.depends("partner_id.commercial_partner_id.bc_is_customer", "sale_id.bc_is_blistercenter")
    def _compute_bc_is_blistercenter(self):
        for picking in self:
            picking.bc_is_blistercenter = bool(
                picking.sale_id.bc_is_blistercenter or picking.partner_id.commercial_partner_id.bc_is_customer
            )

    def _bc_check_before_done(self):
        for picking in self.filtered(lambda p: p.picking_type_code == "outgoing"):
            blocked = picking.move_line_ids.filtered(lambda ml: ml.quantity and ml.lot_id.bc_blocked).lot_id
            if blocked:
                raise UserError(self.env._(
                    "%(picking)s enthält gesperrte Chargen: %(lots)s. Versand nicht möglich.",
                    picking=picking.name, lots=", ".join(blocked.mapped("name")),
                ))
            partner = (picking.sale_id.partner_id or picking.partner_id).commercial_partner_id
            if not partner.bc_is_customer:
                continue
            config = self.env["blistercenter.config"]._get(picking.company_id)
            if config.rule_block_delivery and partner.bc_payment_block:
                raise UserError(self.env._(
                    "%(customer)s hat eine Zahlungssperre (%(reason)s). %(picking)s kann nicht versendet werden.",
                    customer=partner.display_name, reason=partner.bc_payment_block_reason or "-", picking=picking.name,
                ))
            sale = picking.sale_id
            if sale and config.rule_prepayment_before_delivery and sale._bc_needs_prepayment():
                raise UserError(self.env._(
                    "%(order)s ist Vorkasse und noch nicht vollständig bezahlt. %(picking)s kann nicht versendet werden.",
                    order=sale.name, picking=picking.name,
                ))

    def _action_done(self):
        self._bc_check_before_done()
        res = super()._action_done()
        self.purchase_id.bc_collective_ids._bc_update_done()
        return res
