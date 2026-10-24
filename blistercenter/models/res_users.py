from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    bc_shop_catalog_allowed = fields.Boolean(
        "Darf Shop-Artikel lesen", compute="_compute_bc_shop_catalog_allowed",
        help="Technisch: Grundlage der Zugriffsregel auf Artikel (ir.access) für Portal- und Website-Besucher.",
    )

    @api.model
    def _bc_invalidate_shop_access(self):
        """Freigaben geändert: Zugriffsregel der Artikel (ir.access, je Benutzer gecacht) neu auswerten."""
        self.env["res.users"].invalidate_model(["bc_shop_catalog_allowed"])
        self.env.transaction.invalidate_ormcache()
        self.env.transaction.invalidate_access_cache()

    def _bc_shop_access_ok(self):
        """Darf der Benutzer den geschützten Blistercenter-Shop sehen und dort bestellen?

        Interne Benutzer (PLCS) sehen den Shop zur Pflege; ob sie bestellen können, entscheiden die
        Regeln am Auftrag. Portalbenutzer brauchen beides: das Blistercenter ist für den Shop
        freigeschaltet, und ihr Kontakt hat den Shop-Zugang (Benutzer). Besucher ohne Anmeldung nie.
        """
        self.ensure_one()
        if self._is_public() or not self.active:
            return False
        if not self.share:
            return True
        # Lesen der eigenen Freigabe-Kennzeichen; Portalbenutzer dürfen ihre Firma nicht immer lesen
        partner = self.sudo().partner_id
        customer = partner.commercial_partner_id
        return bool(customer.bc_is_customer and customer.bc_shop_enabled and partner.bc_shop_access)

    @api.depends_context("uid")
    def _compute_bc_shop_catalog_allowed(self):
        restricted = self.env["website"].sudo()._bc_restricted_company_ids()
        for user in self:
            if not user.share or user._bc_shop_access_ok():
                user.bc_shop_catalog_allowed = True
            else:
                # Ohne geschützten Shop in den Firmen des Benutzers gilt die Standard-Sichtbarkeit von Odoo
                user.bc_shop_catalog_allowed = not (set(user.sudo().company_ids.ids) & restricted)
