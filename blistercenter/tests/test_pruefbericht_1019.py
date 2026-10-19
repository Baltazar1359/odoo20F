"""Prüfbericht 19.10.2026 (externes Audit): Blistercenter-Befunde R01–R07 als Regressionstests.

Jeder Fall bildet den Schreibweg des Prüfers nach (direkter Aufruf wie per RPC, eigener Kontext)
und zeigt, dass er jetzt scheitert. Die normalen Abläufe laufen weiter (Gegenproben).
"""
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_blistercenter import BlistercenterCommon


@tagged("post_install", "-at_install", "blistercenter")
class TestPruefbericht1019(BlistercenterCommon):

    def _picked_delivery(self, qty=2, lot="CH-R"):
        self._stock(10, lot)
        order = self._order(self.customer_invoice, qty=qty)
        order.order_line.action_bc_from_stock()
        delivery = self._delivery(order)
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        return order, delivery

    # R01 -----------------------------------------------------------------
    def test_r01_picker_cannot_forge_second_person(self):
        _order, delivery = self._picked_delivery()
        with self.assertRaises(AccessError):
            delivery.with_user(self.user_bc).write({"bc_picked_by": self.user_bc2.id})
        self.assertEqual(delivery.bc_picked_by, self.user_bc)
        with self.assertRaises(UserError, msg="Selbst kommissioniert"):
            delivery.with_user(self.user_bc).button_validate()
        for field in ("bc_validated_by", "bc_picked_date", "bc_validated_date"):
            value = self.user_bc2.id if field.endswith("_by") else fields.Datetime.now()
            with self.assertRaises(AccessError):
                delivery.with_user(self.user_bc).write({field: value})
        # Gegenprobe: zweite Person bestätigt
        delivery.with_user(self.user_bc2).button_validate()
        self.assertEqual(delivery.state, "done")
        self.assertEqual(delivery.bc_validated_by, self.user_bc2)

    # R02 -----------------------------------------------------------------
    def test_r02_rpc_context_cannot_keep_check_after_lot_change(self):
        lot_b = self._stock(10, "CH-R-B", days=500)
        _order, delivery = self._picked_delivery(lot="CH-R-A")
        delivery.move_line_ids.with_user(self.user_bc).with_context(bc_in_done=True).write({"lot_id": lot_b.id})
        self.assertFalse(delivery.bc_picked_by, "Kontextwert aus RPC hebt die Rücksetzung nicht auf")
        with self.assertRaises(UserError):
            delivery.with_user(self.user_bc2).button_validate()

    # R03 -----------------------------------------------------------------
    def test_r03_warehouse_user_cannot_forge_short_approval(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-R-KURZ", days=40)
        lots = release._bc_short_lots()
        self.assertTrue(lots)
        with self.assertRaises(AccessError):
            release.with_user(self.user_bc).write({
                "bc_short_approved_lot_ids": [(6, 0, lots.ids)],
                "bc_short_approved_by": self.user_approver.id,
                "bc_short_reason": "erfunden",
            })
        with self.assertRaises(AccessError):
            release.with_user(self.user_approver).write({"bc_short_approved_lot_ids": [(6, 0, lots.ids)]})
        self.assertTrue(release.bc_short_open)
        # Gegenprobe: geprüfte VP-Aktion
        release.with_user(self.user_approver)._bc_approve_short("Kunde einverstanden")
        self.assertFalse(release.bc_short_open)
        self.assertEqual(release.bc_short_approved_by, self.user_approver)

    # R04 -----------------------------------------------------------------
    def test_r04_customer_flag_does_not_bypass_payment_block(self):
        order = self._order(self.customer_invoice, confirm=False)
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Mahnung"})
        with self.assertRaises(AccessError):
            self.customer_invoice.with_user(self.user_bc).write({"bc_is_customer": False})
        self.assertTrue(self.customer_invoice.bc_is_customer)
        with self.assertRaises(UserError):
            order.action_confirm()
        # Auch wenn die Freigabe-Rolle das Kennzeichen später entfernt, bleibt ein bestätigter
        # Auftrag Blistercenter-Auftrag und seine Lieferung den Regeln unterworfen
        self.customer_invoice.write({"bc_payment_block": False})
        self._stock(10, "CH-R4")
        order.action_confirm()
        order.order_line.action_bc_from_stock()
        self.customer_invoice.with_user(self.user_approver).write({"bc_is_customer": False})
        self.assertTrue(order.bc_is_blistercenter, "Zuordnung bleibt festgehalten")
        delivery = self._delivery(order)
        self.assertEqual(delivery.bc_kind, "delivery")
        self.customer_invoice.write({"bc_payment_block": True, "bc_payment_block_reason": "Mahnung"})
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaises(UserError, msg="Zahlungssperre gilt weiter"):
            delivery.with_user(self.user_bc2).button_validate()

    # R05 -----------------------------------------------------------------
    def test_r05_collective_state_cannot_unlock_ordered_lines(self):
        order = self._order(self.customer_invoice, qty=5)
        collective = self._collect(order)
        collective.action_create_purchase()
        with self.assertRaises(AccessError):
            collective.with_user(self.user_bc).write({"state": "draft"})
        with self.assertRaises(AccessError):
            collective.with_user(self.user_bc).write({"purchase_id": False})
        self.assertEqual(collective.state, "ordered")
        with self.assertRaises(UserError):
            collective.line_ids.with_user(self.user_bc).write({"qty": 3})
        with self.assertRaises(AccessError):
            self.env["blistercenter.collective.order"].with_user(self.user_bc).create({
                "supplier_id": self.supplier.id, "state": "done"})
        # Herstellerbestellung muss die Kundenmengen decken
        purchase = collective.purchase_id
        purchase.order_line.product_qty = 3
        with self.assertRaisesRegex(UserError, "Kundenmengen"):
            purchase.button_confirm()
        purchase.order_line.product_qty = 6  # mehr bestellen ist erlaubt
        purchase.button_confirm()
        self.assertEqual(purchase.state, "purchase")

    # R06 -----------------------------------------------------------------
    def test_r06_reducing_order_quantity_must_not_overallocate(self):
        order = self._order(self.customer_invoice, qty=5)
        self._collect(order)
        with self.assertRaises(ValidationError):
            order.order_line.product_uom_qty = 2
        order.order_line.product_uom_qty = 7  # erhöhen geht, Rest ist offen
        self.assertEqual(order.order_line.bc_qty_open, 2)

    def test_r06_reactivating_collective_must_not_overallocate(self):
        order = self._order(self.customer_invoice, qty=5)
        first = self._collect(order)
        first.action_cancel()
        second = self._collect(order)
        self.assertEqual(second.line_ids.qty, 5)
        with self.assertRaises(ValidationError):
            first.action_set_draft()
        self.assertEqual(first.state, "cancel")
        self.assertEqual(order.order_line.bc_qty_allocated, 5)
        # Gegenprobe: ohne zweite Bündelung lässt sie sich reaktivieren
        second.action_cancel()
        first.action_set_draft()
        self.assertEqual(first.state, "draft")

    # R07 -----------------------------------------------------------------
    def test_r07_partial_cannot_be_forged_without_agreement(self):
        self._stock(10, "CH-R7")
        order = self._order(self.customer_invoice, qty=4)
        order.order_line.action_bc_from_stock()
        with self.assertRaises(AccessError):
            order.with_user(self.user_bc).write({"bc_partial_allowed": True})
        with self.assertRaises(AccessError):
            order.with_user(self.user_bc).write({"bc_partial_note": "erfunden"})
        delivery = self._delivery(order)
        delivery.move_line_ids.quantity = 2
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaises(UserError):
            delivery.with_user(self.user_bc2)._action_done()
        # Freigabe-Flag ohne Vermerk (z. B. Altdaten) reicht beim Versand nicht
        order.sudo().write({"bc_partial_allowed": True})
        with self.assertRaises(UserError):
            delivery.with_user(self.user_bc2)._action_done()
        # Gegenprobe: Assistent mit Vermerk
        self.env["blistercenter.partial.wizard"].with_user(self.user_bc).create({
            "order_id": order.id, "note": "Telefonat mit Frau Muster"}).action_apply()
        delivery.with_user(self.user_bc2)._action_done()
        self.assertEqual(delivery.state, "done")

    # Weitere Nachweisfelder --------------------------------------------------
    def test_other_protected_fields(self):
        order = self._order(self.customer_invoice, qty=2)
        line = order.order_line
        with self.assertRaises(AccessError):
            line.with_user(self.user_bc).write({"bc_from_stock": True})
        delivery = self._delivery(order)
        with self.assertRaises(AccessError, msg="Feste Zuordnung nur über „Aus Lagerbestand liefern“"):
            delivery.move_ids.with_user(self.user_bc).write({"bc_wait_collective": False})
        lot = self._stock(1, "CH-R-LOT")
        with self.assertRaises(AccessError):
            lot.with_user(self.user_bc).write({"bc_blocked_by": self.user_approver.id})
        with self.assertRaises(AccessError):
            self.customer_invoice.with_user(self.user_approver).write({"bc_shop_enabled_by": self.user_bc.id})
        invoice = self._overdue_invoice(self.customer_invoice)
        with self.assertRaises(AccessError):
            invoice.with_user(self.user_accounting).write({"bc_picking_id": delivery.id})
        # Gegenprobe: geprüfte Aktionen setzen die Felder weiterhin
        line.with_user(self.user_bc).action_bc_from_stock()
        self.assertTrue(line.bc_from_stock)
        lot.with_user(self.user_bc).write({"bc_blocked": True, "bc_block_reason": "Test"})
        self.assertEqual(lot.bc_blocked_by, self.user_bc)
        lot.with_user(self.user_approver).write({"bc_blocked": False})
        self.assertFalse(lot.bc_blocked_by)
