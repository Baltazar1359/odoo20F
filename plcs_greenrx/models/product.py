import re

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

    @api.model
    def _plcs_ensure_from_ativ(self, pzn=False, gtin=False, name=False, storage=False):
        """Artikel zur ATIV-Position finden oder – wenn es ihn nicht gibt – anlegen.

        Angelegt wird nur mit gültiger PZN und Bezeichnung: Charge und Verfall Pflicht,
        Lagertemperatur aus den ATIV-Lagerbedingungen („Kühlware 2 - 8 °C“ → kühl).
        Gibt einen Artikel zurück oder ein leeres Recordset (z. B. bei mehrdeutiger PZN).
        """
        pzn = (pzn or "").strip()
        if pzn.isdigit():
            pzn = pzn.zfill(8)
        gtin = (gtin or "").strip() or gs1.pzn_to_gtin(pzn)
        found = self._plcs_find_by_code(gtin=gtin, pzn=pzn or False)
        if found:
            return found if len(found) == 1 else self.browse()
        # Prüfbericht 19.10. R11: gleiche PZN-Prüfung wie bei Scan und Handeingabe
        if not (gs1._pzn_check_ok(pzn) and gtin and gs1.gtin_check_digit_ok(gtin) and (name or "").strip()):
            return self.browse()
        cool = bool(re.search(r"k(ü|ue)hl|2\s*[-–]\s*8", storage or "", re.IGNORECASE))
        return self.sudo().create({
            "name": name.strip(),
            "plcs_pzn": pzn,
            "default_code": pzn,
            "barcode": gtin,
            "is_storable": True,
            "tracking": "lot",
            "use_expiration_date": True,
            "plcs_storage_temp": "cool" if cool else "ambient",
            "description": self.env._("Aus ATIV-Gutschrift angelegt (Lagerbedingungen: %s)", storage or "-"),
        })
