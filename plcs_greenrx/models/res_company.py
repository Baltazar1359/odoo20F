from odoo import api, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    plcs_grx_min_shelf_months = fields.Integer(
        "GreenRX: Mindest-Restlaufzeit (Monate)", default=3,
        help="Packungen mit kürzerer Restlaufzeit werden in der Vorprüfung rot vorgeschlagen.")
    plcs_grx_allow_heuristic = fields.Boolean(
        "GreenRX: Codes ohne Trennzeichen zulassen", default=False,
        help="Wenn der Scanner keine GS-Trennzeichen sendet, versucht Odoo den Code zu trennen. "
             "Aus Sicherheitsgründen standardmäßig aus.")
    plcs_grx_create_from_slip = fields.Boolean(
        "GreenRX: Vorgang aus Retourenschein anlegen", default=True,
        help="Ist der Vorgang (Referenznummer) beim Scan des Retourenschein-QR-Codes noch nicht in Odoo, "
             "wird er mit den Daten aus dem QR-Code angelegt.")
    plcs_grx_box_prefix = fields.Char("GreenRX: Box-Präfix", default="GX")
    plcs_grx_box_max = fields.Integer("GreenRX: Höchste Box-Nummer", default=200)
    plcs_grx_quarantine_location_id = fields.Many2one("stock.location", "GreenRX: Quarantäne")
    plcs_grx_stock_location_id = fields.Many2one("stock.location", "GreenRX: Bestand (verkaufsfähig)")
    plcs_grx_blocked_location_id = fields.Many2one("stock.location", "GreenRX: Sperrlager")
    plcs_grx_receipt_type_id = fields.Many2one("stock.picking.type", "GreenRX: Eingang")
    plcs_grx_release_type_id = fields.Many2one("stock.picking.type", "GreenRX: Freigabe (VP)")
    plcs_grx_block_type_id = fields.Many2one("stock.picking.type", "GreenRX: Sperrung (VP)")

    @api.model
    def _plcs_grx_setup_all(self):
        for company in self.search([]):
            company._plcs_grx_setup()

    def _plcs_grx_setup(self):
        """Lagerorte, Vorgangstypen und Boxen für GreenRX anlegen (idempotent)."""
        self.ensure_one()
        warehouse = self.env["stock.warehouse"].search([("company_id", "=", self.id)], limit=1)
        if not warehouse:
            return False
        Location = self.env["stock.location"].with_company(self)

        def location(name, parent, usage="internal"):
            loc = Location.search([("name", "=", name), ("location_id", "=", parent.id)], limit=1)
            if not loc:
                loc = Location.create({"name": name, "location_id": parent.id, "usage": usage,
                                       "company_id": self.id})
            return loc

        root = location("GreenRX", warehouse.view_location_id, usage="view")
        quarantine = self.plcs_grx_quarantine_location_id or location("Quarantäne", root)
        stock = self.plcs_grx_stock_location_id or location("Bestand", root)
        blocked = self.plcs_grx_blocked_location_id or location("Sperrlager", root)
        customers = self.env.ref("stock.stock_location_customers")
        PickingType = self.env["stock.picking.type"].with_company(self)

        def picking_type(code, name, kind, src, dest, **extra):
            ptype = PickingType.search([("sequence_code", "=", code), ("company_id", "=", self.id)], limit=1)
            if not ptype:
                vals = {"name": name, "sequence_code": code, "code": kind, "warehouse_id": warehouse.id,
                        "company_id": self.id, "default_location_src_id": src.id,
                        "default_location_dest_id": dest.id, "create_backorder": "never"}
                vals.update(extra)
                ptype = PickingType.create(vals)
            return ptype

        receipt = self.plcs_grx_receipt_type_id or picking_type(
            "GRXIN", "GreenRX-Eingang", "incoming", customers, quarantine,
            use_create_lots=True, use_existing_lots=False)
        release = self.plcs_grx_release_type_id or picking_type(
            "GRXFRG", "GreenRX: Freigabe (VP)", "internal", quarantine, stock, show_entire_packs=True)
        block = self.plcs_grx_block_type_id or picking_type(
            "GRXSPR", "GreenRX: Sperrung (VP)", "internal", quarantine, blocked, show_entire_packs=True)
        self.write({
            "plcs_grx_quarantine_location_id": quarantine.id,
            "plcs_grx_stock_location_id": stock.id,
            "plcs_grx_blocked_location_id": blocked.id,
            "plcs_grx_receipt_type_id": receipt.id,
            "plcs_grx_release_type_id": release.id,
            "plcs_grx_block_type_id": block.id,
        })
        self.env["stock.package"].plcs_grx_ensure_boxes(self)
        return True

    def _plcs_grx_config(self):
        """Konfiguration prüfen und zurückgeben."""
        self.ensure_one()
        missing = [
            label for fname, label in (
                ("plcs_grx_quarantine_location_id", "Quarantäne"),
                ("plcs_grx_stock_location_id", "Bestand"),
                ("plcs_grx_blocked_location_id", "Sperrlager"),
                ("plcs_grx_receipt_type_id", "Vorgangstyp Eingang"),
                ("plcs_grx_release_type_id", "Vorgangstyp Freigabe"),
                ("plcs_grx_block_type_id", "Vorgangstyp Sperrung"),
            ) if not self[fname]
        ]
        if missing:
            raise UserError(self.env._(
                "GreenRX ist für die Firma %(company)s nicht vollständig eingerichtet. Es fehlt: %(missing)s. "
                "Bitte in Lager > Konfiguration > Einstellungen > GreenRX ergänzen.",
                company=self.name, missing=", ".join(missing)))
        return self
