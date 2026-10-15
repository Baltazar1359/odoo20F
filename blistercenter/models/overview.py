from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

TILE_KEYS = [
    ("new_orders", "Neue Bestellungen"),
    ("downpayments_to_post", "Anzahlungsrechnungen zu buchen"),
    ("awaiting_payment", "Warten auf Zahlung"),
    ("payment_blocks", "Zahlungssperren"),
    ("to_procure", "Offene Kundenmengen"),
    ("open_collective", "Offene Sammelbestellungen"),
    ("open_receipts", "Offene Wareneingänge"),
    ("to_release", "Freigabe Wareneingang (VP)"),
    ("open_deliveries", "Offene Lieferungen"),
    ("to_check", "Endkontrolle offen"),
    ("short_dated", "Kurzläufer im Bestand"),
    ("blocked_lots", "Gesperrte Chargen"),
]


class BlistercenterOverview(models.Model):
    """Kacheln der Übersicht. Die Zahlen werden live aus den Standarddaten berechnet."""

    _name = "blistercenter.overview"
    _description = "Blistercenter-Übersicht"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    key = fields.Selection(TILE_KEYS, required=True)
    sequence = fields.Integer(default=10)
    description = fields.Char(translate=True)
    count = fields.Integer("Anzahl", compute="_compute_count")

    _key_uniq = models.Constraint("unique(key)", "Jede Kachel gibt es nur einmal.")

    def _get_tile(self):
        """(Modell, Domain, Aktion-XML-ID) der Kachel für die aktuelle Firma."""
        self.ensure_one()
        company_ids = self.env.companies.ids
        key = self.key
        if key == "new_orders":
            return ("sale.order", [
                ("bc_is_blistercenter", "=", True), ("company_id", "in", company_ids),
                "|", ("state", "=", "sent"), "&", ("state", "=", "draft"), ("website_id", "=", False),
            ], "blistercenter.action_bc_sale_orders")
        if key == "downpayments_to_post":
            return ("account.move", [
                ("company_id", "in", company_ids), ("move_type", "=", "out_invoice"), ("state", "=", "draft"),
                ("commercial_partner_id.bc_is_customer", "=", True),
                ("invoice_line_ids.sale_line_ids.is_downpayment", "=", True),
            ], "blistercenter.action_bc_invoices")
        if key == "awaiting_payment":
            orders = self.env["sale.order"].search([
                ("bc_is_blistercenter", "=", True), ("company_id", "in", company_ids), ("state", "=", "sale"),
                ("partner_id.commercial_partner_id.bc_payment_model", "=", "prepayment"),
            ])
            ids = orders.filtered(lambda o: not o.bc_is_paid).ids
            return ("sale.order", [("id", "in", ids)], "blistercenter.action_bc_sale_orders")
        if key == "payment_blocks":
            return ("res.partner", [("bc_is_customer", "=", True), ("bc_payment_block", "=", True)],
                    "blistercenter.action_bc_customers")
        if key == "to_procure":
            return ("sale.order.line", [
                ("bc_is_blistercenter", "=", True), ("company_id", "in", company_ids), ("bc_qty_open", ">", 0),
            ], "blistercenter.action_bc_sale_lines")
        if key == "open_collective":
            return ("blistercenter.collective.order", [
                ("company_id", "in", company_ids), ("state", "in", ("draft", "ordered")),
            ], "blistercenter.action_bc_collective_orders")
        if key == "open_receipts":
            return ("stock.picking", [
                ("company_id", "in", company_ids), ("picking_type_code", "=", "incoming"),
                ("purchase_id.bc_is_collective", "=", True), ("state", "not in", ("done", "cancel")),
            ], "blistercenter.action_bc_receipts")
        if key == "to_release":
            return ("stock.picking", [
                ("company_id", "in", company_ids), ("bc_kind", "=", "release"),
                ("state", "not in", ("done", "cancel", "draft")),
            ], "blistercenter.action_bc_release")
        if key == "to_check":
            return ("stock.picking", [
                ("company_id", "in", company_ids), ("bc_kind", "=", "delivery"),
                ("bc_picked_by", "!=", False), ("state", "not in", ("done", "cancel")),
            ], "blistercenter.action_bc_check")
        if key == "short_dated":
            months = self.env["blistercenter.config"]._get().short_shelf_months
            limit = fields.Datetime.now() + relativedelta(months=months)
            return ("stock.quant", [
                ("company_id", "in", company_ids), ("location_id.usage", "=", "internal"),
                ("quantity", ">", 0), ("lot_id.expiration_date", "!=", False),
                ("lot_id.expiration_date", "<", limit),
            ], "blistercenter.action_bc_short_quants")
        if key == "open_deliveries":
            return ("stock.picking", [
                ("company_id", "in", company_ids), ("picking_type_code", "=", "outgoing"),
                ("bc_is_blistercenter", "=", True), ("state", "not in", ("done", "cancel", "draft")),
            ], "blistercenter.action_bc_deliveries")
        if key == "blocked_lots":
            return ("stock.lot", [("bc_blocked", "=", True)], "blistercenter.action_bc_lots_blocked")
        return (False, [], False)

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_count(self):
        for tile in self:
            model, domain, _xmlid = tile._get_tile()
            if not model or not self.env[model].has_access("read"):
                tile.count = 0
                continue
            tile.count = self.env[model].search_count(domain)

    def action_open(self):
        self.ensure_one()
        model, domain, xmlid = self._get_tile()
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action.update({
            "name": self.name,
            "domain": domain,
            "context": {},
            "target": "current",
        })
        return action
