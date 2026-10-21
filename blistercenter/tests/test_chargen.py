"""Chargen im Wareneingang (Entscheidungen 21.10.2026).

- Dieselbe Charge kommt oft mehrfach vom Hersteller: eine Charge in Odoo, Rückverfolgung über alle Eingänge.
- Abweichendes Verfallsdatum bei bekannter Charge: nur Hinweis (Formular, Verlauf, Aufgabe für die VP).
- Gesperrte Charge im Eingang: Buchung in die Quarantäne mit Hinweis; Freigabe bleibt gesperrt.
- DataMatrix-Scan im Wareneingang.
"""
import os
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from ..tools import gs1
from .test_blistercenter import BlistercenterCommon

GS = "\x1d"


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterLots(BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.default_code = "12345678"
        cls.gtin = gs1.pzn_to_gtin("12345678")

    def _open_receipt(self, customer, qty=3):
        order = self._order(customer, qty=qty)
        collective = self._collect(order)
        collective.action_create_purchase()
        purchase = collective.purchase_id
        purchase.button_confirm()
        receipt = purchase.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
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

    def _release_of(self, receipt):
        return receipt.move_ids.move_dest_ids.picking_id.filtered(lambda p: p.bc_kind == "release")

    def test_same_lot_twice_is_one_lot_and_traceable(self):
        order_a, receipt_a = self._open_receipt(self.customer_invoice, qty=3)
        order_b, receipt_b = self._open_receipt(self.customer_debit, qty=5)
        for receipt in (receipt_a, receipt_b):
            self._fill(receipt, "CH-GLEICH", 700)
            receipt.with_user(self.user_bc).button_validate()
            self._release_of(receipt).with_user(self.user_approver).button_validate()
        lots = self.env["stock.lot"].search([("product_id", "=", self.product.id), ("name", "=", "CH-GLEICH")])
        self.assertEqual(len(lots), 1, "Zweiter Eingang derselben Charge nutzt die vorhandene Charge")
        self.assertFalse(receipt_b.message_ids.filtered(lambda m: "Hinweise zu Chargen" in str(m.body)),
                         "Gleiches Verfallsdatum: kein Hinweis")
        for order in (order_a, order_b):
            delivery = self._delivery(order)
            self.assertEqual(delivery.move_line_ids.lot_id, lots)
            self._ship(delivery)
        wizard = self.env["blistercenter.recall.wizard"].with_user(self.user_bc).create({
            "lot_ids": [Command.set(lots.ids)]})
        pickings = self.env["stock.picking"].search(wizard.action_customers()["domain"])
        self.assertEqual(pickings.partner_id.commercial_partner_id, self.customer_invoice | self.customer_debit,
                         "Rückruf findet alle Kunden dieser Charge")

    def test_expiry_mismatch_only_warns(self):
        self.config.vp_user_id = self.user_approver
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-DATUM", 700)
        first.with_user(self.user_bc).button_validate()
        lot = first.move_line_ids.lot_id
        known = lot.expiration_date

        _order, second = self._open_receipt(self.customer_debit)
        self._fill(second, "CH-DATUM", 400)
        self.assertIn("Verfall eingegeben", second.bc_lot_warning)
        self.assertIn("CH-DATUM", second.bc_lot_warning)
        second.with_user(self.user_bc).button_validate()  # nur Hinweis, keine Sperre
        self.assertEqual(second.state, "done")
        self.assertEqual(second.move_line_ids.lot_id, lot)
        self.assertEqual(lot.expiration_date, known, "Odoo behält das Datum der vorhandenen Charge")
        self.assertTrue(second.message_ids.filtered(lambda m: "Hinweise zu Chargen" in str(m.body)))
        self.assertTrue(second.activity_ids.filtered(lambda a: a.user_id == self.user_approver))
        self.assertFalse(second.bc_lot_warning, "Nach dem Buchen steht der Hinweis im Verlauf")

    def test_blocked_lot_received_into_quarantine_and_stays_blocked(self):
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-RUECK", 700)
        first.with_user(self.user_bc).button_validate()
        lot = first.move_line_ids.lot_id
        lot.with_user(self.user_bc).write({"bc_blocked": True, "bc_block_reason": "Rückruf Hersteller"})

        _order, second = self._open_receipt(self.customer_debit)
        self._fill(second, "CH-RUECK", 700)
        self.assertIn("gesperrt", second.bc_lot_warning)
        second.with_user(self.user_bc).button_validate()
        self.assertEqual(second.state, "done", "Eingang wird dokumentiert")
        self.assertEqual(second.location_dest_id, self.quarantine)
        with self.assertRaises(UserError):
            self._release_of(second).with_user(self.user_approver).button_validate()

    # ------------------------------------------------------------------
    # DataMatrix-Scan
    # ------------------------------------------------------------------
    def _code(self, lot, expiry, serial):
        return "01%s17%s10%s%s21%s" % (self.gtin, expiry.strftime("%y%m%d"), lot, GS, serial)

    def test_scan_counts_packs_per_lot(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        expiry = fields.Date.today() + timedelta(days=700)
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.scan = self._code("CH-SCAN-A", expiry, "S1")
        self.assertEqual(form.message_type, "ok")
        form.scan = self._code("CH-SCAN-A", expiry, "S2")
        form.scan = self._code("CH-SCAN-B", expiry, "S3")
        self.assertEqual(len(form.line_ids), 2)
        wizard = form.save()
        self.assertEqual({(l.lot_name, l.qty) for l in wizard.line_ids}, {("CH-SCAN-A", 2), ("CH-SCAN-B", 1)})
        wizard.action_apply()
        lines = receipt.move_line_ids
        self.assertEqual({(l.lot_name, l.quantity) for l in lines}, {("CH-SCAN-A", 2), ("CH-SCAN-B", 1)})
        receipt.with_user(self.user_bc).button_validate()
        lots = receipt.move_line_ids.lot_id
        self.assertEqual(set(lots.mapped("name")), {"CH-SCAN-A", "CH-SCAN-B"})
        self.assertEqual({l.expiration_date.date() for l in lots}, {expiry})

    def test_scan_rejects_foreign_and_unreadable_codes(self):
        _order, receipt = self._open_receipt(self.customer_invoice)
        form = Form(self.env["blistercenter.receipt.scan"].with_context(default_picking_id=receipt.id))
        form.scan = "kein Code"
        self.assertEqual(form.message_type, "error")
        expiry = fields.Date.today() + timedelta(days=700)
        foreign = gs1.pzn_to_gtin("87654321")
        form.scan = "01%s17%s10CH-X%s21S9" % (foreign, expiry.strftime("%y%m%d"), GS)
        self.assertEqual(form.message_type, "error")
        self.assertIn("87654321", form.message)
        self.assertFalse(form.line_ids)
        wizard = form.save()
        with self.assertRaises(UserError):
            wizard.action_apply()

    def test_scan_marks_known_lot_with_other_expiry(self):
        _order, first = self._open_receipt(self.customer_invoice)
        self._fill(first, "CH-SCAN-D", 700)
        first.with_user(self.user_bc).button_validate()
        _order, second = self._open_receipt(self.customer_debit)
        form = Form(self.env["blistercenter.receipt.scan"].with_context(default_picking_id=second.id))
        form.scan = self._code("CH-SCAN-D", fields.Date.today() + timedelta(days=300), "S1")
        self.assertEqual(form.message_type, "warning")
        wizard = form.save()
        self.assertIn("Verfall", wizard.line_ids.warning)
        wizard.action_apply()
        self.assertEqual(second.move_line_ids.lot_id, first.move_line_ids.lot_id, "Vorhandene Charge verwendet")

    def test_scan_only_open_blistercenter_receipts(self):
        _order, receipt = self._open_receipt(self.customer_invoice)
        self.assertEqual(receipt.action_bc_scan()["res_model"], "blistercenter.receipt.scan")
        self._fill(receipt, "CH-ZU", 700)
        receipt.with_user(self.user_bc).button_validate()
        with self.assertRaises(UserError):
            receipt.action_bc_scan()
        with self.assertRaises(AccessError):
            self.env["blistercenter.receipt.scan"].with_user(self.user_sales).create({"picking_id": receipt.id})

    def test_gs1_copy_matches_greenrx(self):
        here = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "gs1.py")
        other = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                             "plcs_greenrx", "tools", "gs1.py")
        if not os.path.exists(other):
            self.skipTest("plcs_greenrx nicht im Addons-Pfad")
        with open(here, encoding="utf-8") as fh:
            mine = fh.read().split("\n", 2)[2]
        with open(other, encoding="utf-8") as fh:
            theirs = fh.read()
        self.assertEqual(mine, theirs[3:], "blistercenter/tools/gs1.py muss plcs_greenrx/tools/gs1.py entsprechen")
