from odoo import fields, models
from odoo.exceptions import UserError

# Felder, die die Identität einer GreenRX-Packung ausmachen (Code-Review R08)
GRX_LOT_LOCKED_FIELDS = ("name", "product_id", "company_id", "plcs_batch", "plcs_is_grx_serial",
                         "plcs_grx_box_id", "plcs_grx_manual", "expiration_date")


class StockLot(models.Model):
    _inherit = "stock.lot"

    plcs_is_grx_serial = fields.Boolean(
        "GreenRX-Seriennummer", readonly=True, index=True,
        help="Dieser Datensatz steht für eine einzelne Packung (securPharm-Seriennummer). "
             "Die Charge steht im Feld Charge.")
    plcs_batch = fields.Char("Charge", readonly=True, index=True)
    plcs_grx_manual = fields.Boolean(
        "Von Hand eingegeben", readonly=True,
        help="Seriennummer, Charge und Verfall wurden beim GreenRX-Eingang getippt (Code nicht lesbar).")
    plcs_grx_box_id = fields.Many2one("plcs.greenrx.box", "Box-Erfassung", readonly=True, index="btree_not_null")
    plcs_grx_ticket_id = fields.Many2one(
        related="plcs_grx_box_id.ticket_id", string="GreenRX-Ticket", store=True)
    plcs_grx_archived = fields.Boolean(related="plcs_grx_box_id.archived", string="Belegung archiviert")
    plcs_grx_ampel = fields.Selection(related="plcs_grx_box_id.ampel", string="Ampel")
    plcs_grx_document_id = fields.Many2one(
        related="plcs_grx_box_id.valid_document_id", string="Formblatt", groups="plcs_greenrx.group_grx_user",
        help="Gültiges Formblatt (PDF) der Belegung, in der die Packung erfasst wurde.")
    plcs_grx_whereabouts = fields.Char(
        "Verbleib", compute="_compute_plcs_grx_whereabouts",
        help="Wo die Packung jetzt liegt bzw. wohin sie zuletzt gegangen ist (Lieferung, Vernichtung, …).")

    def _compute_plcs_grx_whereabouts(self):
        Quant = self.env["stock.quant"].sudo()
        MoveLine = self.env["stock.move.line"].sudo()
        for lot in self:
            if not lot.plcs_is_grx_serial or not lot.id:
                lot.plcs_grx_whereabouts = False
                continue
            quant = Quant.search([("lot_id", "=", lot.id), ("location_id.usage", "=", "internal"),
                                  ("quantity", ">", 0)], limit=1)
            if quant:
                lot.plcs_grx_whereabouts = self.env._("Im Lager: %(loc)s%(box)s", loc=quant.location_id.display_name,
                                                      box=(" · Box %s" % quant.package_id.name) if quant.package_id else "")
                continue
            line = MoveLine.search([("lot_id", "=", lot.id), ("state", "=", "done")],
                                   order="date desc, id desc", limit=1)
            lot.plcs_grx_whereabouts = lot._plcs_grx_move_text(line) if line else False

    def _plcs_grx_move_text(self, line):
        """Bewegung einer Packung in Worten: Art, Beleg, Ziel, Partner, Datum."""
        line = line.sudo()
        dest = line.location_dest_id
        move = line.move_id
        if move.is_scrap:
            kind = self.env._("Ausschuss/Vernichtung")
        elif line.is_inventory:
            kind = self.env._("Bestandskorrektur")
        elif dest.usage == "customer":
            kind = self.env._("Ausgeliefert")
        elif dest.usage == "supplier":
            kind = self.env._("Rückgabe an Lieferant")
        elif dest.usage == "inventory":
            kind = self.env._("Ausgebucht")
        elif dest.usage == "internal":
            kind = self.env._("Umlagerung")
        else:
            kind = self.env._("Abgang")
        ref = line.picking_id.name or move.reference or line.reference or ""
        partner = line.picking_id.partner_id.display_name or ""
        date = fields.Datetime.context_timestamp(self, line.date).strftime("%d.%m.%Y") if line.date else ""
        parts = [kind, ref, "→ %s" % dest.display_name, partner, date]
        return " · ".join(p for p in parts if p)

    def action_plcs_grx_open_document(self):
        """Von der Packung direkt zum gültigen Formblatt."""
        self.ensure_one()
        if not self.plcs_grx_box_id:
            raise UserError(self.env._("%s ist keine GreenRX-Packung.", self.name))
        return self.plcs_grx_box_id.action_grx_open_valid_document()

    def action_plcs_grx_open_box(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "plcs.greenrx.box", "res_id": self.plcs_grx_box_id.id,
                "views": [(False, "form")], "target": "current"}

    def write(self, vals):
        """Seriennummer, Artikel, Charge und Verfall einer GreenRX-Packung sind nach dem Abschluss fest.

        Sonst nennt das Lager eine andere Seriennummer als Scan-Protokoll und Formblatt.
        Korrekturen nur über Superuser-Rechte (Systemverwaltung, Migration).
        """
        locked = [f for f in GRX_LOT_LOCKED_FIELDS if f in vals]
        if locked and not self.env.su:
            for lot in self.filtered("plcs_is_grx_serial"):
                for fname in locked:
                    field = lot._fields[fname]
                    new = field.convert_to_record(field.convert_to_cache(vals[fname], lot), lot)
                    if new != lot[fname]:
                        raise UserError(self.env._(
                            "Die GreenRX-Packung %(sn)s (%(product)s) ist erfasst. Feld „%(field)s“ kann "
                            "nicht geändert werden.", sn=lot.name, product=lot.product_id.display_name,
                            field=lot._fields[fname].string))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered("plcs_is_grx_serial"):
            raise UserError(self.env._("Erfasste GreenRX-Packungen können nicht gelöscht werden."))
        return super().unlink()
