from odoo import models
from odoo.fields import Domain


class Website(models.Model):
    _inherit = "website"

    def _bc_shop_restricted(self):
        """Regel R40: Shop zeigt nur Artikel mit „Im Blistercenter-Shop“ (Einstellung je Firma)."""
        website = self[:1] or self.env.website
        company = website.company_id or self.env.company
        return bool(self.env["blistercenter.config"]._get(company).rule_shop_only_bc_products)

    def _product_domain(self):
        domain = super()._product_domain()
        if self._bc_shop_restricted():
            domain = Domain.AND([domain, [("bc_shop_product", "=", True)]])
        return domain
