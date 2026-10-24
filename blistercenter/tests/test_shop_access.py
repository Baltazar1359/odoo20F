"""Geschützter Blistercenter-Shop (1.3.0): nur freigegebene Benutzer sehen Artikel und Preise und bestellen.

| Benutzer                              | Shop sehen | Bestellen |
|---------------------------------------|------------|-----------|
| nicht angemeldet                      | nein       | nein      |
| angemeldet, nicht freigegeben         | nein       | nein      |
| freigegebener Blistercenter-Benutzer  | ja         | ja        |
| gesperrter oder entzogener Benutzer   | nein       | nein      |
"""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.service.model import call_kw
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


class ShopAccessCommon:

    @classmethod
    def _setup_shop(cls):
        env = cls.env
        cls.company = env.company
        cls.config = env["blistercenter.config"]._get(cls.company)
        cls.config.rule_shop_requires_enable = True
        Partner = env["res.partner"].sudo()
        cls.customer = Partner.create({
            "name": "Blistercenter Shop GmbH", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "property_payment_term_id": env.ref("blistercenter.payment_term_14days").id,
        })
        cls.contact_ok = Partner.create({
            "name": "Frau Freigegeben", "parent_id": cls.customer.id, "type": "contact",
            "email": "frei@example.com", "bc_shop_access": True,
        })
        cls.contact_no = Partner.create({
            "name": "Herr Buchhaltung", "parent_id": cls.customer.id, "type": "contact", "email": "buha@example.com",
        })
        cls.other_customer = Partner.create({"name": "Anderes Blistercenter", "is_company": True, "bc_is_customer": True})

        def portal(login, partner=None):
            vals = {"partner_id": partner.id} if partner else {}
            return new_test_user(env, login=login, groups="base.group_portal",
                                 company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)], **vals)

        cls.portal_ok = portal("bc_portal_ok", cls.contact_ok)
        cls.portal_no = portal("bc_portal_no", cls.contact_no)
        cls.portal_foreign = portal("bc_portal_fremd")  # z. B. nach Selbstregistrierung
        cls.internal = new_test_user(env, login="bc_intern", groups="base.group_user",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.approver = new_test_user(env, login="bc_shop_freigabe", groups="blistercenter.group_bc_approver",
                                     company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.product = env["product.product"].create({
            "name": "Shop-Arzneimittel 20 St.", "type": "consu", "list_price": 42.0, "sale_ok": True,
            "is_published": True, "invoice_policy": "delivery",
            # 1.7.0: Geschützter Shop (R72) gilt zusätzlich zum Sortiment (R40)
            "bc_shop_product": True,
        })
        cls.website = env["website"].search([("company_id", "=", cls.company.id)], limit=1) \
            or env["website"].create({"name": "BC Shop", "company_id": cls.company.id})
        cls.website.bc_shop_restricted = True


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterShopAccess(ShopAccessCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shop()

    def test_access_matrix(self):
        public = self.website.user_id
        self.assertFalse(public._bc_shop_access_ok(), "nicht angemeldet")
        self.assertFalse(self.portal_no._bc_shop_access_ok(), "angemeldet, nicht freigegeben")
        self.assertFalse(self.portal_foreign._bc_shop_access_ok(), "fremder Portalbenutzer")
        self.assertTrue(self.portal_ok._bc_shop_access_ok(), "freigegebener Blistercenter-Benutzer")
        self.assertTrue(self.internal._bc_shop_access_ok(), "PLCS intern (Pflege)")
        for user, expected in ((public, False), (self.portal_no, False), (self.portal_ok, True)):
            self.assertEqual(self.website.with_user(user).has_ecommerce_access(), expected, user.login)
        # Entzug beim Kunden und beim Benutzer, Sperre des Zugangs
        self.customer.with_user(self.approver).write({"bc_shop_enabled": False})
        self.assertFalse(self.portal_ok._bc_shop_access_ok(), "Kunde entzogen")
        self.customer.with_user(self.approver).write({"bc_shop_enabled": True})
        self.contact_ok.with_user(self.approver).write({"bc_shop_access": False})
        self.assertFalse(self.portal_ok._bc_shop_access_ok(), "Benutzer entzogen")
        self.contact_ok.with_user(self.approver).write({"bc_shop_access": True})
        self.portal_ok.active = False
        self.assertFalse(self.portal_ok._bc_shop_access_ok(), "Zugang gesperrt")

    def test_unrestricted_website_keeps_standard(self):
        self.website.bc_shop_restricted = False
        self.assertTrue(self.website.with_user(self.portal_no).has_ecommerce_access())
        self.website.bc_shop_restricted = True
        self.config.rule_shop_requires_enable = False
        self.assertFalse(self.website._bc_shop_restricted(), "Firmenregel aus: Standard von Odoo")
        self.assertTrue(self.website.with_user(self.portal_no).has_ecommerce_access())

    def test_catalog_and_prices_not_readable_via_api(self):
        template = self.product.product_tmpl_id
        Template = self.env["product.template"]
        for user in (self.portal_no, self.portal_foreign, self.website.user_id):
            self.assertFalse(Template.with_user(user).search([("id", "=", template.id)]), user.login)
            with self.assertRaises(AccessError):
                template.with_user(user).read(["list_price"])
            with self.assertRaises(AccessError):
                self.product.with_user(user).read(["lst_price"])
            # wie ein JSON-RPC-Aufruf (search_read)
            self.assertEqual(call_kw(Template.with_user(user), "search_read",
                                     [[("id", "=", template.id)], ["list_price"]], {}), [])
        self.assertEqual(template.with_user(self.portal_ok).read(["list_price"])[0]["list_price"], 42.0)
        self.assertTrue(Template.with_user(self.internal).search([("id", "=", template.id)]))
        # Entzug wirkt sofort (Cache der Zugriffsregel)
        self.contact_ok.with_user(self.approver).write({"bc_shop_access": False})
        self.assertFalse(Template.with_user(self.portal_ok).search([("id", "=", template.id)]))
        # Ohne geschützten Shop gilt wieder die Standard-Sichtbarkeit
        self.config.rule_shop_requires_enable = False
        self.assertTrue(Template.with_user(self.portal_no).search([("id", "=", template.id)]))

    def test_registration_never_grants_shop_access(self):
        # Wie die freie Registrierung (b2c): Kopie des Portal-Vorlagebenutzers mit neuem Kontakt
        user = self.env["res.users"].sudo().with_context(no_reset_password=True)._create_user_from_template({
            "login": "neu@example.com", "name": "Blistercenter Shop GmbH", "password": "Neu-Passwort-1",
        })
        self.assertTrue(user.share)
        self.assertFalse(user.partner_id.bc_shop_access)
        self.assertNotEqual(user.partner_id.commercial_partner_id, self.customer)
        self.assertFalse(user._bc_shop_access_ok())
        with self.assertRaises(AccessError):
            user.partner_id.with_user(user).write({"bc_shop_access": True})
        with self.assertRaises(AccessError, msg="Nur die Rolle Freigabe"):
            self.contact_no.with_user(self.internal).write({"bc_shop_access": True})
        with self.assertRaises(AccessError, msg="Nachweis nicht fälschbar"):
            self.contact_ok.with_user(self.approver).write({"bc_shop_access_by": self.internal.id})
        self.contact_no.with_user(self.approver).write({"bc_shop_access": True})
        self.assertEqual(self.contact_no.bc_shop_access_by, self.approver)
        self.assertTrue(self.portal_no._bc_shop_access_ok())

    def _cart(self, partner):
        return self.env["sale.order"].sudo().create({
            "partner_id": partner.id, "website_id": self.website.id, "company_id": self.company.id,
        })

    def test_cart_server_side(self):
        order_no = self._cart(self.contact_no)
        with self.assertRaises(UserError):
            order_no.with_user(self.portal_no).sudo().with_context(skip_cart_verification=True)._cart_add(
                self.product.id, 1)
        # Fremder Benutzer kann nicht in den Warenkorb eines freigegebenen Kunden legen
        order_ok = self._cart(self.contact_ok)
        with self.assertRaises(UserError):
            order_ok.with_user(self.portal_foreign).sudo().with_context(skip_cart_verification=True)._cart_add(
                self.product.id, 1)
        order_ok.with_user(self.portal_ok).sudo().with_context(skip_cart_verification=True)._cart_add(
            self.product.id, 2)
        self.assertEqual(order_ok.order_line.product_uom_qty, 2)
        # Zahlungssperre: nicht bestellen
        self.customer.with_user(self.approver).write({"bc_payment_block": True, "bc_payment_block_reason": "Mahnung"})
        with self.assertRaises(UserError):
            order_ok.with_user(self.portal_ok).sudo().with_context(skip_cart_verification=True)._cart_add(
                self.product.id, 1)

    def test_company_separation(self):
        other_company = self.env["res.company"].create({"name": "ATIV (Test)"})
        other_website = self.env["website"].create({"name": "ATIV-Shop", "company_id": other_company.id})
        self.env["blistercenter.config"]._get(other_company).rule_shop_requires_enable = False
        self.assertTrue(self.website._bc_shop_restricted())
        self.assertFalse(other_website._bc_shop_restricted(), "Regeln gelten je Firma")
        # Portalbenutzer sehen nur Aufträge ihres eigenen Blistercenters
        own = self._cart(self.contact_ok)
        foreign = self._cart(self.other_customer)
        orders = self.env["sale.order"].with_user(self.portal_ok).search([("id", "in", (own | foreign).ids)])
        self.assertNotIn(foreign, orders)
        with self.assertRaises(AccessError):
            foreign.with_user(self.portal_ok).read(["amount_total"])

    def test_menu_hidden_for_unapproved(self):
        menu = self.env["website.menu"].create({"name": "Shop", "url": "/shop", "website_id": self.website.id,
                                                "parent_id": self.website.menu_id.id})
        self.assertFalse(menu.with_user(self.portal_no).is_visible)
        menu.invalidate_recordset(["is_visible"])
        self.assertTrue(menu.with_user(self.portal_ok).is_visible)


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterShopHttp(ShopAccessCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shop()

    def _json(self, route, params):
        return self.opener.post(self.base_url() + route, json={
            "id": 0, "jsonrpc": "2.0", "method": "call", "params": params})

    def assertJsonForbidden(self, route, params):
        """JSON-RPC meldet Fehler im Rumpf (HTTP 200): kein Ergebnis, Fehler „Forbidden“."""
        payload = self._json(route, params).json()
        self.assertNotIn("result", payload, route)
        self.assertIn("Forbidden", str(payload.get("error")), route)
        return payload

    def _cart_params(self):
        return {"product_template_id": self.product.product_tmpl_id.id, "product_id": self.product.id, "quantity": 1}

    def test_anonymous_redirected_to_login(self):
        for url in ("/shop", self.product.website_url, "/shop/cart", "/shop/checkout"):
            response = self.url_open(url, allow_redirects=False)
            self.assertIn(response.status_code, (302, 303), url)
            self.assertIn("/web/login", response.headers.get("Location", ""), url)
        self.assertJsonForbidden("/shop/cart/add", self._cart_params())
        payload = self.assertJsonForbidden("/website_sale/get_combination_info", {
            "product_template_id": self.product.product_tmpl_id.id, "product_id": self.product.id,
            "combination": [], "add_qty": 1})
        self.assertNotIn("42.0", str(payload))
        self.assertFalse(self.env["sale.order"].sudo().search([("order_line.product_id", "=", self.product.id)]))

    def test_homepage_and_sitemap_for_anonymous(self):
        response = self.url_open("/")
        self.assertEqual(response.status_code, 200, "Startseite bleibt erreichbar")
        sitemap = self.url_open("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertNotIn(b"/shop", sitemap.content)

    def test_unapproved_portal_user_forbidden(self):
        self.authenticate("bc_portal_no", "bc_portal_no")
        for url in ("/shop", self.product.website_url, "/shop/cart"):
            response = self.url_open(url, allow_redirects=False)
            self.assertEqual(response.status_code, 403, url)
            self.assertNotIn(b"Shop-Arzneimittel", response.content)
        self.assertJsonForbidden("/shop/cart/add", self._cart_params())
        self.assertEqual(self.url_open("/my", allow_redirects=False).status_code, 200, "Portal bleibt nutzbar")

    def test_approved_portal_user_can_browse_and_order(self):
        self.authenticate("bc_portal_ok", "bc_portal_ok")
        response = self.url_open("/shop")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Shop-Arzneimittel", response.content)
        self.assertEqual(self.url_open(self.product.website_url).status_code, 200)
        payload = self._json("/shop/cart/add", self._cart_params()).json()
        self.assertIn("result", payload, payload.get("error"))
        order = self.env["sale.order"].sudo().search([("partner_id", "=", self.contact_ok.id)], limit=1)
        self.assertEqual(order.order_line.product_id, self.product)
        # Entzug wirkt sofort, auch für einen laufenden Warenkorb
        self.contact_ok.with_user(self.approver).write({"bc_shop_access": False})
        self.assertEqual(self.url_open("/shop/cart", allow_redirects=False).status_code, 403)
        self.assertJsonForbidden("/shop/cart/add", self._cart_params())
