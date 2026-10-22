from odoo import api, fields, models


class StockQuant(models.Model):
    _inherit = "stock.quant"

    # Bei GreenRX steht die Seriennummer im Los (lot_id), die Charge im Feld plcs_batch des Loses.
    # Andere Ware (z. B. Blistercenter): das Los IST die Charge (1.10.1, sonst leere Spalte „Charge“).
    plcs_batch = fields.Char("Charge", compute="_compute_plcs_batch", store=True, index=True)
    plcs_grx_ampel = fields.Selection(related="lot_id.plcs_grx_box_id.ampel", string="Ampel")


    def action_plcs_grx_open_document(self):
        """Aus dem Bestand direkt zum gültigen Formblatt der Packung."""
        self.ensure_one()
        return self.lot_id.action_plcs_grx_open_document()

    @api.depends("lot_id.plcs_batch", "lot_id.plcs_is_grx_serial", "lot_id.name")
    def _compute_plcs_batch(self):
        for quant in self:
            lot = quant.lot_id
            quant.plcs_batch = lot.plcs_batch if lot.plcs_is_grx_serial else (lot.name or False)
