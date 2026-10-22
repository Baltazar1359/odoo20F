from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools import grx_auth


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    # GreenRX: Los = Seriennummer, Charge separat. Andere Ware: Los = Charge (1.10.1).
    plcs_batch = fields.Char("Charge", compute="_compute_plcs_batch")

    @api.depends("lot_id.plcs_batch", "lot_id.plcs_is_grx_serial", "lot_id.name", "lot_name")
    def _compute_plcs_batch(self):
        for line in self:
            lot = line.lot_id
            line.plcs_batch = lot.plcs_batch if lot.plcs_is_grx_serial else (lot.name or line.lot_name or False)

    def _action_done(self):
        self._plcs_grx_check_controlled_locations()
        boxes = self.lot_id.filtered("plcs_is_grx_serial").plcs_grx_box_id
        lines = self.browse(self.ids)
        res = super()._action_done()
        # Belegung archivieren, sobald ihre letzte Packung das Lager verlassen hat (1.9.0)
        if boxes:
            boxes.sudo()._grx_try_archive(lines.exists())
        return res

    def _plcs_grx_check_controlled_locations(self):
        """Ware darf GreenRX-Quarantäne und -Sperrlager nur über die VP-Freigabe verlassen.

        Gilt für alle Wege (Transfers, Barcode-App, Inventur, Ausschuss, direkte RPC-Aufrufe).
        Ausgenommen sind nur Zeilen, die zu einer Freigabe aus dem Modulcode gehören
        (grx_auth.ControlledMove): gleiche Box, nur die Seriennummern dieser Erfassung.
        Ein Kontextwert aus einem RPC-Aufruf reicht nicht (Code-Review R01).
        """
        auth = grx_auth.controlled_move(self.env)
        for company in self.company_id:
            protected = company.plcs_grx_quarantine_location_id | company.plcs_grx_blocked_location_id
            if not protected:
                continue
            for line in self.filtered(lambda ml: ml.company_id == company and ml.quantity > 0):
                src = line.location_id
                src_root = protected.filtered(lambda loc: src.parent_path.startswith(loc.parent_path))
                if not src_root:
                    continue
                if auth and line.lot_id.id in auth.lot_ids \
                        and line.picking_id.plcs_grx_release_box_id.id == auth.box_id:
                    continue
                dest = line.location_dest_id
                # Aus einer GreenRX-Box auspacken oder umpacken: nur über die VP-Entscheidung,
                # sonst passt der Boxinhalt nicht mehr zur Erfassung (Code-Review R02)
                repack = line.package_id.plcs_is_grx_box and line.result_package_id != line.package_id
                if not repack and any(dest.parent_path.startswith(loc.parent_path) for loc in protected):
                    continue
                if repack:
                    raise UserError(self.env._(
                        "%(product)s (%(lot)s) liegt in Box %(box)s (%(src)s). Die Box darf dort nicht "
                        "ausgepackt oder umgepackt werden; die VP entscheidet über den ganzen Box-Inhalt.",
                        product=line.product_id.display_name, lot=line.lot_id.name or "-",
                        box=line.package_id.name, src=src_root[0].display_name))
                raise UserError(self.env._(
                    "%(product)s (%(lot)s) liegt in %(src)s und darf von dort nur über die "
                    "GreenRX-Freigabe der VP bewegt werden.",
                    product=line.product_id.display_name,
                    lot=line.lot_id.name or "-",
                    src=src_root[0].display_name))
