"""Bestellschein (1.4.0): feste Artikelliste (Shop-Sortiment) mit Mengen – für Telefon und Kundenportal.

Beide Wege legen den Auftrag über dieselbe Methode an, mit denselben Prüfungen.
"""
import re

from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

MAX_QTY = 9999
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


class SaleOrder(models.Model):
    _inherit = "sale.order"

    bc_submit_token = fields.Char(
        "Absende-Kennung", readonly=True, copy=False, index="btree_not_null",
        help="Einmal-Kennung des Online-Bestellscheins. Dasselbe Absenden zweimal (z. B. nach einer "
             "unterbrochenen Verbindung) legt keine zweite Bestellung an (Prüfbericht 21.10.2026).")

    _bc_submit_token_uniq = models.UniqueIndex(
        "(bc_submit_token) WHERE bc_submit_token IS NOT NULL",
        "Dieser Bestellschein wurde schon abgeschickt.")

    @api.model
    def _bc_find_submitted(self, partner, token):
        """Schon angelegte Bestellung zu dieser Absende-Kennung (nur desselben Kunden)."""
        if not (token and TOKEN_RE.match(token)):
            return self.browse()
        return self.sudo().search([
            ("bc_submit_token", "=", token),
            ("partner_id", "child_of", partner.commercial_partner_id.id)], limit=1)

    @api.model
    def _bc_sheet_products(self, company=None):
        """Artikel des Bestellscheins = Shop-Sortiment (R40), sortiert nach Bezeichnung."""
        company = company or self.env.company
        return self.env["product.product"].sudo().search([
            ("bc_shop_product", "=", True), ("sale_ok", "=", True),
            ("company_id", "in", [False, company.id]),
        ], order="name, default_code, id")

    @api.model
    def _bc_create_from_sheet(self, partner, quantities, channel, company=None, orderer=None,
                              orderer_name=None, client_ref=None, note=None, submit_token=None):
        """Auftrag aus einem Bestellschein anlegen (Entwurf).

        quantities: Liste (product.product, Menge). Nur Artikel des Bestellscheins, ganze Mengen
        0–9999, jeder Artikel höchstens einmal, mindestens eine Menge > 0.
        """
        partner = partner.commercial_partner_id
        company = company or self.env.company
        if not partner.bc_is_customer:
            raise UserError(self.env._("Der Bestellschein ist nur für Blistercenter."))
        allowed = self._bc_sheet_products(company)
        seen = set()
        lines = []
        for product, qty in quantities:
            if not product or product.id not in allowed.ids:
                raise UserError(self.env._(
                    "%s steht nicht auf dem Bestellschein.", product.sudo().display_name or "?"))
            if product.id in seen:
                raise UserError(self.env._("%s steht doppelt im Bestellschein.", product.sudo().display_name))
            seen.add(product.id)
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty != int(qty) \
                    or qty < 0 or qty > MAX_QTY:
                raise UserError(self.env._(
                    "Menge für %(product)s: bitte eine ganze Zahl von 0 bis %(max)s.",
                    product=product.sudo().display_name, max=MAX_QTY))
            if qty:
                lines.append(Command.create({"product_id": product.id, "product_uom_qty": int(qty)}))
        if not lines:
            raise UserError(self.env._("Bitte bei mindestens einem Artikel eine Menge eintragen."))
        vals = {
            "partner_id": partner.id,
            "company_id": company.id,
            "bc_channel": channel,
            "order_line": lines,
        }
        if orderer:
            vals["bc_orderer_id"] = orderer.id
        if orderer_name:
            vals["bc_orderer_name"] = orderer_name.strip()[:128]
        if client_ref:
            vals["client_order_ref"] = client_ref.strip()[:64]
        if submit_token:
            if not TOKEN_RE.match(submit_token):
                raise UserError(self.env._("Der Bestellschein ist abgelaufen. Bitte Seite neu laden."))
            vals["bc_submit_token"] = submit_token
        order = self.with_company(company).create(vals)
        if note and note.strip():
            order.message_post(body=Markup("<b>%s</b><br/>%s") % (
                self.env._("Bemerkung zur Bestellung:"), escape(note.strip()[:2000])))
        return order
