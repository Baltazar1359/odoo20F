"""1.4.0: Bestellschein – Telefon (Assistent) und Online im Kundenportal, gleiche Prüfungen."""
import re

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import HttpCase, new_test_user, tagged

from .test_blistercenter import BlistercenterCommon


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterOrderSheet(BlistercenterCommon):
    """Telefonbestellung als Bestellschein: nur Sortiment, nur Mengen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other = cls.env["product.product"].create({
            "name": "Fremdartikel Bestellschein", "type": "consu", "is_storable": True, "tracking": "lot",
            "use_expiration_date": True, "invoice_policy": "delivery", "list_price": 5.0,
            "taxes_id": [Command.clear()],
            "seller_ids": [Command.create({"partner_id": cls.supplier.id, "price": 2.0})],
        })

    def _sheet(self, partner=None, user=None, **vals):
        Sheet = self.env["blistercenter.order.sheet"].with_user(user or self.user_bc)
        defaults = Sheet.default_get(list(Sheet._fields))
        defaults.update({"partner_id": (partner or self.customer_debit).id, **vals})
        return Sheet.create(defaults)

    def _set_qty(self, sheet, product, qty):
        sheet.line_ids.filtered(lambda line: line.product_id == product).qty = qty

    def test_sheet_lists_only_assortment(self):
        sheet = self._sheet()
        self.assertIn(self.product, sheet.line_ids.product_id)
        self.assertNotIn(self.other, sheet.line_ids.product_id)
        self.assertFalse(sheet.line_ids.filtered(lambda line: not line.product_id.bc_shop_product))
        self.assertTrue(all(line.qty == 0 for line in sheet.line_ids))

    def test_sheet_creates_order_with_discount(self):
        sheet = self._sheet(orderer_name="Herr Telefon", client_order_ref="BC-4711", note="Bitte vormittags")
        self._set_qty(sheet, self.product, 3)
        self.assertAlmostEqual(sheet.total, 3 * 20.0 * 0.98)  # 2 % Rabatt auf HAP
        action = sheet.action_create_order()
        order = self.env["sale.order"].browse(action["res_id"])
        self.assertEqual(action["res_model"], "sale.order")
        self.assertEqual(order.partner_id, self.customer_debit)
        self.assertEqual(order.bc_channel, "phone")
        self.assertEqual(order.bc_orderer_name, "Herr Telefon")
        self.assertEqual(order.bc_received_by, self.user_bc)
        self.assertEqual(order.client_order_ref, "BC-4711")
        self.assertEqual(order.order_line.product_id, self.product)
        self.assertEqual(order.order_line.product_uom_qty, 3)
        self.assertEqual(order.order_line.discount, 2.0)
        self.assertIn("Bitte vormittags", order.message_ids.mapped("body")[0] + "".join(order.message_ids.mapped("body")))
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_sheet_requires_orderer_for_phone(self):
        sheet = self._sheet()
        self._set_qty(sheet, self.product, 1)
        with self.assertRaisesRegex(UserError, "wer bestellt hat"):
            sheet.action_create_order()

    def test_sheet_requires_a_quantity(self):
        sheet = self._sheet(orderer_name="X")
        with self.assertRaisesRegex(UserError, "mindestens einem Artikel"):
            sheet.action_create_order()

    def test_service_rejects_bad_input(self):
        """Negativtests der gemeinsamen Methode (auch Weg des Portals)."""
        Order = self.env["sale.order"]
        partner = self.customer_debit
        cases = [
            ([(self.other, 1)], "nicht auf dem Bestellschein"),
            ([(self.product, -1)], "ganze Zahl"),
            ([(self.product, 1.5)], "ganze Zahl"),
            ([(self.product, 10000)], "ganze Zahl"),
            ([(self.product, True)], "ganze Zahl"),
            ([(self.product, 1), (self.product, 2)], "doppelt"),
            ([(self.product, 0)], "mindestens einem Artikel"),
        ]
        for quantities, message in cases:
            with self.subTest(quantities=quantities), self.assertRaisesRegex(UserError, message):
                Order._bc_create_from_sheet(partner, quantities, "phone", orderer_name="X")
        with self.assertRaisesRegex(UserError, "nur für Blistercenter"):
            Order._bc_create_from_sheet(self.customer_other, [(self.product, 1)], "phone", orderer_name="X")

    def test_orderer_must_belong_to_customer(self):
        sheet = self._sheet(orderer_name="X")
        foreign = self.env["res.partner"].create({"name": "Fremder Kontakt", "parent_id": self.customer_other.id})
        sheet.orderer_id = foreign
        sheet._onchange_partner_id()
        self.assertFalse(sheet.orderer_id)

    def test_phone_entry_points_open_sheet(self):
        tile = self.env.ref("blistercenter.tile_phone_order").with_user(self.user_bc)
        action = tile.action_open()
        self.assertEqual(action["res_model"], "blistercenter.order.sheet")
        self.assertEqual(action["target"], "new")
        action = self.customer_debit.with_user(self.user_bc).action_bc_new_phone_order()
        self.assertEqual(action["res_model"], "blistercenter.order.sheet")
        self.assertEqual(action["context"]["default_partner_id"], self.customer_debit.id)

    def test_portal_channel_needs_no_receiver(self):
        order = self.env["sale.order"]._bc_create_from_sheet(
            self.customer_debit, [(self.product, 2)], "portal", orderer_name="Online Kunde")
        self.assertEqual(order.bc_channel, "portal")
        self.assertFalse(order.bc_received_by, "Online-Bestellung: niemand bei PLCS hat sie angenommen")
        order.action_confirm()
        self.assertEqual(order.state, "sale")


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterPortalOrderSheet(HttpCase):
    """Online-Bestellschein im Portal: Zugang, Abschicken, Negativtests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env.company
        env["blistercenter.config"]._get(cls.company)._bc_check_setup()
        cls.term = env.ref("blistercenter.payment_term_14days")
        cls.customer = env["res.partner"].create({
            "name": "Portal-Blistercenter (Test)", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "bc_discount": "1", "property_payment_term_id": cls.term.id})
        cls.contact = env["res.partner"].create({"name": "Petra Portal", "parent_id": cls.customer.id,
                                                 "email": "petra.portal@example.com"})
        cls.portal_user = new_test_user(env, login="bc_portal_user", groups="base.group_portal",
                                        partner_id=cls.contact.id)
        cls.other_customer = env["res.partner"].create({
            "name": "Nicht freigeschaltet (Test)", "is_company": True, "bc_is_customer": True})
        cls.other_contact = env["res.partner"].create({"name": "Otto Ohne", "parent_id": cls.other_customer.id})
        cls.other_user = new_test_user(env, login="bc_portal_other", groups="base.group_portal",
                                       partner_id=cls.other_contact.id)
        Product = env["product.product"]
        cls.inside = Product.create({"name": "Sortiment Portal 10 St", "default_code": "11111111",
                                     "list_price": 100.0, "sale_ok": True, "bc_shop_product": True,
                                     "invoice_policy": "delivery"})
        cls.outside = Product.create({"name": "Fremd Portal", "list_price": 5.0, "sale_ok": True})

    def _page(self, user):
        self.authenticate(user.login, user.login + "x" * (8 - len(user.login)))
        res = self.url_open("/my/bestellschein")
        self.assertEqual(res.status_code, 200)
        return res.text

    def _post(self, user, data):
        html = self._page(user)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)
        data = dict(data, csrf_token=token.group(1) if token else "")
        return self.url_open("/my/bestellschein", data=data)

    def _orders(self, partner=None):
        return self.env["sale.order"].search([("partner_id", "=", (partner or self.customer).id)])

    def test_page_lists_assortment_with_customer_price(self):
        html = self._page(self.portal_user)
        self.assertIn("Sortiment Portal 10 St", html)
        self.assertNotIn("Fremd Portal", html)
        self.assertIn("99.00", html.replace(",", "."), "Ihr Preis = HAP − 1 %")
        self.assertIn('name="qty_%s"' % self.inside.id, html)

    def test_submit_creates_new_order_for_plcs(self):
        res = self._post(self.portal_user, {
            "qty_%s" % self.inside.id: "4", "client_order_ref": "PO-9", "note": "Lieferung bis Freitag"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Danke", res.text)
        order = self._orders()
        self.assertEqual(len(order), 1)
        self.assertEqual(order.state, "sent")
        self.assertEqual(order.bc_channel, "portal")
        self.assertEqual(order.bc_orderer_id, self.contact)
        self.assertEqual(order.client_order_ref, "PO-9")
        self.assertEqual(order.order_line.product_id, self.inside)
        self.assertEqual(order.order_line.product_uom_qty, 4)
        self.assertEqual(order.order_line.discount, 1.0)
        self.assertTrue(any("Lieferung bis Freitag" in (b or "") for b in order.message_ids.mapped("body")))
        self.assertIn(order.name, res.text)
        # PLCS sieht die Bestellung unter „Neue Bestellungen“
        tile = self.env.ref("blistercenter.tile_new_orders")
        model, domain, _x = tile._get_tile()
        self.assertIn(order, self.env[model].search(domain))

    def test_foreign_product_rejected(self):
        """Negativtest: Menge für einen Artikel außerhalb des Bestellscheins (manipuliertes Formular)."""
        res = self._post(self.portal_user, {
            "qty_%s" % self.inside.id: "1", "qty_%s" % self.outside.id: "5"})
        self.assertIn("nicht (mehr) auf dem Bestellschein", res.text)
        self.assertFalse(self._orders())

    def test_invalid_quantities_rejected(self):
        for raw in ("-1", "1.5", "abc", "10000"):
            with self.subTest(raw=raw):
                res = self._post(self.portal_user, {"qty_%s" % self.inside.id: raw})
                self.assertIn("ganze Zahl", res.text)
                self.assertFalse(self._orders())

    def test_empty_sheet_rejected(self):
        res = self._post(self.portal_user, {"qty_%s" % self.inside.id: ""})
        self.assertIn("mindestens einem Artikel", res.text)
        self.assertFalse(self._orders())

    def test_not_enabled_customer_gets_no_form(self):
        html = self._page(self.other_user)
        self.assertIn("noch nicht freigeschaltet", html)
        self.assertNotIn('name="qty_', html)
        self.authenticate(self.other_user.login, self.other_user.login + "x" * (8 - len(self.other_user.login)))
        # Direkter POST ohne Formular wird abgewiesen (CSRF fehlt oder kein Zugang) – kein Auftrag
        self.url_open("/my/bestellschein", data={"qty_%s" % self.inside.id: "1"})
        self.assertFalse(self._orders(self.other_customer))

    def test_payment_block_gets_no_form(self):
        self.customer.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        html = self._page(self.portal_user)
        self.assertIn("derzeit nicht möglich", html)
        self.assertNotIn('name="qty_', html)

    def test_thanks_page_hides_foreign_orders(self):
        foreign = self.env["sale.order"].create({"partner_id": self.other_customer.id})
        self.authenticate(self.portal_user.login, self.portal_user.login + "x" * (8 - len(self.portal_user.login)))
        res = self.url_open("/my/bestellschein/danke?ref=%s" % foreign.id)
        self.assertIn("nicht gefunden", res.text)
        self.assertNotIn(foreign.name, res.text)

    def test_portal_card_only_for_enabled_customers(self):
        def count(user):
            self.authenticate(user.login, user.login + "x" * (8 - len(user.login)))
            return self.make_jsonrpc_request("/my/counters", {"counters": {"bc_sheet_count": "client_category"}})
        self.assertGreater(count(self.portal_user).get("bc_sheet_count", 0), 0)
        self.assertEqual(count(self.other_user).get("bc_sheet_count", 0), 0)


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterWebsiteBranding(HttpCase):
    """1.4.0: Website-Fußzeile mit Firmendaten statt Odoo-Demotext, PLCS-Farben einmalig gesetzt."""

    def test_footer_shows_company_data(self):
        website = self.env["website"].search([], order="id", limit=1)
        company = website.company_id
        company.write({"street": "Fußweg 7", "zip": "66687", "city": "Wadern",
                       "email": "info@plcs-test.example", "phone": "+49 1 23"})
        company.plcs_legal_name = "Pharma-Logistik-Testname GmbH"
        html = self.url_open("/shop").text  # Kopf- und Fußzeile (Inhalt der Kontaktseite bleibt Editor-Sache)
        self.assertIn("Pharma-Logistik-Testname GmbH", html)
        self.assertIn("info@plcs-test.example", html)
        self.assertIn("Fußweg 7", html)
        self.assertIn("/my/bestellschein", html)
        self.assertNotIn("We are a team of passionate people", html)
        self.assertNotIn("info@yourcompany.example.com", html)
        self.assertNotIn("555-555-5556", html, "Odoo-Demonummer weder in Kopf- noch Fußzeile")
        self.assertIn("+49 1 23", html)

    def test_palette_customized(self):
        self.env["blistercenter.config"]._bc_apply_website_branding()
        attachments = self.env["ir.attachment"].search([("url", "like", "user_color_palette.scss")])
        self.assertTrue(attachments)
        self.assertIn("#9c0000", attachments[:1].raw.decode())
