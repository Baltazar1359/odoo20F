from odoo import fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    # Bei GreenRX steht die Seriennummer im Los (lot_id), die Charge im Feld plcs_batch des Loses.
    plcs_batch = fields.Char("Charge", related="lot_id.plcs_batch", store=True, index=True)
    plcs_grx_ampel = fields.Selection(related="lot_id.plcs_grx_box_id.ampel", string="Ampel")


    def action_plcs_grx_open_document(self):
        """Aus dem Bestand direkt zum gültigen Formblatt der Packung."""
        self.ensure_one()
        return self.lot_id.action_plcs_grx_open_document()
