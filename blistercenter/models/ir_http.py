import werkzeug.exceptions

from odoo import models
from odoo.http import request

# Routen des Shops (website_sale und Erweiterungen): Katalog, Artikel, Preise, Warenkorb, Kasse, Merkliste,
# Vergleich, Varianten- und Produktkonfigurator, Nachbestellen aus dem Portal
SHOP_ROUTE_PREFIXES = (
    "/shop",
    "/website_sale",
    "/my/orders/reorder",
    "/sale/create_product_variant",
)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _pre_dispatch(cls, rule, args):
        # Vor dem Standard prüfen: der Standard prüft schon den Lesezugriff auf Artikel aus der URL
        cls._bc_check_shop_route(rule)
        super()._pre_dispatch(rule, args)

    @classmethod
    def _bc_check_shop_route(cls, rule):
        """Geschützter Blistercenter-Shop: jede Shop-Route serverseitig prüfen.

        Ausblenden von Menü und Seiten reicht nicht. Besucher ohne Anmeldung landen auf der
        Anmeldung, angemeldete Benutzer ohne Freigabe bekommen 403 – auch bei direkten URLs und
        JSON-Aufrufen (Warenkorb, Preise, Varianten).
        """
        if not rule.rule.startswith(SHOP_ROUTE_PREFIXES):
            return
        # Routen ohne website=True tragen die Website als host_id im Kontext
        website = request.env.website or request.env["website"].browse(request.env.context.get("host_id"))
        if not website or not website._bc_shop_restricted():
            return
        user = request.env.user
        if user._bc_shop_access_ok():
            return
        if user._is_public() and rule.endpoint.routing.get("type") == "http" \
                and request.httprequest.method in ("GET", "HEAD"):
            werkzeug.exceptions.abort(request.redirect_query(
                "/web/login", {"redirect": request.httprequest.full_path.rstrip("?")}))
        raise werkzeug.exceptions.Forbidden()
