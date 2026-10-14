from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "blistercenter")
class BlistercenterCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Testbenutzer der Buchhaltungs-Basisklasse bekommt zusätzlich Verkauf und Blistercenter-Administrator
        cls.env.user.group_ids |= cls.env.ref("sales_team.group_sale_manager") | cls.env.ref("blistercenter.group_bc_manager")
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.stock_location = cls.warehouse.lot_stock_id

        def user(login, groups):
            return new_test_user(
                cls.env, login=login, groups=groups,
                company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)],
            )

        cls.user_bc = user("bc_user", "blistercenter.group_bc_user")
        cls.user_approver = user("bc_approver", "blistercenter.group_bc_approver")
        cls.user_manager = user("bc_manager", "blistercenter.group_bc_manager")
        cls.user_sales = user("plain_sales", "sales_team.group_sale_salesman")

        Partner = cls.env["res.partner"].sudo()
        cls.supplier = Partner.create({"name": "AstraZeneca (Test)", "is_company": True, "bc_is_supplier": True})
        cls.customer_prepay = Partner.create({
            "name": "Blistercenter Vorkasse", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "bc_payment_model": "prepayment",
        })
        cls.customer_invoice = Partner.create({
            "name": "Blistercenter Rechnung", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "bc_payment_model": "invoice",
        })
        cls.customer_other = Partner.create({"name": "Kein Blistercenter", "is_company": True})

        cls.product = cls.env["product.product"].create({
            "name": "Testarzneimittel 10 St.",
            "type": "consu",
            "is_storable": True,
            "tracking": "lot",
            "invoice_policy": "order",
            "list_price": 20.0,
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "seller_ids": [Command.create({"partner_id": cls.supplier.id, "price": 10.0})],
        })
        cls.config = cls.env["blistercenter.config"]._get(cls.company)
        cls.config.write({"sales_user_id": cls.user_bc.id, "purchase_user_id": cls.user_bc.id})

    # Hilfsfunktionen ------------------------------------------------------
    def _order(self, partner, qty=5.0, confirm=True, **vals):
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
            **vals,
        })
        if confirm:
            order.action_confirm()
        return order

    def _pay(self, order):
        invoice = order._create_invoices()
        invoice.action_post()
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create({})._create_payments()
        return invoice

    def _stock(self, qty, lot_name="CH-001"):
        lot = self.env["stock.lot"].create({"name": lot_name, "product_id": self.product.id, "company_id": self.company.id})
        self.env["stock.quant"]._update_available_quantity(self.product, self.stock_location, qty, lot_id=lot)
        return lot

    def _delivery(self, order):
        picking = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        picking.action_assign()
        return picking

    def _collect(self, orders, user=None):
        lines = orders.order_line
        wizard = self.env["blistercenter.collective.add"].with_user(user or self.user_bc).with_context(
            active_model="sale.order.line", active_ids=lines.ids,
        ).create({"mode": "new", "supplier_id": self.supplier.id})
        action = wizard.action_apply()
        return self.env["blistercenter.collective.order"].browse(action["res_id"])


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterRights(BlistercenterCommon):

    def test_shop_enable_only_by_approver(self):
        partner = self.customer_other.sudo()
        partner.bc_is_customer = True
        with self.assertRaises(AccessError):
            partner.with_user(self.user_bc).write({"bc_shop_enabled": True})
        partner.with_user(self.user_approver).write({"bc_shop_enabled": True})
        self.assertTrue(partner.bc_shop_enabled)
        self.assertEqual(partner.bc_shop_enabled_by, self.user_approver)
        self.assertTrue(partner.bc_shop_enabled_date)

    def test_create_with_shop_enabled_needs_approver(self):
        Partner = self.env["res.partner"]
        with self.assertRaises(AccessError):
            Partner.with_user(self.user_bc).create({"name": "Neu", "bc_is_customer": True, "bc_shop_enabled": True})
        Partner.with_user(self.user_bc).create({"name": "Neu ohne Shop", "bc_is_customer": True})

    def test_conditions_of_blistercenter_need_approver(self):
        term = self.env.ref("account.account_payment_term_30days")
        with self.assertRaises(AccessError):
            self.customer_invoice.with_user(self.user_bc).write({"property_payment_term_id": term.id})
        with self.assertRaises(AccessError):
            self.customer_invoice.with_user(self.user_bc).write({"bc_payment_model": "prepayment"})
        # Andere Kunden bleiben unberührt (Standardrechte)
        self.customer_other.with_user(self.user_bc).write({"property_payment_term_id": term.id})
        self.customer_invoice.with_user(self.user_approver).write({"property_payment_term_id": term.id})
        self.assertEqual(self.customer_invoice.property_payment_term_id, term)

    def test_payment_block_needs_reason_and_approver(self):
        partner = self.customer_invoice
        with self.assertRaises(AccessError):
            partner.with_user(self.user_bc).write({"bc_payment_block": True, "bc_payment_block_reason": "Mahnung"})
        with self.assertRaises(UserError):
            partner.with_user(self.user_approver).write({"bc_payment_block": True})
        partner.with_user(self.user_approver).write({"bc_payment_block": True, "bc_payment_block_reason": "Mahnung offen"})
        self.assertTrue(partner.bc_payment_block)
        partner.with_user(self.user_approver).write({"bc_payment_block": False})
        self.assertFalse(partner.bc_payment_block_reason)

    def test_config_only_manager(self):
        with self.assertRaises(AccessError):
            self.config.sudo(False).with_user(self.user_bc).write({"rule_block_confirm": False})
        with self.assertRaises(AccessError):
            self.env["blistercenter.config"].with_user(self.user_approver).action_open()
        self.config.sudo(False).with_user(self.user_manager).write({"rule_block_confirm": False})
        self.assertFalse(self.config.rule_block_confirm)
        action = self.env["blistercenter.config"].with_user(self.user_manager).action_open()
        self.assertEqual(action["res_id"], self.config.id)

    def test_defaults_for_new_blistercenter(self):
        term = self.env.ref("account.account_payment_term_30days")
        self.config.write({"default_payment_model": "invoice", "default_payment_term_id": term.id})
        partner = self.env["res.partner"].with_user(self.user_bc).create({"name": "Neues BC", "bc_is_customer": True})
        self.assertEqual(partner.bc_payment_model, "invoice")
        self.assertEqual(partner.property_payment_term_id, term)

    def test_no_access_without_blistercenter_role(self):
        with self.assertRaises(AccessError):
            self.env["blistercenter.collective.order"].with_user(self.user_sales).search([])
        with self.assertRaises(AccessError):
            self.env["blistercenter.config"].with_user(self.user_sales).search([])

    def test_lot_block_anyone_unblock_only_approver(self):
        lot = self._stock(1, "CH-SPERR")
        with self.assertRaises(UserError):
            lot.with_user(self.user_bc).write({"bc_blocked": True})
        lot.with_user(self.user_bc).write({"bc_blocked": True, "bc_block_reason": "Temperaturabweichung"})
        self.assertEqual(lot.bc_blocked_by, self.user_bc)
        with self.assertRaises(AccessError):
            lot.with_user(self.user_bc).write({"bc_blocked": False})
        lot.with_user(self.user_approver).write({"bc_blocked": False})
        self.assertFalse(lot.bc_blocked)
        self.assertFalse(lot.bc_block_reason)


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterFlow(BlistercenterCommon):

    def test_order_flagged_and_activity_without_mail(self):
        mails_before = self.env["mail.mail"].search_count([])
        order = self._order(self.customer_invoice)
        self.assertTrue(order.bc_is_blistercenter)
        activity = order.activity_ids.filtered(lambda a: a.user_id == self.user_bc)
        self.assertEqual(len(activity), 1)
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before, "Keine E-Mail bei Aufgaben")
        self.assertFalse(self._order(self.customer_other).bc_is_blistercenter)

    def test_payment_block_prevents_confirm(self):
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        order = self._order(self.customer_invoice, confirm=False)
        with self.assertRaises(UserError):
            order.action_confirm()
        self.config.rule_block_confirm = False
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_shop_only_for_enabled_blistercenter(self):
        website = self.env["website"].create({"name": "BC Shop", "company_id": self.company.id})
        self.customer_invoice.bc_shop_enabled = False
        order = self._order(self.customer_invoice, confirm=False, website_id=website.id)
        with self.assertRaises(UserError):
            order.action_confirm()
        with self.assertRaises(UserError):
            order.with_context(skip_cart_verification=True)._cart_add(self.product.id, 1)
        other = self._order(self.customer_other, confirm=False, website_id=website.id)
        with self.assertRaises(UserError):
            other.action_confirm()
        self.customer_invoice.bc_shop_enabled = True
        order.with_context(skip_cart_verification=True)._cart_add(self.product.id, 1)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_prepayment_before_collective(self):
        order = self._order(self.customer_prepay)
        self.assertEqual(order.bc_state, "awaiting_payment")
        with self.assertRaises(ValidationError):
            self._collect(order)
        self._pay(order)
        self.assertTrue(order.bc_is_paid)
        self.assertEqual(order.bc_state, "to_procure")
        collective = self._collect(order)
        self.assertEqual(collective.line_ids.qty, 5.0)
        self.assertEqual(order.bc_state, "procuring")

    def test_payment_block_prevents_collective(self):
        order = self._order(self.customer_invoice)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        with self.assertRaises(ValidationError):
            self._collect(order)

    def test_no_double_allocation(self):
        order = self._order(self.customer_invoice, qty=5)
        line = order.order_line
        first = self._collect(order)
        self.assertEqual(line.bc_qty_open, 0)
        second = self.env["blistercenter.collective.order"].create({"supplier_id": self.supplier.id})
        with self.assertRaises(ValidationError):
            self.env["blistercenter.collective.order.line"].create({"order_id": second.id, "sale_line_id": line.id, "qty": 1})
        with self.assertRaises(ValidationError):
            first.line_ids.qty = 6
        first.action_cancel()
        self.assertEqual(line.bc_qty_open, 5)
        self.env["blistercenter.collective.order.line"].create({"order_id": second.id, "sale_line_id": line.id, "qty": 5})
        self.assertEqual(line.bc_qty_allocated, 5)

    def test_non_blistercenter_line_rejected(self):
        order = self._order(self.customer_other)
        collective = self.env["blistercenter.collective.order"].create({"supplier_id": self.supplier.id})
        with self.assertRaises(ValidationError):
            self.env["blistercenter.collective.order.line"].create({
                "order_id": collective.id, "sale_line_id": order.order_line.id, "qty": 1,
            })

    def test_collective_creates_one_aggregated_rfq(self):
        order_a = self._order(self.customer_invoice, qty=3)
        order_b = self._order(self.customer_invoice, qty=5)
        collective = self._collect(order_a | order_b)
        self.assertEqual(len(collective.line_ids), 2)
        purchases_before = self.env["purchase.order"].search_count([])
        collective.with_user(self.user_bc).action_create_purchase()
        self.assertEqual(self.env["purchase.order"].search_count([]), purchases_before + 1)
        purchase = collective.purchase_id
        self.assertEqual(collective.state, "ordered")
        self.assertEqual(purchase.state, "draft", "Anfrage bleibt Entwurf; bestellt wird manuell")
        self.assertEqual(purchase.partner_id, self.supplier)
        self.assertEqual(len(purchase.order_line), 1)
        self.assertEqual(purchase.order_line.product_qty, 8)
        self.assertEqual(purchase.order_line.price_unit, 10.0, "Einkaufskondition des Herstellers")
        self.assertTrue(all(line.purchase_line_id == purchase.order_line for line in collective.line_ids))
        self.assertTrue(purchase.bc_is_collective)
        self.assertTrue(collective.activity_ids)
        with self.assertRaises(UserError):
            collective.action_create_purchase()
        with self.assertRaises(UserError):
            collective.line_ids[0].qty = 1
        with self.assertRaises(UserError):
            collective.unlink()

    def test_cancel_after_confirmed_purchase_denied(self):
        collective = self._collect(self._order(self.customer_invoice))
        collective.action_create_purchase()
        collective.purchase_id.button_confirm()
        with self.assertRaises(UserError):
            collective.action_cancel()

    def test_cancel_draft_purchase_with_collective(self):
        collective = self._collect(self._order(self.customer_invoice))
        collective.action_create_purchase()
        purchase = collective.purchase_id
        collective.action_cancel()
        self.assertEqual(purchase.state, "cancel")
        self.assertEqual(collective.state, "cancel")

    def test_receipt_completes_collective(self):
        order = self._order(self.customer_invoice, qty=4)
        collective = self._collect(order)
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.button_confirm()
        receipt = purchase.picking_ids
        receipt.move_ids.move_line_ids.unlink()
        receipt.move_ids._action_assign()
        ml = receipt.move_ids.move_line_ids
        ml.write({"lot_name": "CH-EIN-1", "quantity": 4})
        receipt.move_ids.picked = True
        receipt.button_validate()
        self.assertEqual(receipt.state, "done")
        self.assertEqual(collective.state, "done")

    def test_delivery_payment_block_and_prepayment(self):
        self._stock(20)
        order = self._order(self.customer_invoice, qty=2)
        picking = self._delivery(order)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        with self.assertRaises(UserError):
            picking.button_validate()
        self.customer_invoice.write({"bc_payment_block": False})
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        self.assertEqual(order.bc_state, "done")

        order_prepay = self._order(self.customer_prepay, qty=2)
        picking = self._delivery(order_prepay)
        with self.assertRaises(UserError):
            picking.button_validate()
        self._pay(order_prepay)
        picking.button_validate()
        self.assertEqual(picking.state, "done")

    def test_blocked_lot_not_shipped(self):
        lot = self._stock(5, "CH-GESPERRT")
        order = self._order(self.customer_invoice, qty=2)
        picking = self._delivery(order)
        self.assertEqual(picking.move_line_ids.lot_id, lot)
        lot.write({"bc_blocked": True, "bc_block_reason": "Rückruf"})
        with self.assertRaises(UserError):
            picking.button_validate()
        lot.with_user(self.user_approver).write({"bc_blocked": False})
        picking.button_validate()
        self.assertEqual(picking.state, "done")

    def test_recall_evaluation(self):
        lot = self._stock(10, "CH-RUECKRUF")
        order = self._order(self.customer_invoice, qty=3)
        picking = self._delivery(order)
        picking.button_validate()
        wizard = self.env["blistercenter.recall.wizard"].with_user(self.user_bc).create({
            "lot_ids": [Command.set(lot.ids)], "block_lots": True, "reason": "Rückruf 2026-01",
        })
        action = wizard.action_customers()
        pickings = self.env["stock.picking"].search(action["domain"])
        self.assertEqual(pickings, picking)
        self.assertEqual(pickings.partner_id, self.customer_invoice)
        lines = self.env["stock.move.line"].search(wizard.action_deliveries()["domain"])
        self.assertEqual(sum(lines.mapped("quantity")), 3)
        quants = self.env["stock.quant"].search(wizard.action_stock()["domain"])
        self.assertEqual(sum(quants.mapped("quantity")), 7)
        self.assertTrue(lot.bc_blocked)
        with self.assertRaises(UserError):
            self.env["blistercenter.recall.wizard"].create({"lot_ids": [Command.set(lot.ids)], "block_lots": True}).action_customers()

    def test_overview_counts(self):
        tiles = {t.key: t for t in self.env["blistercenter.overview"].with_user(self.user_bc).search([])}
        self.assertEqual(len(tiles), 8)
        before = {k: t.count for k, t in tiles.items()}
        self._order(self.customer_prepay)
        self._order(self.customer_invoice, confirm=False)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        self.env.invalidate_all()
        tiles = {t.key: t for t in self.env["blistercenter.overview"].with_user(self.user_bc).search([])}
        self.assertEqual(tiles["awaiting_payment"].count, before["awaiting_payment"] + 1)
        self.assertEqual(tiles["new_orders"].count, before["new_orders"] + 1)
        self.assertEqual(tiles["payment_blocks"].count, before["payment_blocks"] + 1)
        action = tiles["awaiting_payment"].action_open()
        self.assertEqual(action["res_model"], "sale.order")

    def test_menus_and_actions_load(self):
        """Alle Menüs der App lassen sich für die Rollen laden."""
        root = self.env.ref("blistercenter.menu_bc_root")
        for user, expect_settings in ((self.user_bc, False), (self.user_manager, True)):
            visible = self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()
            self.assertIn(root.id, visible)
            self.assertIn(self.env.ref("blistercenter.menu_bc_collective_list").id, visible)
            self.assertEqual(self.env.ref("blistercenter.menu_bc_settings").id in visible, expect_settings)
        visible = self.env["ir.ui.menu"].with_user(self.user_sales)._visible_menu_ids()
        self.assertNotIn(root.id, visible, "Ohne Blistercenter-Rolle keine App")
        for xmlid in (
            "action_bc_customers", "action_bc_suppliers", "action_bc_supplierinfo", "action_bc_sale_orders",
            "action_bc_sale_lines", "action_bc_collective_orders", "action_bc_purchase_orders",
            "action_bc_receipts", "action_bc_picking", "action_bc_release", "action_bc_partial",
            "action_bc_deliveries", "action_bc_lots", "action_bc_lots_expiry", "action_bc_lots_blocked",
            "action_bc_overview",
        ):
            action = self.env["ir.actions.act_window"].with_user(self.user_bc)._for_xml_id(f"blistercenter.{xmlid}")
            model = self.env[action["res_model"]].with_user(self.user_bc)
            model.search_count(eval(action.get("domain") or "[]") if isinstance(action.get("domain"), str) else action.get("domain") or [])
