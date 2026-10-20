from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    bc_shop_product = fields.Boolean(
        "Im Blistercenter-Shop", copy=False, tracking=True,
        help="Nur so gekennzeichnete Artikel erscheinen im Shop und lassen sich dort bestellen "
             "(Regel „Shop zeigt nur Blistercenter-Artikel“ in den Blistercenter-Einstellungen). "
             "Telefonbestellungen sind davon nicht betroffen.")

    def _bc_shop_allowed(self, website=None):
        """Darf der Artikel im Shop erscheinen und bestellt werden? (Regel R40)"""
        self.ensure_one()
        website = website or self.env.website
        if not website._bc_shop_restricted():
            return True
        return bool(self.sudo().bc_shop_product)


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _is_add_to_cart_allowed(self):
        allowed = super()._is_add_to_cart_allowed()
        return allowed and self.product_tmpl_id._bc_shop_allowed()
