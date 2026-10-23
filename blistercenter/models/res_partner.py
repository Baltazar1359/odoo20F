from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import bc_auth
from .blistercenter_config import DISCOUNTS, PAYMENT_MODELS

# Felder, die nur die Rolle „Freigabe“ ändern darf (bei Blistercenter-Kunden)
APPROVAL_FIELDS = {
    "bc_shop_enabled",
    "bc_payment_model",
    "bc_discount",
    "bc_payment_block",
    "bc_payment_block_reason",
}
# Konditionen aus dem Standard, die bei Blistercentern ebenfalls Freigabe brauchen
APPROVAL_STANDARD_FIELDS = {
    "property_payment_term_id",
    "property_product_pricelist",
    "specific_property_product_pricelist",
}


# Nachweis der Shopfreischaltung setzt nur Odoo (Prüfbericht 19.10.2026)
PARTNER_PROTECTED_FIELDS = ("bc_shop_enabled_by", "bc_shop_enabled_date")


class ResPartner(models.Model):
    _inherit = "res.partner"

    bc_is_customer = fields.Boolean(
        "Blistercenter", tracking=True, copy=False,
        help="Kunde ist ein Blistercenter und bestellt im PLCS-Großhandel.",
    )
    bc_is_supplier = fields.Boolean(
        "Hersteller (Blistercenter)", tracking=True, copy=False,
        help="Lieferant, bei dem PLCS für Blistercenter bestellt (z. B. AstraZeneca).",
    )
    bc_shop_enabled = fields.Boolean("Shop freigeschaltet", tracking=True, copy=False)
    bc_shop_enabled_by = fields.Many2one("res.users", "Freigeschaltet von", readonly=True, copy=False)
    bc_shop_enabled_date = fields.Datetime("Freigeschaltet am", readonly=True, copy=False)
    bc_payment_model = fields.Selection(
        PAYMENT_MODELS, string="Zahlungsart", tracking=True, copy=False,
        help="Lastschrift: Der Einzug läuft über DATEV, das Mandat wird nur in DATEV geführt. "
             "Odoo druckt einen Hinweis auf die Rechnung; die Zahlung wird später in Odoo eingebucht.",
    )
    bc_discount = fields.Selection(
        DISCOUNTS, string="Rabatt auf HAP", default="0", tracking=True, copy=False,
        help="Rabatt des Blistercenters auf den Herstellerabgabepreis (Verkaufspreis des Artikels), "
             "gilt für alle Artikel. Erlaubt sind 0, 1 oder 2 %.",
    )
    bc_overdue_amount = fields.Monetary(
        "Überfällig", compute="_compute_bc_overdue", currency_field="currency_id",
        help="Offener Betrag gebuchter Rechnungen, deren Fälligkeit überschritten ist.",
    )
    bc_overdue_count = fields.Integer("Überfällige Rechnungen", compute="_compute_bc_overdue")
    bc_payment_block = fields.Boolean("Zahlungssperre", tracking=True, copy=False)
    bc_payment_block_reason = fields.Char("Grund der Zahlungssperre", tracking=True, copy=False)
    bc_can_approve = fields.Boolean(compute="_compute_bc_can_approve")
    bc_sale_count = fields.Integer("Bestellungen", compute="_compute_bc_counts")
    bc_supplierinfo_count = fields.Integer("Einkaufskonditionen", compute="_compute_bc_counts")
    bc_portal_user_count = fields.Integer("Portalzugänge", compute="_compute_bc_portal")
    bc_portal_warning = fields.Boolean(compute="_compute_bc_portal")

    def _compute_bc_portal(self):
        for partner in self:
            if not partner.id:
                partner.bc_portal_user_count = 0
                partner.bc_portal_warning = False
                continue
            users = self.env["res.users"].sudo().search([
                ("partner_id", "child_of", partner.commercial_partner_id.id), ("share", "=", True),
            ])
            partner.bc_portal_user_count = len(users)
            partner.bc_portal_warning = bool(partner.bc_is_customer and partner.bc_shop_enabled and not users)

    def _bc_overdue_invoices(self):
        """Gebuchte, nicht bezahlte Kundenrechnungen mit überschrittener Fälligkeit (nur lesen)."""
        self.ensure_one()
        if not self.commercial_partner_id.id:
            return self.env["account.move"]
        # Nur Rechnungen der aktuellen Firma: Betrag in einer Währung (Prüfbericht 21.10.2026)
        return self.env["account.move"].sudo().search([
            ("commercial_partner_id", "=", self.commercial_partner_id.id),
            ("company_id", "=", self.env.company.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("invoice_date_due", "<", fields.Date.context_today(self)),
        ])

    @api.depends_context("uid", "allowed_company_ids", "bc_force_overdue")
    def _compute_bc_overdue(self):
        for partner in self:
            invoices = partner._bc_overdue_invoices() if (
                partner.commercial_partner_id.bc_is_customer or self.env.context.get("bc_force_overdue")) else False
            partner.bc_overdue_count = len(invoices) if invoices else 0
            partner.bc_overdue_amount = sum(invoices.mapped("amount_residual_signed")) if invoices else 0.0

    @api.depends_context("uid")
    def _compute_bc_can_approve(self):
        allowed = self.env.user.has_group("blistercenter.group_bc_approver")
        for partner in self:
            partner.bc_can_approve = allowed

    def _compute_bc_counts(self):
        Sale = self.env["sale.order"]
        Info = self.env["product.supplierinfo"]
        for partner in self:
            if not partner.id:
                partner.bc_sale_count = partner.bc_supplierinfo_count = 0
                continue
            partner.bc_sale_count = Sale.search_count([("partner_id", "child_of", partner.commercial_partner_id.id)])
            partner.bc_supplierinfo_count = Info.search_count([("partner_id", "=", partner.id)])

    # ------------------------------------------------------------------
    # Rechte und Konsistenz
    # ------------------------------------------------------------------
    def _bc_check_approval_rights(self, vals, is_create=False):
        bc_auth.check_protected(self, vals, PARTNER_PROTECTED_FIELDS)
        if self.env.su or self.env.user.has_group("blistercenter.group_bc_approver"):
            return
        touched = APPROVAL_FIELDS & set(vals)
        # Prüfbericht 19.10.2026 R04: Kennzeichen „Blistercenter“ entfernen nur durch die Rolle Freigabe,
        # sonst ließen sich Zahlungssperre, Zahlungsziel und Rabatt umgehen
        if not is_create and "bc_is_customer" in vals and not vals["bc_is_customer"] \
                and any(p.bc_is_customer for p in self):
            touched |= {"bc_is_customer"}
        if is_create:
            # Beim Anlegen zählen nur gesetzte Werte
            touched = {f for f in touched if vals.get(f) and not (f == "bc_discount" and vals[f] == "0")}
        standard = APPROVAL_STANDARD_FIELDS & set(vals)
        if standard and not is_create:
            if any(p.commercial_partner_id.bc_is_customer for p in self):
                touched |= standard
        elif standard and vals.get("bc_is_customer"):
            touched |= {f for f in standard if vals.get(f)}
        if touched:
            labels = ", ".join(sorted(self._fields[f].string for f in touched))
            raise AccessError(self.env._(
                "Nur die Rolle „Blistercenter Freigabe“ darf bei Blistercentern ändern: %(fields)s",
                fields=labels,
            ))

    def _bc_prepare_vals(self, vals):
        vals = dict(vals)
        if vals.get("bc_payment_block"):
            if "bc_payment_block_reason" in vals:
                reasons = [vals["bc_payment_block_reason"]]
            else:
                reasons = [p.bc_payment_block_reason for p in self] or [False]
            if not all((r or "").strip() for r in reasons):
                raise UserError(self.env._("Für eine Zahlungssperre ist ein Grund Pflicht."))
        elif "bc_payment_block" in vals:
            vals["bc_payment_block_reason"] = False
        if "bc_shop_enabled" in vals:
            if vals["bc_shop_enabled"]:
                vals["bc_shop_enabled_by"] = self.env.uid
                vals["bc_shop_enabled_date"] = fields.Datetime.now()
            else:
                vals["bc_shop_enabled_by"] = False
                vals["bc_shop_enabled_date"] = False
        return vals

    def _bc_apply_defaults(self):
        """Standardwerte der Firma für neu gekennzeichnete Blistercenter übernehmen."""
        config = self.env["blistercenter.config"]._get()
        for partner in self.sudo():
            vals = {}
            if not partner.bc_payment_model:
                vals["bc_payment_model"] = config.default_payment_model
            if config.default_payment_term_id and not partner.property_payment_term_id:
                vals["property_payment_term_id"] = config.default_payment_term_id.id
            if config.default_pricelist_id and not partner.specific_property_product_pricelist:
                vals["property_product_pricelist"] = config.default_pricelist_id.id
            if vals:
                partner.write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        new_vals = []
        for vals in vals_list:
            self._bc_check_approval_rights(vals, is_create=True)
            if any(f in vals for f in ("bc_payment_block", "bc_shop_enabled")):
                vals = self.browse()._bc_prepare_vals(vals)
            new_vals.append(vals)
        partners = super().create(new_vals)
        partners.filtered("bc_is_customer")._bc_apply_defaults()
        return partners

    def write(self, vals):
        self._bc_check_approval_rights(vals)
        if any(f in vals for f in ("bc_payment_block", "bc_shop_enabled")):
            vals = self._bc_prepare_vals(vals)
        newly_bc = self.browse()
        if vals.get("bc_is_customer"):
            newly_bc = self.filtered(lambda p: not p.bc_is_customer)
        res = super().write(vals)
        if newly_bc:
            newly_bc._bc_apply_defaults()
        return res

    # ------------------------------------------------------------------
    # Smart-Buttons
    # ------------------------------------------------------------------
    def action_bc_open_orders(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_sale_orders")
        action["domain"] = [("partner_id", "child_of", self.commercial_partner_id.id)]
        action["context"] = {"default_partner_id": self.id}
        return action

    def action_bc_portal_access(self):
        """Standard-Assistent „Portalzugang gewähren“ für das Blistercenter und seine Kontakte."""
        self.ensure_one()
        partners = self.commercial_partner_id | self.commercial_partner_id.child_ids.filtered(
            lambda p: p.type == "contact")
        return self.env["portal.wizard"].with_context(
            active_model="res.partner", active_ids=partners.ids).action_open_wizard()

    def action_bc_new_phone_order(self):
        """Telefonbestellung für dieses Blistercenter erfassen."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_phone_order")
        action["context"] = {
            "default_partner_id": self.commercial_partner_id.id,
            "dialog_size": "large",
        }
        return action

    def action_bc_open_supplierinfo(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_supplierinfo")
        action["domain"] = [("partner_id", "=", self.id)]
        action["context"] = {"default_partner_id": self.id}
        return action
