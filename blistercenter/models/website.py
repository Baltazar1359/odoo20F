from odoo import api, fields, models
from odoo.fields import Domain


class Website(models.Model):
    _inherit = "website"

    bc_shop_restricted = fields.Boolean(
        "Blistercenter-Shop (nur freigegebene Benutzer)", default=True,
        help="Artikel, Preise, Warenkorb und Bestellung dieses Shops nur für Portalbenutzer freigeschalteter "
             "Blistercenter mit Shop-Zugang. Gilt, solange in den Blistercenter-Einstellungen der Firma "
             "„Shop nur für freigeschaltete Blistercenter“ aktiv ist.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        websites = super().create(vals_list)
        self.env["res.users"]._bc_invalidate_shop_access()
        return websites

    def write(self, vals):
        res = super().write(vals)
        if "bc_shop_restricted" in vals or "company_id" in vals:
            # Zugriffsregel der Artikel hängt davon ab (ir.access-Cache je Benutzer)
            self.env["res.users"]._bc_invalidate_shop_access()
        return res

    def _bc_shop_assortment_only(self):
        """Regel R40: Shop zeigt nur Artikel mit „Im Blistercenter-Shop“ (Einstellung je Firma)."""
        website = self[:1] or self.env.website
        company = website.company_id or self.env.company
        return bool(self.env["blistercenter.config"]._get(company).rule_shop_only_bc_products)

    def _product_domain(self):
        domain = super()._product_domain()
        if self._bc_shop_assortment_only():
            domain = Domain.AND([domain, [("bc_shop_product", "=", True)]])
        return domain

    def _bc_shop_restricted(self):
        """Regel R72: Gilt für diesen Shop die Freigabe nur für Blistercenter? (Website-Schalter und Firmenregel)"""
        self.ensure_one()
        if not self.bc_shop_restricted:
            return False
        return self.env["blistercenter.config"]._get_readonly(self.company_id).rule_shop_requires_enable

    def _bc_restricted_company_ids(self):
        """Firmen mit mindestens einem geschützten Blistercenter-Shop."""
        websites = self.sudo().search([("bc_shop_restricted", "=", True)])
        return {website.company_id.id for website in websites if website._bc_shop_restricted()}

    def _enumerate_pages(self, query_string=None, force=False, ignore_custom_homepage=False):
        """Sitemap und Linkauswahl: Shop-Adressen eines geschützten Shops nicht öffentlich auflisten."""
        pages = super()._enumerate_pages(query_string=query_string, force=force,
                                         ignore_custom_homepage=ignore_custom_homepage)
        if not (len(self) == 1 and self._bc_shop_restricted() and not self.env.user._bc_shop_access_ok()):
            yield from pages
            return
        for page in pages:
            if not str(page.get("loc") or "").startswith("/shop"):
                yield page

    def has_ecommerce_access(self):
        """Geschützter Blistercenter-Shop: Artikel, Preise und Warenkorb nur für freigegebene Benutzer.

        Odoo prüft diesen Wert in Shop, Artikelseite, Warenkorb, Menü, Kopfzeile, Suche und Produkt-Snippets.
        Die übrigen Shop-Schnittstellen sperrt ir.http (_bc_check_shop_route) serverseitig.
        """
        if not super().has_ecommerce_access():
            return False
        if len(self) == 1 and self._bc_shop_restricted():
            return self.env.user._bc_shop_access_ok()
        return True
