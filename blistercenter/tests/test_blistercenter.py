from datetime import timedelta

from odoo import Command, fields
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
        cls.user_bc2 = user("bc_user2", "blistercenter.group_bc_user")
        cls.user_approver = user("bc_approver", "blistercenter.group_bc_approver")
        cls.user_approver2 = user("bc_approver2", "blistercenter.group_bc_approver")
        cls.user_manager = user("bc_manager", "blistercenter.group_bc_manager")
        cls.user_sales = user("plain_sales", "sales_team.group_sale_salesman")
        cls.user_accounting = user("bc_accounting", "account.group_account_invoice")

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
        cls.contact_invoice = Partner.create({
            "name": "Frau Muster", "parent_id": cls.customer_invoice.id, "type": "contact",
        })
        cls.customer_other = Partner.create({"name": "Kein Blistercenter", "is_company": True})

        cls.product = cls.env["product.product"].create({
            "name": "Testarzneimittel 10 St.",
            "type": "consu",
            "is_storable": True,
            "tracking": "lot",
            "use_expiration_date": True,
            "expiration_time": 730,
            "invoice_policy": "order",
            "list_price": 20.0,
            "taxes_id": [Command.clear()],
            "supplier_taxes_id": [Command.clear()],
            "seller_ids": [Command.create({"partner_id": cls.supplier.id, "price": 10.0})],
        })
        cls.config = cls.env["blistercenter.config"]._get(cls.company)
        cls.config._bc_check_setup()
        cls.config.write({
            "sales_user_id": cls.user_bc.id, "purchase_user_id": cls.user_bc.id,
            "accounting_user_id": cls.user_accounting.id, "vp_user_id": cls.user_approver.id,
        })
        cls.quarantine = cls.config.quarantine_location_id

    # Hilfsfunktionen ------------------------------------------------------
    def _order(self, partner, qty=5.0, confirm=True, **vals):
        if partner.commercial_partner_id.bc_is_customer and not vals.get("website_id"):
            vals.setdefault("bc_channel", "phone")
            vals.setdefault("bc_orderer_name", "Herr Test")
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
            **vals,
        })
        if confirm:
            order.action_confirm()
        return order

    def _register_payment(self, invoice):
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids,
        ).create({})._create_payments()

    def _pay(self, order):
        """Vorkasse: Anzahlungsrechnung buchen und bezahlen; sonst Rechnung erstellen und bezahlen."""
        invoice = order.invoice_ids.filtered(lambda m: m.state == "draft")
        if not invoice:
            invoice = order._create_invoices()
        invoice.action_post()
        self._register_payment(invoice)
        return invoice

    def _stock(self, qty, lot_name="CH-001", days=400):
        lot = self.env["stock.lot"].create({
            "name": lot_name, "product_id": self.product.id, "company_id": self.company.id,
            "expiration_date": fields.Datetime.now() + timedelta(days=days),
        })
        self.env["stock.quant"]._update_available_quantity(self.product, self.stock_location, qty, lot_id=lot)
        return lot

    def _delivery(self, order):
        picking = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        picking.action_assign()
        return picking

    def _ship(self, picking, picker=None, checker=None):
        """Kommissionieren (Person 1) und Endkontrolle mit Bestätigung (Person 2)."""
        picking.with_user(picker or self.user_bc).action_bc_mark_picked()
        return picking.with_user(checker or self.user_bc2).button_validate()

    def _collect(self, orders, user=None):
        lines = orders.order_line.filtered(lambda line: not line.is_downpayment and not line.display_type)
        wizard = self.env["blistercenter.collective.add"].with_user(user or self.user_bc).with_context(
            active_model="sale.order.line", active_ids=lines.ids,
        ).create({"mode": "new", "supplier_id": self.supplier.id})
        action = wizard.action_apply()
        return self.env["blistercenter.collective.order"].browse(action["res_id"])

    def _receive(self, collective, lot_name="CH-EIN-1", days=730, user=None, qty=None):
        """Herstellerbestellung bestätigen und Wareneingang (in die Quarantäne) buchen."""
        if collective.state == "draft":
            collective.action_create_purchase()
        purchase = collective.purchase_id
        if purchase.state in ("draft", "sent"):
            purchase.button_confirm()
        receipt = purchase.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        receipt.move_ids.move_line_ids.unlink()
        receipt.move_ids._action_assign()
        for move in receipt.move_ids:
            move.move_line_ids.write({
                "lot_name": lot_name,
                "quantity": qty if qty is not None else move.product_uom_qty,
                "expiration_date": fields.Datetime.now() + timedelta(days=days),
            })
        receipt.move_ids.picked = True
        receipt.with_user(user or self.user_bc).button_validate()
        release = receipt.move_ids.move_dest_ids.picking_id.filtered(lambda p: p.bc_kind == "release")
        return receipt, release


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
class TestBlistercenterOrders(BlistercenterCommon):

    def test_order_flagged_and_activity_without_mail(self):
        mails_before = self.env["mail.mail"].search_count([])
        order = self._order(self.customer_invoice)
        self.assertTrue(order.bc_is_blistercenter)
        activity = order.activity_ids.filtered(lambda a: a.user_id == self.user_bc)
        self.assertEqual(len(activity), 1)
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before, "Keine E-Mail bei Aufgaben")
        self.assertFalse(self._order(self.customer_other).bc_is_blistercenter)

    def test_phone_order_needs_channel_and_orderer(self):
        Sale = self.env["sale.order"].with_user(self.user_bc)
        line = [Command.create({"product_id": self.product.id, "product_uom_qty": 2})]
        order = Sale.create({"partner_id": self.customer_invoice.id, "order_line": line})
        with self.assertRaises(UserError, msg="Bestellweg fehlt"):
            order.action_confirm()
        order.bc_channel = "phone"
        with self.assertRaises(UserError, msg="Besteller fehlt"):
            order.action_confirm()
        order.bc_orderer_id = self.contact_invoice
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        # Telefonbestellung über die Aktion: Bestellweg und Annahme werden vorbelegt
        phone = Sale.with_context(default_bc_channel="phone").create({
            "partner_id": self.customer_invoice.id, "order_line": line, "bc_orderer_name": "Herr Anrufer",
        })
        self.assertEqual(phone.bc_channel, "phone")
        self.assertEqual(phone.bc_received_by, self.user_bc)
        self.assertTrue(phone.bc_received_date)
        self.assertEqual(phone.picking_policy, "one", "Blistercenter: Komplettlieferung als Standard")
        phone.action_confirm()
        action = self.customer_invoice.with_user(self.user_bc).action_bc_new_phone_order()
        self.assertEqual(action["context"]["default_bc_channel"], "phone")
        self.assertEqual(action["context"]["default_partner_id"], self.customer_invoice.id)

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
        self.assertEqual(order.bc_channel, "shop", "Shop-Bestellung wird gekennzeichnet")
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

    def test_mto_route_forbidden(self):
        mto = self.env.ref("stock.route_warehouse0_mto")
        mto.active = True
        self.product.route_ids = [Command.link(mto.id)]
        order = self._order(self.customer_invoice, confirm=False)
        with self.assertRaises(UserError):
            order.action_confirm()
        self.product.route_ids = [Command.unlink(mto.id)]
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_prepayment_downpayment_draft(self):
        mails_before = self.env["mail.mail"].search_count([])
        order = self._order(self.customer_prepay)
        invoice = order.invoice_ids
        self.assertEqual(len(invoice), 1)
        self.assertEqual(invoice.state, "draft", "Anzahlungsrechnung bleibt Entwurf")
        self.assertTrue(invoice.invoice_line_ids.sale_line_ids.is_downpayment)
        self.assertAlmostEqual(invoice.amount_total, order.amount_total)
        self.assertTrue(invoice.activity_ids.filtered(lambda a: a.user_id == self.user_accounting))
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before, "Keine E-Mail")
        self.assertEqual(order.bc_state, "awaiting_payment")
        with self.assertRaises(ValidationError):
            self._collect(order)
        invoice.action_post()
        self.assertFalse(order.bc_is_paid, "Gebucht, aber nicht bezahlt")
        self._register_payment(invoice)
        self.assertTrue(order.bc_is_paid)
        self.assertEqual(order.bc_state, "to_procure")
        collective = self._collect(order)
        self.assertEqual(collective.line_ids.qty, 5.0)
        self.assertEqual(order.bc_state, "procuring")
        # Rechnungskunden bekommen keine Anzahlungsrechnung
        self.assertFalse(self._order(self.customer_invoice).invoice_ids)

    def test_payment_block_prevents_collective(self):
        order = self._order(self.customer_invoice)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        with self.assertRaises(ValidationError):
            self._collect(order)

    def test_only_lot_tracked_products_with_expiry(self):
        self.product.use_expiration_date = False
        order = self._order(self.customer_invoice)
        with self.assertRaises(ValidationError):
            self._collect(order)
        self.product.use_expiration_date = True
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
        self.assertEqual(purchase.picking_type_id, self.config.receipt_type_id, "Eingang über Quarantäne")
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

    def test_purchase_must_use_quarantine_receipt(self):
        collective = self._collect(self._order(self.customer_invoice))
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.picking_type_id = self.warehouse.in_type_id
        with self.assertRaises(UserError):
            purchase.button_confirm()
        purchase.picking_type_id = self.config.receipt_type_id
        purchase.button_confirm()
        self.assertEqual(purchase.picking_ids.location_dest_id, self.quarantine)

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


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterStock(BlistercenterCommon):

    def test_full_flow_fixed_allocation(self):
        """Bestellung → Sammelbestellung → Quarantäne → VP-Freigabe → Ware fest je Kunde → Versand."""
        other_lot = self._stock(50, "CH-FREI")
        order_a = self._order(self.customer_invoice, qty=3)
        order_b = self._order(self.customer_invoice, qty=5)
        delivery_a, delivery_b = self._delivery(order_a), self._delivery(order_b)
        self.assertFalse(delivery_a.move_line_ids, "Lieferung wartet auf die Sammelbestellung, kein freier Bestand")
        self.assertTrue(delivery_a.move_ids.bc_wait_collective)
        purchases_before = self.env["purchase.order"].search_count([])
        self.assertEqual(self.env["purchase.order"].search_count([]), purchases_before, "Keine automatische Bestellung")

        collective = self._collect(order_a | order_b)
        receipt, release = self._receive(collective, "CH-NEU")
        self.assertEqual(receipt.location_dest_id, self.quarantine)
        self.assertEqual(collective.state, "done")
        self.assertEqual(len(release), 1)
        self.assertEqual(release.bc_kind, "release")
        self.assertEqual(release.state, "assigned")
        self.assertEqual(release.move_ids.move_dest_ids, (delivery_a | delivery_b).move_ids)
        self.assertTrue(release.activity_ids.filtered(lambda a: a.user_id == self.user_approver))
        self.assertFalse(delivery_a.move_line_ids, "Vor der Freigabe nichts reserviert")

        with self.assertRaises(UserError, msg="Nur die VP gibt frei"):
            release.with_user(self.user_bc2).button_validate()
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")
        self.assertEqual(release.bc_validated_by, self.user_approver)

        for delivery, qty in ((delivery_a, 3), (delivery_b, 5)):
            self.assertEqual(delivery.state, "assigned")
            self.assertEqual(delivery.move_line_ids.lot_id.name, "CH-NEU", "Nur Ware aus der eigenen Sammelbestellung")
            self.assertEqual(sum(delivery.move_line_ids.mapped("quantity")), qty)
        self.assertNotIn(other_lot, (delivery_a | delivery_b).move_line_ids.lot_id)

        with self.assertRaises(UserError, msg="Erst kommissionieren"):
            delivery_a.with_user(self.user_bc2).button_validate()
        delivery_a.with_user(self.user_bc).action_bc_mark_picked()
        self.assertEqual(delivery_a.bc_picked_by, self.user_bc)
        with self.assertRaises(UserError, msg="Endkontrolle durch zweite Person"):
            delivery_a.with_user(self.user_bc).button_validate()
        delivery_a.with_user(self.user_bc2).button_validate()
        self.assertEqual(delivery_a.state, "done")
        self.assertEqual(delivery_a.bc_picked_by, self.user_bc, "Kommissionierer bleibt dokumentiert")
        self.assertEqual(delivery_a.bc_validated_by, self.user_bc2)
        self.assertEqual(order_a.bc_state, "done")

    def test_release_four_eyes(self):
        order = self._order(self.customer_invoice, qty=2)
        collective = self._collect(order)
        _receipt, release = self._receive(collective, user=self.user_approver)
        with self.assertRaises(UserError, msg="Wer gebucht hat, gibt nicht frei"):
            release.with_user(self.user_approver).button_validate()
        release.with_user(self.user_approver2).button_validate()
        self.assertEqual(release.state, "done")

    def test_quarantine_exit_only_via_release(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-Q")
        lot = release.move_line_ids.lot_id
        for user in (self.user_bc, self.user_approver):
            move = self.env["stock.move"].create({
                "product_id": self.product.id, "product_uom_qty": 1, "uom_id": self.product.uom_id.id,
                "location_id": self.quarantine.id, "location_dest_id": self.stock_location.id,
                "picking_type_id": self.warehouse.int_type_id.id,
            })
            move._action_confirm()
            move.move_line_ids.unlink()
            self.env["stock.move.line"].create({
                "move_id": move.id, "product_id": self.product.id, "lot_id": lot.id, "quantity": 1,
                "location_id": self.quarantine.id, "location_dest_id": self.stock_location.id,
            })
            move.picked = True
            with self.assertRaises(UserError):
                move.with_user(user)._action_done()

    def test_short_dated_needs_vp(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-KURZ", days=40)
        self.assertTrue(release.bc_short_open)
        self.assertEqual(release.bc_short_lot_ids.name, "CH-KURZ")
        with self.assertRaises(UserError, msg="Kurzläufer ohne Zusatzfreigabe"):
            release.with_user(self.user_approver).button_validate()
        with self.assertRaises(AccessError):
            release.with_user(self.user_bc)._bc_approve_short("Kunde einverstanden")
        with self.assertRaises(UserError, msg="Begründung Pflicht"):
            release.with_user(self.user_approver)._bc_approve_short(" ")
        self.env["blistercenter.short.wizard"].with_user(self.user_approver).create({
            "picking_id": release.id, "reason": "Kunde akzeptiert kurze Restlaufzeit",
        }).action_apply()
        self.assertFalse(release.bc_short_open)
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")

        delivery = self._delivery(order)
        self.assertTrue(delivery.bc_short_open, "Beim Versand erneut freigeben")
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaises(UserError):
            delivery.with_user(self.user_bc2).button_validate()
        delivery.with_user(self.user_approver)._bc_approve_short("Abgestimmt mit Frau Muster")
        delivery.with_user(self.user_bc2).button_validate()
        self.assertEqual(delivery.state, "done")

    def test_short_dated_limit_configurable(self):
        self.config.short_shelf_months = 1
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-60", days=60)
        self.assertFalse(release.bc_short_open, "60 Tage liegen über 1 Monat")
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")

    def test_missing_expiry_blocks_release(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-OHNE")
        release.move_line_ids.lot_id.expiration_date = False
        with self.assertRaises(UserError):
            release.with_user(self.user_approver).button_validate()

    def test_blocked_lot_not_released(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-SPERRE")
        release.move_line_ids.lot_id.write({"bc_blocked": True, "bc_block_reason": "Transportschaden"})
        with self.assertRaises(UserError):
            release.with_user(self.user_approver).button_validate()

    def test_change_after_picking_resets(self):
        self._stock(10, "CH-A")
        lot_b = self._stock(10, "CH-B", days=500)
        order = self._order(self.customer_invoice, qty=2)
        order.order_line.action_bc_from_stock()
        delivery = self._delivery(order)
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        self.assertTrue(delivery.bc_picked_by)
        delivery.move_line_ids.lot_id = lot_b
        self.assertFalse(delivery.bc_picked_by, "Änderung nach dem Kommissionieren setzt zurück")
        with self.assertRaises(UserError):
            delivery.with_user(self.user_bc2).button_validate()
        self._ship(delivery)
        self.assertEqual(delivery.state, "done")

    def test_partial_delivery_needs_approval(self):
        self._stock(10, "CH-P")
        order = self._order(self.customer_invoice, qty=4)
        order.order_line.action_bc_from_stock()
        delivery = self._delivery(order)
        delivery.move_line_ids.quantity = 2
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaises(UserError, msg="Teillieferung ohne Rücksprache"):
            delivery.with_user(self.user_bc2)._action_done()
        with self.assertRaises(UserError, msg="Vermerk Pflicht"):
            self.env["blistercenter.partial.wizard"].with_user(self.user_bc).create({
                "order_id": order.id, "note": " "}).action_apply()
        self.env["blistercenter.partial.wizard"].with_user(self.user_bc).create({
            "order_id": order.id, "note": "Telefonat mit Frau Muster"}).action_apply()
        self.assertTrue(order.bc_partial_allowed)
        self.assertEqual(order.bc_partial_by, self.user_bc)
        self.assertEqual(delivery.move_type, "direct")
        delivery.with_user(self.user_bc2)._action_done()
        self.assertEqual(delivery.state, "done")
        backorder = order.picking_ids.filtered(lambda p: p.backorder_id == delivery)
        self.assertTrue(backorder, "Rest als Teillieferung offen")
        order.action_bc_revoke_partial()
        self.assertFalse(order.bc_partial_allowed)

    def test_from_stock_option(self):
        lot = self._stock(10, "CH-LAGER")
        order = self._order(self.customer_invoice, qty=3)
        line = order.order_line
        self.assertEqual(line.bc_qty_open, 3)
        line.with_user(self.user_bc).action_bc_from_stock()
        self.assertTrue(line.bc_from_stock)
        self.assertEqual(line.bc_qty_open, 0)
        delivery = self._delivery(order)
        self.assertEqual(delivery.move_line_ids.lot_id, lot)
        with self.assertRaises(ValidationError):
            collective = self.env["blistercenter.collective.order"].create({"supplier_id": self.supplier.id})
            self.env["blistercenter.collective.order.line"].create({"order_id": collective.id, "sale_line_id": line.id, "qty": 1})
        # Schon gebündelt: nicht mehr aus Lagerbestand
        other = self._order(self.customer_invoice, qty=2)
        self._collect(other)
        with self.assertRaises(UserError):
            other.order_line.action_bc_from_stock()

    def test_delivery_payment_block_and_prepayment(self):
        self._stock(20)
        order = self._order(self.customer_invoice, qty=2)
        order.order_line.action_bc_from_stock()
        picking = self._delivery(order)
        picking.with_user(self.user_bc).action_bc_mark_picked()
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        with self.assertRaises(UserError):
            picking.with_user(self.user_bc2).button_validate()
        self.customer_invoice.write({"bc_payment_block": False})
        picking.with_user(self.user_bc2).button_validate()
        self.assertEqual(picking.state, "done")
        self.assertEqual(order.bc_state, "done")

        order_prepay = self._order(self.customer_prepay, qty=2)
        order_prepay.order_line.filtered(lambda sol: not sol.is_downpayment).action_bc_from_stock()
        picking = self._delivery(order_prepay)
        picking.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaises(UserError):
            picking.with_user(self.user_bc2).button_validate()
        self._pay(order_prepay)
        picking.with_user(self.user_bc2).button_validate()
        self.assertEqual(picking.state, "done")

    def test_blocked_lot_not_shipped(self):
        lot = self._stock(5, "CH-GESPERRT")
        order = self._order(self.customer_invoice, qty=2)
        order.order_line.action_bc_from_stock()
        picking = self._delivery(order)
        self.assertEqual(picking.move_line_ids.lot_id, lot)
        picking.with_user(self.user_bc).action_bc_mark_picked()
        lot.write({"bc_blocked": True, "bc_block_reason": "Rückruf"})
        with self.assertRaises(UserError):
            picking.with_user(self.user_bc2).button_validate()
        lot.with_user(self.user_approver).write({"bc_blocked": False})
        picking.with_user(self.user_bc2).button_validate()
        self.assertEqual(picking.state, "done")

    def test_recall_evaluation(self):
        lot = self._stock(10, "CH-RUECKRUF")
        order = self._order(self.customer_invoice, qty=3)
        order.order_line.action_bc_from_stock()
        picking = self._delivery(order)
        self._ship(picking)
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

    def test_portal_access_and_warning(self):
        self.assertTrue(self.customer_invoice.bc_portal_warning, "Shop frei, aber kein Portalzugang")
        action = self.customer_invoice.with_user(self.user_bc).action_bc_portal_access()
        self.assertEqual(action["res_model"], "portal.wizard")
        self.contact_invoice.email = "muster@example.com"
        wizard = self.env["portal.wizard"].browse(action["res_id"])
        wizard.user_ids.filtered(lambda u: u.partner_id == self.contact_invoice).with_context(
            mail_notrack=True).sudo()._create_user()
        self.customer_invoice.invalidate_recordset()
        self.assertEqual(self.customer_invoice.bc_portal_user_count, 1)
        self.assertFalse(self.customer_invoice.bc_portal_warning)


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterOverview(BlistercenterCommon):

    def test_overview_counts(self):
        tiles = {t.key: t for t in self.env["blistercenter.overview"].with_user(self.user_bc).search([])}
        self.assertEqual(len(tiles), 12)
        before = {k: t.count for k, t in tiles.items()}
        self._order(self.customer_prepay)
        self._order(self.customer_invoice, confirm=False)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Test"})
        self._stock(3, "CH-KURZ-BESTAND", days=20)
        self.env.invalidate_all()
        tiles = {t.key: t for t in self.env["blistercenter.overview"].with_user(self.user_bc).search([])}
        self.assertEqual(tiles["awaiting_payment"].count, before["awaiting_payment"] + 1)
        self.assertEqual(tiles["downpayments_to_post"].count, before["downpayments_to_post"] + 1)
        self.assertEqual(tiles["new_orders"].count, before["new_orders"] + 1)
        self.assertEqual(tiles["payment_blocks"].count, before["payment_blocks"] + 1)
        self.assertEqual(tiles["short_dated"].count, before["short_dated"] + 1)
        for tile in tiles.values():
            action = tile.action_open()
            self.env[action["res_model"]].with_user(self.user_bc).search_count(action["domain"])

    def test_menus_and_actions_load(self):
        """Alle Menüs der App lassen sich für die Rollen laden."""
        root = self.env.ref("blistercenter.menu_bc_root")
        for user, expect_settings in ((self.user_bc, False), (self.user_manager, True)):
            visible = self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()
            self.assertIn(root.id, visible)
            self.assertIn(self.env.ref("blistercenter.menu_bc_collective_list").id, visible)
            self.assertIn(self.env.ref("blistercenter.menu_bc_phone_order").id, visible)
            self.assertEqual(self.env.ref("blistercenter.menu_bc_settings").id in visible, expect_settings)
        visible = self.env["ir.ui.menu"].with_user(self.user_sales)._visible_menu_ids()
        self.assertNotIn(root.id, visible, "Ohne Blistercenter-Rolle keine App")
        for xmlid in (
            "action_bc_customers", "action_bc_suppliers", "action_bc_supplierinfo", "action_bc_sale_orders",
            "action_bc_sale_lines", "action_bc_collective_orders", "action_bc_purchase_orders",
            "action_bc_receipts", "action_bc_release", "action_bc_picking", "action_bc_check", "action_bc_partial",
            "action_bc_deliveries", "action_bc_lots", "action_bc_lots_expiry", "action_bc_lots_blocked",
            "action_bc_short_quants", "action_bc_overview", "action_bc_phone_order",
        ):
            action = self.env["ir.actions.act_window"].with_user(self.user_bc)._for_xml_id(f"blistercenter.{xmlid}")
            model = self.env[action["res_model"]].with_user(self.user_bc)
            model.search_count(eval(action.get("domain") or "[]") if isinstance(action.get("domain"), str) else action.get("domain") or [])

    def test_setup_idempotent(self):
        quarantine, receipt, release = (
            self.config.quarantine_location_id, self.config.receipt_type_id, self.config.release_type_id)
        self.config.with_user(self.user_manager).action_setup_stock()
        self.env["blistercenter.config"]._setup_all()
        self.assertEqual(
            (self.config.quarantine_location_id, self.config.receipt_type_id, self.config.release_type_id),
            (quarantine, receipt, release))
        with self.assertRaises(AccessError):
            self.config.with_user(self.user_bc).action_setup_stock()
