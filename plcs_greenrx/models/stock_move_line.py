from odoo import fields, models
from odoo.exceptions import UserError


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    plcs_batch = fields.Char("Charge", related="lot_id.plcs_batch")

    def _action_done(self):
        if not self.env.context.get("plcs_grx_controlled_move"):
            self._plcs_grx_check_controlled_locations()
        return super()._action_done()

    def _plcs_grx_check_controlled_locations(self):
        """Ware darf GreenRX-Quarantäne und -Sperrlager nur über die VP-Freigabe verlassen.

        Gilt für alle Wege (Transfers, Barcode-App, Inventur, Ausschuss), damit die
        Freigabe nicht umgangen werden kann.
        """
        for company in self.company_id:
            protected = company.plcs_grx_quarantine_location_id | company.plcs_grx_blocked_location_id
            if not protected:
                continue
            for line in self.filtered(lambda ml: ml.company_id == company and ml.quantity > 0):
                src = line.location_id
                src_root = protected.filtered(lambda loc: src.parent_path.startswith(loc.parent_path))
                if not src_root:
                    continue
                dest = line.location_dest_id
                if any(dest.parent_path.startswith(loc.parent_path) for loc in protected):
                    continue
                raise UserError(self.env._(
                    "%(product)s (%(lot)s) liegt in %(src)s und darf von dort nur über die "
                    "GreenRX-Freigabe der VP bewegt werden.",
                    product=line.product_id.display_name,
                    lot=line.lot_id.name or "-",
                    src=src_root[0].display_name))
