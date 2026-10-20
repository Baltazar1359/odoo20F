from werkzeug.exceptions import NotFound

from odoo.http import route

from odoo.addons.website_sale.controllers.main import WebsiteSale


class BlistercenterWebsiteSale(WebsiteSale):

    @route()
    def product(self, product, pricelist=None, **kwargs):
        # Regel R40: Artikel außerhalb des Blistercenter-Sortiments haben keine Produktseite im Shop
        if not product.sudo()._bc_shop_allowed(self.env.website):
            raise NotFound()
        return super().product(product, pricelist=pricelist, **kwargs)
