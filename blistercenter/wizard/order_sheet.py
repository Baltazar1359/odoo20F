from odoo import api, fields, models
from odoo.exceptions import UserError

from ..models.sale_order import CHANNELS_WITH_ORDERER


class BlistercenterOrderSheet(models.TransientModel):
    """Telefonbestellung als Bestellschein: Kunde, Besteller, Mengen je Artikel (1.4.0)."""

    _name = "blistercenter.order.sheet"
    _description = "Bestellschein Blistercenter"

    partner_id = fields.Many2one(
        "res.partner", "Kunde", required=True,
        domain="[('bc_is_customer', '=', True)]")
    orderer_id = fields.Many2one(
        "res.partner", "Besteller (Kontakt)",
        domain="[('parent_id', '=', partner_id)]")
    orderer_name = fields.Char("Besteller (Name)")
    client_order_ref = fields.Char("Bestellnummer des Kunden")
    note = fields.Text("Bemerkung")
    channel = fields.Selection(
        [("phone", "Telefon"), ("email", "E-Mail"), ("fax", "Fax")], string="Bestellweg",
        default="phone", required=True)
    discount = fields.Selection(related="partner_id.bc_discount", string="Rabatt auf HAP")
    payment_block = fields.Boolean(related="partner_id.bc_payment_block")
    payment_block_reason = fields.Char(related="partner_id.bc_payment_block_reason")
    line_ids = fields.One2many("blistercenter.order.sheet.line", "sheet_id", "Artikel")
    total = fields.Float("Summe netto", compute="_compute_total", digits="Product Price")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "line_ids" in fields_list and not res.get("line_ids"):
            products = self.env["sale.order"]._bc_sheet_products()
            res["line_ids"] = [(0, 0, {"product_id": p.id, "qty": 0}) for p in products]
        return res

    @api.depends("line_ids.qty", "line_ids.price_customer")
    def _compute_total(self):
        for sheet in self:
            sheet.total = sum(line.qty * line.price_customer for line in sheet.line_ids)

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.orderer_id and self.orderer_id.parent_id != self.partner_id:
            self.orderer_id = False

    def action_create_order(self):
        self.ensure_one()
        config = self.env["blistercenter.config"]._get(self.env.company)
        if config.rule_require_channel and self.channel in CHANNELS_WITH_ORDERER \
                and not (self.orderer_id or (self.orderer_name or "").strip()):
            raise UserError(self.env._("Bitte angeben, wer bestellt hat (Besteller als Kontakt oder Name)."))
        order = self.env["sale.order"]._bc_create_from_sheet(
            self.partner_id, [(line.product_id, line.qty) for line in self.line_ids],
            self.channel, orderer=self.orderer_id, orderer_name=self.orderer_name,
            client_ref=self.client_order_ref, note=self.note)
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": order.id,
            "view_mode": "form",
            "views": [(self.env.ref("sale.view_order_form").id, "form")],
            "target": "current",
        }


class BlistercenterOrderSheetLine(models.TransientModel):
    _name = "blistercenter.order.sheet.line"
    _description = "Bestellschein-Zeile"
    _order = "sequence, id"

    sheet_id = fields.Many2one("blistercenter.order.sheet", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product", "Artikel", required=True, readonly=True)
    default_code = fields.Char(related="product_id.default_code", string="PZN")
    product_name = fields.Char(related="product_id.name", string="Artikelname")
    price_avp = fields.Float(related="product_id.bc_avp", string="AVP", digits="Product Price")
    price_hap = fields.Float(related="product_id.lst_price", string="HAP", digits="Product Price")
    price_customer = fields.Float("Ihr Preis", compute="_compute_price_customer", digits="Product Price")
    qty = fields.Integer("Menge", default=0)

    @api.depends("product_id", "sheet_id.discount")
    def _compute_price_customer(self):
        for line in self:
            discount = float(line.sheet_id.discount or 0)
            line.price_customer = line.product_id.lst_price * (1 - discount / 100)
