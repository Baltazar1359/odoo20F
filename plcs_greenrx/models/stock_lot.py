from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    plcs_is_grx_serial = fields.Boolean(
        "GreenRX-Seriennummer", readonly=True, index=True,
        help="Dieser Datensatz steht für eine einzelne Packung (securPharm-Seriennummer). "
             "Die Charge steht im Feld Charge.")
    plcs_batch = fields.Char("Charge", readonly=True, index=True)
    plcs_grx_box_id = fields.Many2one("plcs.greenrx.box", "Box-Erfassung", readonly=True, index="btree_not_null")
    plcs_grx_ticket_id = fields.Many2one(
        related="plcs_grx_box_id.ticket_id", string="GreenRX-Ticket", store=True)
