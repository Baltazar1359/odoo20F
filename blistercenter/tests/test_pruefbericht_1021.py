"""Prüfbericht 21.10.2026 (Blistercenter 1.5.1): Befunde 1–7, jeweils mit Negativtest.

1/2 Verfall aus dem Scan bleibt erhalten, widersprüchliche Daten werden abgelehnt (R49, R50)
3   Rechnung nur über die Menge der jeweiligen Lieferung (R51)
4   Preis = HAP bei der Bestätigung (R53)
5   Scan-Fenster nur einmal übernehmen
6   Bestellschein nur einmal absenden (R54)
7   Storno der Herstellerbestellung hält die Sammelbestellung aktuell (R52)
"""
import re
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..tools import gs1
from .test_blistercenter import BlistercenterCommon

GS = "\x1d"


@tagged("post_install", "-at_install", "blistercenter")
class TestPruefbericht1021(BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.default_code = "12345678"
        cls.gtin = gs1.pzn_to_gtin("12345678")

    # Hilfen ---------------------------------------------------------------
    def _open_receipt(self, customer, qty=3):
        order = self._order(customer, qty=qty)
        collective = self._collect(order)
        collective.action_create_purchase()
        collective.purchase_id.button_confirm()
        receipt = collective.purchase_id.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        return order, receipt

    def _fill(self, receipt, lot_name, days):
        receipt.move_ids.move_line_ids.unlink()
        receipt.move_ids._action_assign()
        for move in receipt.move_ids:
            move.move_line_ids.write({
                "lot_name": lot_name, "quantity": move.product_uom_qty,
                "expiration_date": fields.Datetime.now() + timedelta(days=days),
            })
        receipt.move_ids.picked = True

    def _code(self, lot, expiry, serial):
        return "01%s17%s10%s%s21%s" % (self.gtin, expiry.strftime("%y%m%d"), lot, GS, serial)

    def _scan_form(self, receipt):
        return Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))

    # ------------------------------------------------------------------
    # Befund 1: Verfall laut Packung bleibt erhalten
    # ------------------------------------------------------------------
    def test_scanned_expiry_kept_for_known_lot_until_vp_task(self):
        self.config.vp_user_id = self.user_approver
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-1021-A", 700)
        first.with_user(self.user_bc).button_validate()
        lot = first.move_line_ids.lot_id
        known = lot.expiration_date

        _order, second = self._open_receipt(self.customer_debit, qty=1)
        scanned = fields.Date.today() + timedelta(days=300)
        form = self._scan_form(second)
        form.scan = self._code("CH-1021-A", scanned, "S1")
        form.save().action_apply()
        line = second.move_line_ids.filtered("quantity")
        self.assertEqual(line.lot_id, lot, "Vorhandene Charge verwendet")
        self.assertEqual(line.bc_scanned_expiry, scanned, "Gescanntes Datum festgehalten")
        self.assertIn("Verfall eingegeben", second.bc_lot_warning)
        self.assertIn(scanned.strftime("%d.%m.%Y"), second.bc_lot_warning)

        second.move_ids.picked = True
        second.with_user(self.user_bc).button_validate()
        self.assertEqual(second.state, "done")
        notes = second.message_ids.filtered(lambda m: "Hinweise zu Chargen" in str(m.body))
        self.assertTrue(notes, "Vermerk im Verlauf nach dem Buchen")
        self.assertIn(scanned.strftime("%d.%m.%Y"), str(notes[:1].body))
        self.assertTrue(second.activity_ids.filtered(lambda a: a.user_id == self.user_approver), "Aufgabe für die VP")
        self.assertEqual(lot.expiration_date, known, "Chargenstamm unverändert (R47)")
        self.assertEqual(line.bc_scanned_expiry, scanned, "Nachweis bleibt nach dem Buchen")

    def test_scanned_same_expiry_gives_no_note(self):
        """Gegenprobe: gleiches Datum → kein Hinweis, keine Aufgabe."""
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-1021-B", 700)
        first.with_user(self.user_bc).button_validate()
        known = first.move_line_ids.lot_id.expiration_date.date()
        _order, second = self._open_receipt(self.customer_debit, qty=1)
        form = self._scan_form(second)
        form.scan = self._code("CH-1021-B", known, "S1")
        form.save().action_apply()
        self.assertFalse(second.bc_lot_warning)
        second.move_ids.picked = True
        second.with_user(self.user_bc).button_validate()
        self.assertEqual(second.state, "done")
        self.assertFalse(second.message_ids.filtered(lambda m: "Hinweise zu Chargen" in str(m.body)))

    def test_scanned_expiry_not_writable_directly(self):
        _order, receipt = self._open_receipt(self.customer_invoice)
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-C", fields.Date.today() + timedelta(days=500), "S1")
        form.save().action_apply()
        line = receipt.move_line_ids.filtered("quantity")
        with self.assertRaises(AccessError):
            line.with_user(self.user_bc).write({"bc_scanned_expiry": fields.Date.today() + timedelta(days=900)})

    # ------------------------------------------------------------------
    # Befund 2: widersprüchliches Verfallsdatum derselben Charge
    # ------------------------------------------------------------------
    def test_second_scan_with_other_expiry_rejected(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        first_date = fields.Date.today() + timedelta(days=400)
        other_date = fields.Date.today() + timedelta(days=700)
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-D", first_date, "S1")
        self.assertEqual(form.message_type, "ok")
        form.scan = self._code("CH-1021-D", other_date, "S2")
        self.assertEqual(form.message_type, "error")
        self.assertIn("weicht", form.message)
        self.assertEqual(len(form.line_ids), 1)
        with form.line_ids.edit(0) as line:
            self.assertEqual(line.qty, 1, "Abweichende Packung nicht gezählt")
            self.assertEqual(line.expiration_date, first_date, "Erstes Datum bleibt")
        # Gegenprobe: gleiches Datum zählt weiter
        form.scan = self._code("CH-1021-D", first_date, "S3")
        self.assertEqual(form.message_type, "ok")
        with form.line_ids.edit(0) as line:
            self.assertEqual(line.qty, 2)

    def test_apply_rejects_two_dates_for_one_lot(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        Scan = self.env["blistercenter.receipt.scan"].with_user(self.user_bc)
        wizard = Scan.create({"picking_id": receipt.id, "line_ids": [
            Command.create({"product_id": self.product.id, "lot_name": "CH-1021-E", "qty": 1,
                            "expiration_date": fields.Date.today() + timedelta(days=400)}),
            Command.create({"product_id": self.product.id, "lot_name": "CH-1021-E", "qty": 1,
                            "expiration_date": fields.Date.today() + timedelta(days=700)}),
        ]})
        with self.assertRaisesRegex(UserError, "zwei Verfallsdaten"):
            wizard.action_apply()

    def test_apply_rejects_other_date_than_already_in_receipt(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-F", fields.Date.today() + timedelta(days=400), "S1")
        form.save().action_apply()
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-F", fields.Date.today() + timedelta(days=700), "S2")
        wizard = form.save()
        with self.assertRaisesRegex(UserError, "schon mit Verfall"):
            wizard.action_apply()
        self.assertEqual(sum(receipt.move_line_ids.mapped("quantity")), 1, "Menge unverändert")
        # Gegenprobe: zweites Fenster mit gleichem Datum addiert
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-F", fields.Date.today() + timedelta(days=400), "S3")
        form.save().action_apply()
        self.assertEqual(sum(receipt.move_line_ids.mapped("quantity")), 2)

    def test_scan_onto_manually_chosen_known_lot_only_warns(self):
        """Review 1.5.1: Von Hand gewählte vorhandene Charge + Scan mit anderem Datum → Hinweis, keine Sperre."""
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-1021-H", 700)
        first.with_user(self.user_bc).button_validate()
        lot = first.move_line_ids.lot_id
        _order, second = self._open_receipt(self.customer_debit, qty=2)
        second.move_ids.move_line_ids.unlink()
        self.env["stock.move.line"].create({
            "move_id": second.move_ids.id, "picking_id": second.id, "product_id": self.product.id,
            "location_id": second.location_id.id, "location_dest_id": second.move_ids.location_dest_id.id,
            "lot_id": lot.id, "quantity": 1,
        })
        scanned = fields.Date.today() + timedelta(days=200)
        form = self._scan_form(second)
        form.scan = self._code("CH-1021-H", scanned, "S1")
        form.save().action_apply()
        line = second.move_line_ids.filtered(lambda ml: ml.lot_id == lot)
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.bc_scanned_expiry, scanned)
        self.assertIn("Verfall eingegeben", second.bc_lot_warning)

    # ------------------------------------------------------------------
    # Befund 5: Scan-Fenster nur einmal übernehmen
    # ------------------------------------------------------------------
    def test_apply_twice_counts_once(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._scan_form(receipt)
        form.scan = self._code("CH-1021-G", fields.Date.today() + timedelta(days=500), "S1")
        wizard = form.save()
        wizard.action_apply()
        wizard.action_apply()
        self.assertEqual(sum(receipt.move_line_ids.mapped("quantity")), 1)
        self.assertTrue(wizard.applied)
        self.assertEqual(len(receipt.message_ids.filtered(lambda m: "Erfasst:" in str(m.body))), 1)

    # ------------------------------------------------------------------
    # Befund 3: Rechnung je Lieferung
    # ------------------------------------------------------------------
    def _two_deliveries_without_invoice(self, qty_first=2, qty_second=3):
        """Zwei gebuchte Lieferungen eines Auftrags, noch ohne Rechnung (Regel kurz aus)."""
        self._stock(10, "CH-1021-RE")
        order = self._order(self.customer_debit, qty=qty_first + qty_second)
        order.order_line.action_bc_from_stock()
        self.env["blistercenter.partial.wizard"].with_user(self.user_bc).create({
            "order_id": order.id, "note": "Telefonat"}).action_apply()
        self.config.rule_invoice_per_delivery = False
        pickings = self.env["stock.picking"]
        for qty in (qty_first, qty_second):
            delivery = order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel"))
            delivery.action_assign()
            delivery.move_line_ids.quantity = qty
            delivery.with_user(self.user_bc).action_bc_mark_picked()
            delivery.with_user(self.user_bc2)._action_done()
            pickings |= delivery
        self.assertFalse(order.invoice_ids)
        self.config.rule_invoice_per_delivery = True
        return order, pickings

    def test_two_deliveries_together_get_own_invoices(self):
        """Gleiche Lage wie zwei zusammen gebuchte Lieferungen: beide Mengen sind schon geliefert."""
        order, (first, second) = self._two_deliveries_without_invoice(2, 3)
        (first | second)._bc_invoice_after_delivery()
        self.assertEqual(len(first.bc_invoice_ids), 1)
        self.assertEqual(len(second.bc_invoice_ids), 1)
        self.assertEqual(first.bc_invoice_ids.invoice_line_ids.quantity, 2, "Nur die Menge der ersten Lieferung")
        self.assertEqual(second.bc_invoice_ids.invoice_line_ids.quantity, 3, "Nur die Menge der zweiten Lieferung")
        self.assertEqual(order.order_line.qty_invoiced, 5)
        # Wiederholter Aufruf legt keine zweite Rechnung an
        (first | second)._bc_invoice_after_delivery()
        self.assertEqual(len(order.invoice_ids), 2)

    def test_older_uninvoiced_quantity_stays_out(self):
        """Rechnungsentwurf der ersten Lieferung gelöscht: zweite Rechnung enthält nur die zweite Menge."""
        order, (first, second) = self._two_deliveries_without_invoice(2, 3)
        second._bc_invoice_after_delivery()
        self.assertEqual(second.bc_invoice_ids.invoice_line_ids.quantity, 3)
        self.assertEqual(order.order_line.qty_to_invoice, 2, "Menge der ersten Lieferung bleibt offen")
        self.assertTrue(order.message_ids.filtered(lambda m: "ohne Rechnung" in str(m.body)),
                        "Hinweis auf gelieferte Menge ohne Rechnung")
        self.assertFalse(first.bc_invoice_ids)

    def test_rpc_context_cannot_limit_invoice(self):
        """Negativtest: Kontextwert von außen (JSON) ändert die Rechnung nicht."""
        order, (first, _second) = self._two_deliveries_without_invoice(2, 3)
        line = order.order_line
        invoice = order.with_context(bc_invoice_qty={str(line.id): 1, line.id: 1})._create_invoices()
        self.assertEqual(invoice.invoice_line_ids.quantity, 5, "Normale Odoo-Fakturierung ohne Begrenzung")

    # ------------------------------------------------------------------
    # Befund 7: Storno der Herstellerbestellung
    # ------------------------------------------------------------------
    def test_direct_purchase_cancel_cancels_collective(self):
        order = self._order(self.customer_invoice, qty=4)
        collective = self._collect(order)
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.button_confirm()
        self.assertEqual(order.order_line.bc_qty_open, 0)
        purchase.button_cancel()
        self.assertEqual(purchase.state, "cancel")
        self.assertEqual(collective.state, "cancel", "Sammelbestellung mit storniert")
        self.assertEqual(order.order_line.bc_qty_open, 4, "Kundenmenge wieder unter „Zu bestellen“")
        self.assertTrue(collective.message_ids.filtered(lambda m: "mit storniert" in str(m.body)))

    def test_purchase_cancel_after_receipt_blocked(self):
        order = self._order(self.customer_invoice, qty=4)
        collective = self._collect(order)
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.button_confirm()
        receipt = purchase.picking_ids
        self._fill(receipt, "CH-1021-PO", 700)
        receipt.move_line_ids.quantity = 2
        receipt.with_user(self.user_bc)._action_done()  # Teilmenge, Rest als Rückstand
        self.assertEqual(receipt.state, "done")
        self.assertTrue(purchase.picking_ids - receipt, "Rückstand angelegt")
        with self.assertRaisesRegex(UserError, "teilweise eingegangen"):
            purchase.button_cancel()
        self.assertNotEqual(purchase.state, "cancel")
        self.assertEqual(collective.state, "ordered")

    def test_cancelled_purchase_cannot_be_revived(self):
        """Review 1.5.1: Stornierte Herstellerbestellung nicht zurück in den Entwurf (sonst doppelte Menge)."""
        order = self._order(self.customer_invoice, qty=2)
        collective = self._collect(order)
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.button_cancel()
        with self.assertRaisesRegex(UserError, "stornierten Sammelbestellung"):
            purchase.button_draft()
        # Gegenprobe: normale Herstellerbestellung ohne Sammelbestellung bleibt frei
        plain = self.env["purchase.order"].create({"partner_id": self.supplier.id, "order_line": [
            Command.create({"product_id": self.product.id, "product_qty": 1})]})
        plain.button_cancel()
        plain.button_draft()
        self.assertEqual(plain.state, "draft")

    def test_cancel_from_collective_still_works(self):
        """Gegenprobe: Storno aus der Sammelbestellung (Anfrage) wie bisher, ohne Doppelvermerk."""
        order = self._order(self.customer_invoice, qty=2)
        collective = self._collect(order)
        collective.action_create_purchase()
        collective.action_cancel()
        self.assertEqual(collective.purchase_id.state, "cancel")
        self.assertEqual(collective.state, "cancel")
        self.assertFalse(collective.message_ids.filtered(lambda m: "mit storniert" in str(m.body)))
        self.assertEqual(order.order_line.bc_qty_open, 2)

    # ------------------------------------------------------------------
    # Befund 4: Preis = HAP
    # ------------------------------------------------------------------
    def test_price_must_equal_hap(self):
        pricelist = self.env["product.pricelist"].create({
            "name": "Sonderpreis (Test)", "currency_id": self.company.currency_id.id,
            "item_ids": [Command.create({"applied_on": "0_product_variant", "product_id": self.product.id,
                                         "compute_price": "fixed", "fixed_price": 15.0})],
        })
        self.customer_invoice.property_product_pricelist = pricelist
        order = self._order(self.customer_invoice, confirm=False)
        self.assertEqual(order.order_line.price_unit, 15.0)
        with self.assertRaisesRegex(UserError, "HAP"):
            order.action_confirm()
        # Gegenprobe: Preis von Hand auf den HAP → Bestätigung geht
        order.order_line.price_unit = self.product.lst_price
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_foreign_currency_rejected(self):
        other = self.env["res.currency"].with_context(active_test=False).search(
            [("id", "!=", self.company.currency_id.id)], limit=1)
        other.active = True
        pricelist = self.env["product.pricelist"].create({"name": "Fremdwährung (Test)", "currency_id": other.id})
        self.customer_invoice.property_product_pricelist = pricelist
        order = self._order(self.customer_invoice, confirm=False)
        self.assertEqual(order.currency_id, other)
        with self.assertRaisesRegex(UserError, self.company.currency_id.name):
            order.action_confirm()

    # ------------------------------------------------------------------
    # Mehrere Firmen: offene Posten nur der eigenen Firma
    # ------------------------------------------------------------------
    def test_overdue_only_own_company(self):
        self._overdue_invoice(self.customer_invoice, amount=80.0)
        other_company = self.env["res.company"].create({"name": "Andere Firma (Test)"})
        self.env.invalidate_all()
        self.assertEqual(self.customer_invoice.bc_overdue_count, 1)
        self.assertEqual(self.customer_invoice.with_company(other_company).bc_overdue_count, 0)


@tagged("post_install", "-at_install", "blistercenter")
class TestPruefbericht1021Portal(HttpCase):
    """Befund 6: Online-Bestellschein nur einmal absenden; Summe im Formular."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["blistercenter.config"]._get(env.company)._bc_check_setup()
        term = env.ref("blistercenter.payment_term_14days")
        cls.customer = env["res.partner"].create({
            "name": "Portal-Blistercenter 1021 (Test)", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "bc_discount": "2", "property_payment_term_id": term.id})
        contact = env["res.partner"].create({"name": "Paula Portal", "parent_id": cls.customer.id})
        cls.user = new_test_user(env, login="bc_portal_1021", groups="base.group_portal", partner_id=contact.id)
        cls.product = env["product.product"].create({
            "name": "Sortiment 1021 10 St", "default_code": "22222222", "list_price": 100.0,
            "sale_ok": True, "bc_shop_product": True, "invoice_policy": "delivery"})

    def _form(self):
        self.authenticate(self.user.login, self.user.login)
        html = self.url_open("/my/bestellschein").text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        token = re.search(r'name="bc_submit_token" value="([0-9a-f]{32})"', html)
        return html, csrf, token and token.group(1)

    def _orders(self):
        return self.env["sale.order"].search([("partner_id", "child_of", self.customer.id)])

    def test_same_submit_twice_gives_one_order(self):
        html, csrf, token = self._form()
        self.assertTrue(token, "Bestellschein trägt eine Einmal-Kennung")
        self.assertIn('data-bc-price="100.0"', html)
        self.assertIn('data-bc-discount="2.0"', html)
        self.assertIn("o_bc_sum_net", html, "Summe vor dem Absenden")
        data = {"csrf_token": csrf, "bc_submit_token": token, "qty_%s" % self.product.id: "3"}
        first = self.url_open("/my/bestellschein", data=data)
        second = self.url_open("/my/bestellschein", data=data)
        orders = self._orders()
        self.assertEqual(len(orders), 1, "Wiederholung legt keine zweite Bestellung an")
        self.assertIn(orders.name, first.text)
        self.assertIn(orders.name, second.text, "Wiederholung zeigt dieselbe Bestellung")
        # Gegenprobe: neuer Bestellschein → neue Bestellung
        _html, csrf, token2 = self._form()
        self.assertNotEqual(token, token2)
        self.url_open("/my/bestellschein", data={
            "csrf_token": csrf, "bc_submit_token": token2, "qty_%s" % self.product.id: "1"})
        self.assertEqual(len(self._orders()), 2)

    def test_token_kept_after_error_and_invalid_token_rejected(self):
        _html, csrf, token = self._form()
        res = self.url_open("/my/bestellschein", data={
            "csrf_token": csrf, "bc_submit_token": token, "qty_%s" % self.product.id: "abc"})
        self.assertIn("ganze Zahl", res.text)
        self.assertIn(token, res.text, "Kennung unverbraucht, Formular kann korrigiert werden")
        res = self.url_open("/my/bestellschein", data={
            "csrf_token": csrf, "bc_submit_token": "nicht-gueltig", "qty_%s" % self.product.id: "1"})
        self.assertIn("abgelaufen", res.text)
        self.assertFalse(self._orders())

    def test_token_unique_in_database(self):
        Order = self.env["sale.order"].sudo()
        token = "a" * 32
        Order._bc_create_from_sheet(self.customer, [(self.product, 1)], "portal", submit_token=token)
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.env.cr.savepoint():
            Order._bc_create_from_sheet(self.customer, [(self.product, 1)], "portal", submit_token=token)
