"""Online-Bestellschein im Kundenportal (1.4.0).

Ein Blistercenter meldet sich an, trägt Mengen für die Artikel des Bestellscheins ein und
schickt ab. Der Auftrag entsteht als „Angebot gesendet“ und erscheint bei PLCS unter
„Neue Bestellungen“; PLCS bestätigt ihn (dabei greifen alle Regeln wie bei jeder Bestellung).
"""
import re
import uuid

from psycopg2 import IntegrityError

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal

from ..models.order_sheet import TOKEN_RE

QTY_KEY = re.compile(r"^qty_(\d+)$")


class BlistercenterPortal(CustomerPortal):

    def _show_report(self, model, report_type, report_ref, download=False):
        """Bestätigte Blistercenter-Bestellung: im Portal die Proforma-Rechnung statt des Auftrags (1.5.0)."""
        if (report_ref == "sale.action_report_saleorder" and model._name == "sale.order" and len(model) == 1
                and model.bc_is_blistercenter and model.state == "sale"):
            report_ref = "blistercenter.action_report_bc_proforma"
        return super()._show_report(model, report_type, report_ref, download=download)

    # ------------------------------------------------------------------
    # Zugang
    # ------------------------------------------------------------------
    def _bc_sheet_access(self):
        """(Kunde, Fehlermeldung). Nur Blistercenter mit Shop-Freischaltung und ohne Zahlungssperre."""
        env = request.env
        user = env.user
        if user._is_public():
            return None, env._("Bitte melden Sie sich an.")
        partner = user.partner_id.commercial_partner_id.sudo()
        company = request.website.company_id if getattr(request, "website", None) else env.company
        config = env["blistercenter.config"]._get(company)
        if not partner.bc_is_customer:
            return None, env._("Der Bestellschein ist nur für Blistercenter-Kunden der PLCS.")
        if config.rule_shop_requires_enable and not partner.bc_shop_enabled:
            return None, env._("Ihr Kundenkonto ist für Online-Bestellungen noch nicht freigeschaltet. "
                               "Bitte wenden Sie sich an PLCS.")
        # R72 (1.7.0): Artikel und Preise wie im geschützten Shop nur mit „Shop-Zugang (Benutzer)“
        website = getattr(request, "website", None)
        restricted = website._bc_shop_restricted() if website else config.rule_shop_requires_enable
        if restricted and not user._bc_shop_access_ok():
            return None, env._("Ihr Benutzer ist für Online-Bestellungen noch nicht freigegeben. "
                               "Bitte wenden Sie sich an PLCS.")
        if partner.bc_payment_block and config.rule_block_confirm:
            return None, env._("Für Ihr Kundenkonto ist eine Online-Bestellung derzeit nicht möglich. "
                               "Bitte wenden Sie sich an PLCS.")
        return partner, None

    @staticmethod
    def _bc_reuse_token(post):
        token = ((post or {}).get("bc_submit_token") or "").strip()
        return token if TOKEN_RE.match(token) else None

    def _bc_company(self):
        return request.website.company_id if getattr(request, "website", None) else request.env.company

    def _prepare_portal_counter_values(self, counter):
        if counter == "bc_sheet_count":
            partner, _error = self._bc_sheet_access()
            if partner:
                company = self._bc_company()
                return "product.product", [
                    ("bc_shop_product", "=", True), ("sale_ok", "=", True),
                    ("company_id", "in", [False, company.id])], "sudo"
            return False, False, False
        return super()._prepare_portal_counter_values(counter)

    # ------------------------------------------------------------------
    # Seiten
    # ------------------------------------------------------------------
    def _bc_sheet_values(self, partner, error=None, post=None):
        company = self._bc_company()
        products = request.env["sale.order"]._bc_sheet_products(company) if partner else request.env["product.product"]
        discount = float(partner.bc_discount or 0) if partner else 0.0
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "bc_order_sheet",
            "bc_partner": partner,
            "bc_error": error,
            "bc_products": products,
            "bc_discount": discount,
            "bc_currency": company.currency_id,
            "bc_post": post or {},
            # Neue Kennung je Anzeige; nach einem Fehler bleibt die abgeschickte Kennung (noch unverbraucht)
            "bc_submit_token": self._bc_reuse_token(post) or uuid.uuid4().hex,
        })
        return values

    @http.route("/my/bestellschein", type="http", auth="user", website=True, methods=["GET"], sitemap=False)
    def bc_order_sheet(self, **kw):
        partner, error = self._bc_sheet_access()
        return request.render("blistercenter.portal_order_sheet", self._bc_sheet_values(partner, error))

    @http.route("/my/bestellschein", type="http", auth="user", website=True, methods=["POST"], sitemap=False)
    def bc_order_sheet_submit(self, **post):
        partner, error = self._bc_sheet_access()
        if not partner:
            return request.render("blistercenter.portal_order_sheet", self._bc_sheet_values(None, error))
        env = request.env
        company = self._bc_company()
        # Ohne Kennung (Seite von vor 1.5.1 im Browser) wie bisher, nur ohne Schutz vor Wiederholung
        token = (post.get("bc_submit_token") or "").strip() or None
        done = env["sale.order"]._bc_find_submitted(partner, token)
        if done:
            # Wiederholtes Absenden (Doppelklick, Neuladen, Verbindungsabbruch): keine zweite Bestellung
            return request.redirect("/my/bestellschein/danke?ref=%s" % done.id)
        products = env["sale.order"]._bc_sheet_products(company)
        by_id = {p.id: p for p in products}
        quantities = []
        try:
            for key in post:
                match = QTY_KEY.match(key)
                if match and int(match.group(1)) not in by_id:
                    raise UserError(env._("Ein Artikel steht nicht (mehr) auf dem Bestellschein. Bitte Seite neu laden."))
            for product in products:
                raw = (post.get("qty_%s" % product.id) or "").strip()
                if not raw:
                    continue
                if not raw.isdigit():
                    raise UserError(env._("Menge für %s: bitte eine ganze Zahl eintragen.", product.display_name))
                quantities.append((product, int(raw)))
            user_partner = env.user.partner_id.sudo()
            orderer = user_partner if user_partner != partner and user_partner.commercial_partner_id == partner else None
            Order = env["sale.order"].sudo().with_company(company)
            with env.cr.savepoint():
                order = Order._bc_create_from_sheet(
                    partner, quantities, "portal", company=company,
                    orderer=orderer, orderer_name=None if orderer else env.user.name,
                    client_ref=post.get("client_order_ref"), note=post.get("note"), submit_token=token)
        except IntegrityError:
            # Gleichzeitig zweimal abgeschickt: die andere Anfrage hat die Bestellung schon angelegt
            return request.redirect("/my/orders")
        except UserError as exc:
            return request.render("blistercenter.portal_order_sheet",
                                  self._bc_sheet_values(partner, exc.args[0], post))
        order.action_quotation_sent()
        order.message_post(body=env._("Online-Bestellschein abgeschickt von %s.", env.user.name))
        config = env["blistercenter.config"]._get(company)
        if config.notify_new_order and config.sales_user_id:
            order.with_context(mail_activity_quick_update=True).activity_schedule(
                "mail.mail_activity_data_todo",
                summary=env._("Online-Bestellung prüfen und bestätigen"),
                user_id=config.sales_user_id.id,
            )
        return request.redirect("/my/bestellschein/danke?ref=%s" % order.id)

    @http.route("/my/bestellschein/danke", type="http", auth="user", website=True, sitemap=False)
    def bc_order_sheet_thanks(self, ref=None, **kw):
        partner, error = self._bc_sheet_access()
        order = request.env["sale.order"]
        if partner and ref and str(ref).isdigit():
            candidate = order.sudo().browse(int(ref)).exists()
            if candidate and candidate.partner_id.commercial_partner_id == partner:
                order = candidate
        values = self._bc_sheet_values(partner, error)
        values.update({"page_name": "bc_order_sheet", "bc_order": order})
        return request.render("blistercenter.portal_order_sheet_thanks", values)
