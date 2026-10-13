from odoo import fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    # Bei GreenRX steht die Seriennummer im Los (lot_id), die Charge im Feld plcs_batch des Loses.
    plcs_batch = fields.Char("Charge", related="lot_id.plcs_batch", store=True, index=True)
    plcs_grx_ampel = fields.Selection(related="lot_id.plcs_grx_box_id.ampel", string="Ampel")

