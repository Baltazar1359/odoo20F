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
