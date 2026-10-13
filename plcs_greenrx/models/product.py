from odoo import api, fields, models

from ..tools import gs1


class ProductTemplate(models.Model):
    _inherit = "product.template"

    plcs_pzn = fields.Char(
        "PZN", index=True, copy=False,
        help="Pharmazentralnummer (8-stellig). Wird beim Scan zum Finden des Artikels genutzt, "
             "zusätzlich zum Barcode (GTIN).")
    plcs_storage_temp = fields.Selection(
        [("ambient", "Ambient (15–25 °C)"), ("cool", "Kühl (2–8 °C)")],
        string="Lagertemperatur", default="ambient",
        help="Wird in der KabQ-Checkliste angekreuzt.")


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _plcs_find_by_code(self, gtin=False, pzn=False):
        """Artikel über GTIN (Barcode) oder PZN finden. Gibt höchstens einen Artikel zurück."""
        candidates = self.browse()
        barcodes = set()
        if gtin:
            barcodes.add(gtin)
            stripped = gtin.lstrip("0")
            # GTIN-13 als Barcode gepflegt (ohne führende 0)
            if len(gtin) == 14 and gtin.startswith("0"):
                barcodes.add(gtin[1:])
            if stripped and len(stripped) in (8, 12, 13):
                barcodes.add(stripped)
            if not pzn:
                pzn = gs1.gtin_to_pzn(gtin)
        if barcodes:
            candidates = self.search([("barcode", "in", list(barcodes))], limit=2)
        if not candidates and pzn:
            candidates = self.search([("plcs_pzn", "=", pzn)], limit=2)
            if not candidates:
                candidates = self.search([("default_code", "=", pzn)], limit=2)
        return candidates
