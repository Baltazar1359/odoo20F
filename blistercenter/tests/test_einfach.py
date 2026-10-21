"""1.3.0: Shop nur mit Blistercenter-Sortiment (R40), Arbeitsbereiche und Startseite (R41)."""
import importlib.util
import os

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import HttpCase, new_test_user, tagged

from .test_blistercenter import BlistercenterCommon


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterShopAssortment(BlistercenterCommon):
    """R40: Im Shop nur Artikel mit „Im Blistercenter-Shop“; Telefonbestellung unberührt."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].create({"name": "BC Shop R40", "company_id": cls.company.id})
        cls.other = cls.env["product.product"].create({
            "name": "Fremdartikel (nicht im Sortiment)", "type": "consu", "is_storable": True,
            "tracking": "lot", "use_expiration_date": True, "invoice_policy": "delivery",
            "list_price": 5.0, "taxes_id": [Command.clear()], "is_published": True,
            "seller_ids": [Command.create({"partner_id": cls.supplier.id, "price": 2.0})],
        })
        cls.product.is_published = True

    def _shop_templates(self):
        return self.env["product.template"].search(self.website.sale_product_domain())

    def test_shop_lists_only_assortment(self):
        found = self._shop_templates()
        self.assertIn(self.product.product_tmpl_id, found)
        self.assertNotIn(self.other.product_tmpl_id, found, "Fremdartikel darf im Shop nicht erscheinen")
        self.assertFalse(found.filtered(lambda t: not t.bc_shop_product), "Nur Sortimentsartikel im Shop")
        self.assertTrue(self.product.product_tmpl_id._bc_shop_allowed(self.website))
        self.assertFalse(self.other.product_tmpl_id._bc_shop_allowed(self.website))

    def test_works_without_explicit_website(self):
        """Aufruf ohne übergebene Website (aktuelle Website aus dem Kontext bzw. keine): kein Fehler."""
        Website = self.env["website"]
        self.assertIn(("bc_shop_product", "=", True), list(Website._product_domain()))
        self.assertFalse(self.other.product_tmpl_id._bc_shop_allowed())
        ctx_product = self.other.product_tmpl_id.with_context(website_id=self.website.id)
        self.assertFalse(ctx_product._bc_shop_allowed())
        self.assertTrue(self.product.product_tmpl_id.with_context(website_id=self.website.id)._bc_shop_allowed())
        self.assertFalse(self.other.with_context(website_id=self.website.id)._is_add_to_cart_allowed())

    def test_cart_rejects_product_outside_assortment(self):
        order = self._order(self.customer_debit, confirm=False, website_id=self.website.id)
        with self.assertRaisesRegex(UserError, "nicht im Blistercenter-Shop"):
            order.with_context(skip_cart_verification=True)._cart_add(self.other.id, 1)
        self.assertNotIn(self.other, order.order_line.product_id)
        order.with_context(skip_cart_verification=True)._cart_add(self.product.id, 1)

    def test_shop_order_with_outside_line_cannot_confirm(self):
        """Negativtest: Fremdartikel an der Oberfläche vorbei in eine Shop-Bestellung geschrieben."""
        order = self._order(self.customer_debit, confirm=False, website_id=self.website.id)
        order.order_line = [Command.create({"product_id": self.other.id, "product_uom_qty": 1})]
        with self.assertRaisesRegex(UserError, "Blistercenter-Sortiment"):
            order.action_confirm()
        self.assertNotEqual(order.state, "sale")

    def test_phone_order_not_affected(self):
        """Telefonbestellung darf jeden Artikel enthalten."""
        order = self.env["sale.order"].create({
            "partner_id": self.customer_debit.id, "bc_channel": "phone", "bc_orderer_name": "Herr Test",
            "order_line": [Command.create({"product_id": self.other.id, "product_uom_qty": 1})],
        })
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_rule_can_be_switched_off(self):
        self.config.rule_shop_only_bc_products = False
        self.assertIn(self.other.product_tmpl_id, self._shop_templates())
        self.assertTrue(self.other.product_tmpl_id._bc_shop_allowed(self.website))
        order = self._order(self.customer_debit, confirm=False, website_id=self.website.id)
        order.with_context(skip_cart_verification=True)._cart_add(self.other.id, 1)
        self.assertIn(self.other, order.order_line.product_id)

    def test_demo_data_loading_not_blocked(self):
        """Odoos Demo-Shopbestellungen enthalten Demoprodukte – das Laden darf nicht abbrechen,
        über RPC (Kontextwert allein) lässt sich die Regel aber nicht abschalten."""
        order = self._order(self.customer_debit, confirm=False, website_id=self.website.id)
        order.order_line = [Command.create({"product_id": self.other.id, "product_uom_qty": 1})]
        with self.assertRaisesRegex(UserError, "Blistercenter-Sortiment"):
            order.with_user(self.user_manager).with_context(install_demo=True).action_confirm()
        order.sudo().with_context(install_demo=True).action_confirm()
        self.assertEqual(order.state, "sale")


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterWorkAreas(BlistercenterCommon):
    """R41: Menüs und Startseite je Arbeitsbereich."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def user(login, groups):
            return new_test_user(cls.env, login=login, groups=groups, company_id=cls.company.id,
                                 company_ids=[Command.set(cls.company.ids)])

        cls.only_sales = user("bc_only_sales", "blistercenter.group_bc_sales")
        cls.only_purchase = user("bc_only_purchase", "blistercenter.group_bc_purchase")
        cls.only_stock = user("bc_only_stock", "blistercenter.group_bc_stock")
        cls.no_area = user("bc_no_area", "blistercenter.group_bc_user")

    def _menus(self, user):
        visible = self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()

        def shown(menu):
            # Die Oberfläche zeigt einen Menüpunkt nur, wenn auch alle übergeordneten sichtbar sind
            while menu:
                if menu.id not in visible:
                    return False
                menu = menu.parent_id
            return True
        return {xmlid for xmlid in (
            "menu_bc_root", "menu_bc_overview", "menu_bc_customers", "menu_bc_orders", "menu_bc_phone_order",
            "menu_bc_collective", "menu_bc_suppliers", "menu_bc_stock", "menu_bc_release", "menu_bc_picking",
            "menu_bc_check", "menu_bc_lots", "menu_bc_recall", "menu_bc_settings",
        ) if shown(self.env.ref("blistercenter." + xmlid))}

    def _tiles(self, user):
        return set(self.env["blistercenter.overview"].with_user(user).search([]).mapped("key"))

    def test_menus_per_area(self):
        base = {"menu_bc_root", "menu_bc_overview"}
        self.assertEqual(self._menus(self.only_sales),
                         base | {"menu_bc_customers", "menu_bc_orders", "menu_bc_phone_order"})
        self.assertEqual(self._menus(self.only_purchase), base | {"menu_bc_collective", "menu_bc_suppliers"})
        self.assertEqual(self._menus(self.only_stock),
                         base | {"menu_bc_stock", "menu_bc_picking", "menu_bc_check", "menu_bc_lots"})
        self.assertEqual(self._menus(self.no_area), base, "Ohne Arbeitsbereich nur die Startseite")
        approver = self._menus(self.user_approver)
        self.assertTrue({"menu_bc_release", "menu_bc_recall", "menu_bc_collective", "menu_bc_phone_order",
                         "menu_bc_stock"} <= approver, "Freigabe sieht alle Bereiche")
        self.assertNotIn("menu_bc_settings", approver)
        self.assertIn("menu_bc_settings", self._menus(self.user_manager))

    def test_tiles_per_area(self):
        self.assertEqual(self._tiles(self.only_sales),
                         {"phone_order", "new_orders", "overdue_invoices", "payment_blocks"})
        self.assertEqual(self._tiles(self.only_purchase), {"to_procure", "open_collective"})
        self.assertEqual(self._tiles(self.only_stock),
                         {"open_receipts", "open_deliveries", "to_check", "short_dated", "blocked_lots"})
        self.assertEqual(self._tiles(self.no_area), set())
        # Negativtest: Odoos Rechnungsrolle (haben viele interne Benutzer) zeigt keine Rechnungskacheln
        self.only_stock.group_ids |= self.env.ref("account.group_account_invoice")
        self.assertEqual(self._tiles(self.only_stock),
                         {"open_receipts", "open_deliveries", "to_check", "short_dated", "blocked_lots"})
        self.assertEqual(len(self._tiles(self.user_approver)), 13)
        self.assertIn("to_release", self._tiles(self.user_approver))

    def test_start_page_grouped_by_area(self):
        """Startseite in Spalten je Bereich; jeder sieht nur seine Spalte(n)."""
        Tile = self.env["blistercenter.overview"]
        self.assertFalse(Tile.search([("area", "=", False)]))
        def areas(user):
            return {area for (area,) in Tile.with_user(user)._read_group([], ["area"])}
        self.assertEqual(areas(self.only_stock), {"stock"})
        self.assertEqual(areas(self.only_sales), {"sales"})
        self.assertEqual(areas(self.user_approver), {"sales", "purchase", "stock", "approval"})

    def test_tile_rule_cannot_be_bypassed(self):
        """Negativtest: Lager liest per RPC keine Kachel eines anderen Bereichs."""
        tile = self.env.ref("blistercenter.tile_to_procure")
        Tile = self.env["blistercenter.overview"].with_user(self.only_stock)
        self.assertFalse(Tile.search([("id", "=", tile.id)]))
        with self.assertRaises(Exception):
            Tile.browse(tile.id).read(["name"])
        with self.assertRaises(Exception):
            tile.with_user(self.only_stock).write({"group_ids": [Command.link(self.env.ref(
                "blistercenter.group_bc_stock").id)]})

    def test_phone_order_tile_opens_form(self):
        tile = self.env.ref("blistercenter.tile_phone_order").with_user(self.only_sales)
        action = tile.action_open()
        self.assertEqual(action["res_model"], "blistercenter.order.sheet", "1.4.0: Bestellschein")
        self.assertEqual(action["view_mode"], "form")

    def test_areas_do_not_change_rights(self):
        """Arbeitsbereiche steuern nur die Sicht; die Rechte kommen weiter aus „Benutzer“."""
        for user in (self.only_sales, self.only_purchase, self.only_stock):
            self.assertTrue(user.has_group("blistercenter.group_bc_user"))
            self.assertFalse(user.has_group("blistercenter.group_bc_approver"))

    def test_migration_gives_existing_users_all_areas(self):
        path = os.path.join(os.path.dirname(__file__), "..", "migrations", "20.0.1.3.0", "post-migrate.py")
        spec = importlib.util.spec_from_file_location("bc_mig_130", path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        areas = (self.env.ref("blistercenter.group_bc_sales") | self.env.ref("blistercenter.group_bc_purchase")
                 | self.env.ref("blistercenter.group_bc_stock"))
        self.assertFalse(areas & self.no_area.group_ids)
        mig.migrate(self.env.cr, "20.0.1.2.1")
        self.env.invalidate_all()
        self.assertEqual(areas & self.no_area.group_ids, areas)
        self.assertEqual(areas & self.only_stock.group_ids, self.env.ref("blistercenter.group_bc_stock"),
                         "Wer schon einen Bereich hat, behält genau diesen")
        self.assertFalse(areas & self.user_approver.group_ids, "Freigabe braucht keine Bereiche")


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterDemoData(BlistercenterCommon):
    """1.3.0: Demodaten bringen das Shop-Sortiment mit (jeder Dev-Build), mehrfach ladbar."""

    def test_demo_loader_assortment(self):
        Categ = self.env["product.public.category"]
        foreign = Categ.create({"name": "Desks (Demo)"})
        Config = self.env["blistercenter.config"]
        Config._bc_load_demo_data()
        Config._bc_load_demo_data()  # zweites Mal: keine Duplikate
        Template = self.env["product.template"]
        shop = Template.search([("bc_shop_product", "=", True), ("default_code", "!=", False)])
        demo_codes = {a[0] for a in self._articles()}
        shop = shop.filtered(lambda t: t.default_code in demo_codes)
        self.assertEqual(len(shop), 13)
        self.assertEqual({t.name.split()[0] for t in shop}, {"FORXIGA", "XIGDUO", "DAXAS", "BRILIQUE", "TAGRISSO"})
        self.assertEqual(set(shop.public_categ_ids.mapped("name")), {"Forxiga", "Xigduo", "Daxas", "Brilique", "Tagrisso"})
        others = Template.search([("default_code", "in", list(demo_codes)), ("bc_shop_product", "=", False)])
        self.assertEqual(len(others), 7)
        self.assertFalse(others.filtered("is_published"), "Übrige Testartikel nicht im Shop")
        self.assertEqual(Template.search_count([("default_code", "in", list(demo_codes))]), 20, "keine Duplikate")
        customers = self.env["res.partner"].search([("name", "like", "(Test)"), ("bc_is_customer", "=", True)])
        self.assertGreaterEqual(len(customers.filtered(lambda p: p.name.startswith("Blistercenter "))), 3)
        self.assertGreaterEqual(self.config.shop_product_count, 13)
        self.assertTrue(foreign.not_in_shop, "Fremde Kategorie ohne Sortiment nicht in der Kategorieleiste")
        self.assertFalse(shop.public_categ_ids.filtered("not_in_shop"))

    def _articles(self):
        from ..tools import testdaten
        return testdaten.ARTICLES

    def test_foreign_demo_products_archived(self):
        """Odoo-Demowaren (z. B. Möbel) werden archiviert; PLCS-Artikel und Dienstleistungen bleiben."""
        from ..tools import testdaten
        Template = self.env["product.template"]
        chair = Template.create({"name": "Demo-Stuhl", "type": "consu", "sale_ok": True})
        service = Template.create({"name": "Demo-Hotel", "type": "service"})
        own = Template.create({"name": "Eigener Artikel ohne XML-ID", "type": "consu"})
        for rec, name in ((chair, "demo_chair_x"), (service, "demo_service_x")):
            self.env["ir.model.data"].create({"module": "product", "name": name,
                                              "model": "product.template", "res_id": rec.id})
        self.env["ir.model.data"].create({"module": "product", "name": "demo_pzn_x", "model": "product.template",
                                          "res_id": self.product.product_tmpl_id.id})
        archived = testdaten.archive_foreign_demo_products(self.env)
        self.assertIn(chair, archived)
        self.assertFalse(chair.active)
        self.assertFalse(service.active, "Demo-Dienstleistung ohne Bezug zu PLCS")
        self.assertTrue(own.active, "Ohne fremde XML-ID: von Hand angelegt, bleibt")
        self.assertTrue(self.product.product_tmpl_id.active, "Sortimentsartikel bleibt")

    def test_demo_loader_not_callable_by_rpc_users(self):
        """Negativtest: private Methode – über die Weboberfläche nicht aufrufbar."""
        self.assertTrue(self.env["blistercenter.config"]._bc_load_demo_data.__name__.startswith("_"))

    def test_empty_assortment_warning(self):
        self.env["product.template"].search([("bc_shop_product", "=", True)]).bc_shop_product = False
        self.config.invalidate_recordset()
        self.assertEqual(self.config.shop_product_count, 0)
        action = self.config.with_user(self.user_manager).action_open_shop_products()
        self.assertEqual(action["domain"], [("bc_shop_product", "=", True)])


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterShopPage(HttpCase):
    """R40: Produktseite eines Fremdartikels gibt es im Shop nicht (404)."""

    def test_product_page_only_for_assortment(self):
        website = self.env["website"].search([], order="id", limit=1)
        Template = self.env["product.template"]
        inside = Template.create({"name": "Sortiment R40", "is_published": True, "sale_ok": True,
                                  "bc_shop_product": True, "website_id": website.id})
        outside = Template.create({"name": "Fremd R40", "is_published": True, "sale_ok": True,
                                   "website_id": website.id})
        self.authenticate("admin", "admin")
        self.assertEqual(self.url_open(inside.website_url).status_code, 200)
        self.assertEqual(self.url_open(outside.website_url).status_code, 404)
