"""Wareneingang per DataMatrix-Scan (Entscheidung 21.10.2026: alle Packungen haben DataMatrix).

Jeder Scan zählt eine Packung: Artikel (über GTIN/NTIN bzw. PZN), Charge und Verfall kommen aus dem Code.
Dieselbe Charge mehrfach gescannt erhöht nur die Menge; bei vielen gleichen Packungen genügt ein Scan und
die Menge wird eingetippt. „Übernehmen“ schreibt die Zeilen in den Wareneingang; gebucht wird wie gewohnt.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools import gs1


class BlistercenterReceiptScan(models.TransientModel):
    _name = "blistercenter.receipt.scan"
    _description = "Wareneingang scannen"

    picking_id = fields.Many2one("stock.picking", "Wareneingang", required=True, readonly=True)
    scan = fields.Char("Scan", help="DataMatrix-Code der Packung mit dem Handscanner lesen.")
    message = fields.Char("Meldung", readonly=True)
    message_type = fields.Selection(
        [("ok", "OK"), ("warning", "Hinweis"), ("error", "Fehler")], readonly=True)
    line_ids = fields.One2many("blistercenter.receipt.scan.line", "scan_id", "Gescannte Packungen")

    def _bc_products(self):
        return self.picking_id.move_ids.filtered(lambda m: m.state not in ("done", "cancel")).product_id

    def _bc_find_product(self, data):
        gtin, pzn = data.get("gtin"), data.get("pzn")
        for product in self._bc_products():
            codes = {c for c in (product.barcode, product.default_code,
                                 "plcs_pzn" in product._fields and product.plcs_pzn) if c}
            if gtin and (gtin in codes or gtin.lstrip("0") in {c.lstrip("0") for c in codes}):
                return product
            if pzn and pzn in codes:
                return product
        return self.env["product.product"]

    @api.onchange("scan")
    def _onchange_scan(self):
        raw = self.scan
        if not raw:
            return
        self.scan = False
        try:
            data = gs1.parse(raw)
        except gs1.Gs1Error as exc:
            self.message, self.message_type = str(exc), "error"
            return
        if not (data.get("batch") and data.get("expiry")):
            self.message = self.env._("Im Code fehlen Charge oder Verfall. Bitte Packung prüfen oder von Hand erfassen.")
            self.message_type = "error"
            return
        product = self._bc_find_product(data)
        if not product:
            self.message = self.env._(
                "PZN %(pzn)s gehört nicht zu diesem Wareneingang.", pzn=data.get("pzn") or data.get("gtin") or "-")
            self.message_type = "error"
            return
        line = self.line_ids.filtered(lambda l: l.product_id == product and l.lot_name == data["batch"])[:1]
        if line:
            line.qty += 1
            if line.expiration_date != data["expiry"]:
                line.expiration_date = data["expiry"]
        else:
            self.line_ids |= self.env["blistercenter.receipt.scan.line"].new({
                "product_id": product.id, "lot_name": data["batch"],
                "expiration_date": data["expiry"], "qty": 1,
            })
            line = self.line_ids.filtered(lambda l: l.product_id == product and l.lot_name == data["batch"])[:1]
        self.message = self.env._(
            "%(product)s · Charge %(lot)s · Verfall %(exp)s · Menge %(qty)s",
            product=product.display_name, lot=data["batch"], exp=data["expiry"].strftime("%d.%m.%Y"),
            qty="%g" % line.qty)
        self.message_type = "ok"
        if data.get("heuristic"):
            self.message += " " + self.env._("(Code ohne Trennzeichen gelesen – Charge bitte prüfen.)")
            self.message_type = "warning"
        elif line.warning:
            self.message_type = "warning"

    def action_apply(self):
        self.ensure_one()
        picking = self.picking_id
        if picking.bc_kind != "receipt" or picking.state in ("done", "cancel"):
            raise UserError(self.env._("Der Wareneingang ist nicht mehr offen."))
        lines = self.line_ids.filtered(lambda l: l.qty > 0)
        if not lines:
            raise UserError(self.env._("Noch keine Packung gescannt."))
        MoveLine = self.env["stock.move.line"]
        Lot = self.env["stock.lot"]
        for product in lines.product_id:
            move = picking.move_ids.filtered(
                lambda m: m.product_id == product and m.state not in ("done", "cancel"))[:1]
            if not move:
                raise UserError(self.env._("%(product)s gehört nicht zu diesem Wareneingang.",
                                           product=product.display_name))
            # Vorbelegte Zeilen ohne Charge ersetzen; schon erfasste Chargen bleiben
            move.move_line_ids.filtered(lambda ml: not ml.lot_id and not ml.lot_name).unlink()
            for line in lines.filtered(lambda l: l.product_id == product):
                if not (line.lot_name or "").strip() or not line.expiration_date:
                    raise UserError(self.env._("%(product)s: Charge und Verfall sind Pflicht.",
                                               product=product.display_name))
                vals = {
                    "move_id": move.id, "picking_id": picking.id, "product_id": product.id,
                    "uom_id": move.uom_id.id, "location_id": move.location_id.id,
                    "location_dest_id": move.location_dest_id.id, "quantity": line.qty, "picked": True,
                }
                lot = Lot.search([("product_id", "=", product.id), ("name", "=", line.lot_name.strip()),
                                  ("company_id", "in", [picking.company_id.id, False])], limit=1)
                if lot:
                    vals["lot_id"] = lot.id
                else:
                    vals.update({"lot_name": line.lot_name.strip(),
                                 "expiration_date": fields.Datetime.to_datetime(line.expiration_date)})
                existing = move.move_line_ids.filtered(
                    lambda ml: (ml.lot_id.name or ml.lot_name) == line.lot_name.strip())[:1]
                if existing:
                    existing.quantity += line.qty
                else:
                    MoveLine.create(vals)
        picking.message_post(body=self.env._(
            "Gescannt: %(count)s Packungen in %(lots)s Charge(n).",
            count="%g" % sum(lines.mapped("qty")), lots=len(lines)))
        return {"type": "ir.actions.act_window_close"}


class BlistercenterReceiptScanLine(models.TransientModel):
    _name = "blistercenter.receipt.scan.line"
    _description = "Gescannte Packungen"

    scan_id = fields.Many2one("blistercenter.receipt.scan", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", "Artikel", required=True)
    lot_name = fields.Char("Charge", required=True)
    expiration_date = fields.Date("Verfall", required=True)
    qty = fields.Float("Menge", default=1, digits="Product Unit")
    warning = fields.Char("Hinweis", compute="_compute_warning")

    @api.depends("product_id", "lot_name", "expiration_date")
    def _compute_warning(self):
        Lot = self.env["stock.lot"].sudo()
        for line in self:
            lot = line.product_id and line.lot_name and Lot.search([
                ("product_id", "=", line.product_id.id), ("name", "=", line.lot_name.strip())], limit=1)
            notes = []
            if lot and lot.bc_blocked:
                notes.append(self.env._("Charge gesperrt"))
            if lot and lot.expiration_date and line.expiration_date \
                    and lot.expiration_date.date() != line.expiration_date:
                notes.append(self.env._("bekannte Charge hat Verfall %(date)s",
                                        date=lot.expiration_date.strftime("%d.%m.%Y")))
            line.warning = ", ".join(notes) or False
