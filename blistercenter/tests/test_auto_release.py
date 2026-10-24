"""Automatische Freigabe regulärer Herstellerlieferungen (Regel BC-AUTO-1, 1.3.0).

Standardfall: Wareneingang → Wareneingangsprüfung → automatische Freigabe in den Bestand.
Ausnahmefall: jede Auffälligkeit, jeder fehlende Wert → Ware bleibt in der Quarantäne, die VP entscheidet.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.service.model import call_kw
from odoo.tests import tagged

from .test_blistercenter import BlistercenterCommon

ALL_OK = {
    "bc_check_packaging": "ok",
    "bc_check_temperature": "ok",
    "bc_check_documents": "ok",
    "bc_check_quality": "ok",
}


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterAutoRelease(BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier.with_user(cls.user_approver).write({
            "bc_supplier_qualified": True,
            "bc_supplier_qualification_ref": "Lieferantenbewertung 2026-03",
        })
        cls.config.with_user(cls.user_approver)._bc_enable_auto_release("SOP-QM-014 v3")

    # Hilfsfunktionen ------------------------------------------------------
    def _receive_checked(self, order_qty=3, answers=ALL_OK, note=False, lot_name="CH-AUTO", days=730,
                         qty=None, user=None, before_validate=None):
        """Bestellung → Sammelbestellung → Wareneingang mit Wareneingangsprüfung → Buchung."""
        order = self._order(self.customer_invoice, qty=order_qty)
        collective = self._collect(order)
        collective.action_create_purchase()
        collective.purchase_id.button_confirm()
        receipt = collective.purchase_id.picking_ids
        receipt.move_ids.move_line_ids.unlink()
        receipt.move_ids._action_assign()
        for move in receipt.move_ids:
            move.move_line_ids.write({
                "lot_name": lot_name,
                "quantity": qty if qty is not None else move.product_uom_qty,
                "expiration_date": fields.Datetime.now() + timedelta(days=days),
            })
        receipt.move_ids.picked = True
        if answers:
            self.env["blistercenter.receipt.check.wizard"].with_user(user or self.user_bc).create({
                "picking_id": receipt.id, **answers, "note": note}).action_apply()
        if before_validate:
            before_validate(receipt)
        receipt.with_user(user or self.user_bc).button_validate()
        release = self.env["stock.picking"].search([("bc_receipt_id", "=", receipt.id)])
        return order, receipt, release

    def assertVpRelease(self, release, failed_text):
        self.assertEqual(release.bc_release_mode, "vp")
        self.assertEqual(release.state, "assigned", "Ware bleibt in der Quarantäne")
        self.assertIn("✗ " + failed_text, release.bc_release_result)
        self.assertTrue(release.activity_ids.filtered(lambda a: a.user_id == self.user_approver),
                        "VP bekommt die Aufgabe")

    # Standardfall ---------------------------------------------------------
    def test_regular_manufacturer_delivery_released_automatically(self):
        order, receipt, release = self._receive_checked()
        self.assertEqual(receipt.bc_check_state, "ok")
        self.assertEqual(receipt.bc_checked_by, self.user_bc)
        self.assertEqual(release.bc_release_mode, "auto")
        self.assertEqual(release.state, "done", "Ohne VP-Einzelentscheidung in den Bestand")
        self.assertEqual(release.location_dest_id, self.stock_location)
        self.assertEqual(release.bc_validated_by, self.env.ref("base.user_root"), "Gebucht vom System")
        self.assertNotIn("✗", release.bc_release_result)
        self.assertIn("BC-AUTO-1", release.bc_release_rule)
        self.assertIn("SOP-QM-014 v3", release.bc_release_rule)
        self.assertIn(self.user_approver.name, release.bc_release_rule, "VP des Verfahrens dokumentiert")
        self.assertIn(self.user_bc.name, release.bc_release_result, "Prüfer dokumentiert")
        self.assertTrue(release.bc_release_evaluated_date)
        self.assertFalse(release.activity_ids, "Keine Aufgabe für die VP")
        # Ware fest je Kunde: die Lieferung reserviert genau diese Ware, Versand wie bisher mit Endkontrolle
        delivery = self._delivery(order)
        self.assertEqual(delivery.state, "assigned")
        self.assertEqual(delivery.move_line_ids.lot_id.name, "CH-AUTO")
        self._ship(delivery)
        self.assertEqual(delivery.state, "done")

    def test_disabled_by_default_and_without_procedure(self):
        self.config._bc_disable_auto_release("Test")
        _order, _receipt, release = self._receive_checked()
        self.assertVpRelease(release, "Verfahren von der VP freigegeben und eingeschaltet")
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")
        self.assertEqual(release.bc_validated_by, self.user_approver)

    # Ausnahmefälle ----------------------------------------------------------
    def test_damaged_goods_stay_in_quarantine_for_vp(self):
        with self.assertRaises(UserError, msg="Abweichung ohne Beschreibung"):
            self._receive_checked(answers={**ALL_OK, "bc_check_packaging": "deviation"})
        _order, receipt, release = self._receive_checked(
            answers={**ALL_OK, "bc_check_packaging": "deviation"}, note="2 Faltschachteln eingedrückt")
        self.assertEqual(receipt.bc_check_state, "deviation")
        self.assertVpRelease(release, "Wareneingangsprüfung erfasst, alle Fragen ohne Abweichung")
        activity = release.activity_ids.filtered(lambda a: a.user_id == self.user_approver)
        self.assertIn("Abweichung", activity.summary)
        # Bestehende VP-Freigabe bleibt: nur die VP, nicht der Wareneingang selbst
        with self.assertRaises(UserError):
            release.with_user(self.user_bc2).button_validate()
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")

    def test_temperature_deviation(self):
        _order, _receipt, release = self._receive_checked(
            answers={**ALL_OK, "bc_check_temperature": "deviation"}, note="Datenlogger 11 °C")
        self.assertVpRelease(release, "Wareneingangsprüfung erfasst, alle Fragen ohne Abweichung")

    def test_unqualified_supplier(self):
        self.supplier.with_user(self.user_approver).write({"bc_supplier_qualified": False})
        _order, _receipt, release = self._receive_checked()
        self.assertVpRelease(release, "Hersteller %s GDP-qualifiziert und freigegeben" % self.supplier.name)

    def test_expired_supplier_qualification(self):
        self.supplier.with_user(self.user_approver).write(
            {"bc_supplier_qualified_until": fields.Date.today() - timedelta(days=1)})
        _order, _receipt, release = self._receive_checked()
        self.assertEqual(release.bc_release_mode, "vp")

    def test_missing_receipt_check(self):
        _order, receipt, release = self._receive_checked(answers=None)
        self.assertEqual(receipt.bc_check_state, "open")
        self.assertVpRelease(release, "Wareneingangsprüfung erfasst, alle Fragen ohne Abweichung")

    def test_missing_expiry_date(self):
        def drop_expiry(receipt):
            # Ohne Verfallsführung setzt Odoo kein Verfallsdatum an der neuen Charge
            self.product.use_expiration_date = False
            receipt.move_line_ids.write({"expiration_date": False})
        _order, receipt, release = self._receive_checked(before_validate=drop_expiry)
        self.assertFalse(receipt.move_line_ids.lot_id.expiration_date)
        self.assertVpRelease(release, "Charge und Verfallsdatum je Position vorhanden")

    def test_short_dated_lot(self):
        _order, _receipt, release = self._receive_checked(days=40)
        self.assertVpRelease(release, "Kein Kurzläufer (Restlaufzeit unter 3 Monaten)")
        self.assertTrue(release.bc_short_open, "Kurzläufer-Freigabe der VP bleibt nötig")

    def test_blocked_lot(self):
        lot = self.env["stock.lot"].create({
            "name": "CH-GESPERRT-EIN", "product_id": self.product.id, "company_id": self.company.id,
            "expiration_date": fields.Datetime.now() + timedelta(days=700),
            "bc_blocked": True, "bc_block_reason": "Rückruf Hersteller",
        })

        def use_blocked_lot(receipt):
            receipt.move_line_ids.write({"lot_id": lot.id, "lot_name": False})
            self.env["blistercenter.receipt.check.wizard"].with_user(self.user_bc).create({
                "picking_id": receipt.id, **ALL_OK}).action_apply()
        _order, _receipt, release = self._receive_checked(before_validate=use_blocked_lot)
        self.assertVpRelease(release, "Keine gesperrte oder zurückgerufene Charge")

    def test_over_delivery(self):
        _order, _receipt, release = self._receive_checked(order_qty=3, qty=5)
        self.assertVpRelease(release, "Lieferung entspricht der Bestellung (nur bestellte Artikel, keine Mehrmenge)")

    def test_delivery_not_from_manufacturer(self):
        other = self.env["res.partner"].create({"name": "Großhändler (Test)", "is_company": True})

        def change_sender(receipt):
            receipt.partner_id = other
        _order, _receipt, release = self._receive_checked(before_validate=change_sender)
        self.assertVpRelease(release, "Lieferung direkt vom Hersteller der Bestellung")

    def test_return_is_never_auto_released(self):
        _order, first_receipt, _release = self._receive_checked(lot_name="CH-ERST")

        def mark_as_return(receipt):
            receipt.sudo().return_id = first_receipt
        _order, _receipt, release = self._receive_checked(lot_name="CH-RET", before_validate=mark_as_return)
        self.assertVpRelease(
            release, "Regulärer Wareneingang aus einer Sammelbestellung (keine Retoure, kein Sonderfall)")

    def test_check_reset_when_receipt_changes(self):
        def change_lot(receipt):
            receipt.move_line_ids.write({"lot_name": "CH-GEAENDERT"})
            self.assertFalse(receipt.bc_checked_by, "Änderung nach der Prüfung setzt sie zurück")
        _order, receipt, release = self._receive_checked(before_validate=change_lot)
        self.assertEqual(receipt.bc_check_state, "open")
        self.assertEqual(release.bc_release_mode, "vp")

    def test_check_only_before_booking(self):
        _order, receipt, _release = self._receive_checked()
        with self.assertRaises(UserError):
            receipt.with_user(self.user_bc)._bc_record_receipt_check(ALL_OK)
        with self.assertRaises(UserError, msg="Unvollständige Antworten"):
            order = self._order(self.customer_invoice, qty=1)
            collective = self._collect(order)
            collective.action_create_purchase()
            collective.purchase_id.button_confirm()
            collective.purchase_id.picking_ids.with_user(self.user_bc)._bc_record_receipt_check(
                {"bc_check_packaging": "ok"})

    # Manipulationsversuche ------------------------------------------------------
    def test_manipulated_release_attempts(self):
        self.config._bc_disable_auto_release("Test")
        _order, receipt, release = self._receive_checked(lot_name="CH-MANIP")
        self.assertEqual(release.bc_release_mode, "vp")
        # Nachweisfelder direkt schreiben (wie per RPC)
        for vals in ({"bc_release_mode": "auto"}, {"bc_release_result": "✓ alles"},
                     {"bc_receipt_id": False}, {"bc_release_rule": "BC-AUTO-1"}):
            with self.assertRaises(AccessError):
                release.with_user(self.user_bc).write(vals)
        with self.assertRaises(AccessError):
            receipt.with_user(self.user_bc).write({"bc_check_packaging": "ok"})
        with self.assertRaises(AccessError):
            receipt.with_user(self.user_bc).write({"bc_checked_by": self.user_bc2.id})
        # Kontextwert aus einem RPC-Aufruf ist keine interne Freigabe
        with self.assertRaises(UserError):
            release.with_user(self.user_bc).with_context(bc_internal_auth="auto_release").button_validate()
        # Interne Methoden sind per RPC nicht aufrufbar
        for method in ("_bc_auto_release", "_bc_decide_release", "_bc_record_receipt_check"):
            with self.assertRaises(AccessError):
                call_kw(release.with_user(self.user_bc), method, [release.ids, {}], {})
        self.assertEqual(release.state, "assigned")
        release.with_user(self.user_approver).button_validate()
        self.assertEqual(release.state, "done")

    def test_procedure_only_by_responsible_vp(self):
        self.config._bc_disable_auto_release("Test")
        with self.assertRaises(AccessError, msg="Andere VP"):
            self.config.with_user(self.user_approver2)._bc_enable_auto_release("SOP")
        with self.assertRaises(AccessError, msg="Lager"):
            self.config.with_user(self.user_bc)._bc_enable_auto_release("SOP")
        with self.assertRaises(AccessError, msg="Administrator schreibt direkt"):
            self.config.sudo(False).with_user(self.user_manager).write({"auto_release_enabled": True})
        with self.assertRaises(UserError, msg="SOP Pflicht"):
            self.config.with_user(self.user_approver)._bc_enable_auto_release(" ")
        wizard = self.env["blistercenter.auto.release.wizard"].with_user(self.user_approver).create(
            {"config_id": self.config.id, "sop": "SOP-QM-014 v4"})
        with self.assertRaises(UserError, msg="Bestätigung Pflicht"):
            wizard.action_enable()
        wizard.confirm = True
        wizard.action_enable()
        self.assertTrue(self.config.auto_release_enabled)
        self.assertEqual(self.config.auto_release_approved_by, self.user_approver)
        # Wechsel der VP schaltet die Automatik aus
        self.config.write({"vp_user_id": self.user_approver2.id})
        self.assertFalse(self.config.auto_release_enabled)
        self.assertFalse(self.config.auto_release_approved_by)
        # Ausschalten dürfen VP und Administrator, nicht das Lager
        self.config.with_user(self.user_approver2)._bc_enable_auto_release("SOP-QM-014 v4")
        with self.assertRaises(AccessError):
            self.config.with_user(self.user_bc)._bc_disable_auto_release()
        self.config.with_user(self.user_manager)._bc_disable_auto_release("Audit")
        self.assertFalse(self.config.auto_release_enabled)

    def test_supplier_qualification_only_by_approver(self):
        with self.assertRaises(AccessError):
            self.supplier.with_user(self.user_bc).write({"bc_supplier_qualified": False})
        with self.assertRaises(AccessError):
            self.supplier.with_user(self.user_bc).write(
                {"bc_supplier_qualified_until": fields.Date.today() + timedelta(days=365)})
        with self.assertRaises(AccessError):
            self.supplier.with_user(self.user_approver).write({"bc_supplier_qualified_by": self.user_bc.id})
        self.assertEqual(self.supplier.bc_supplier_qualified_by, self.user_approver)
        self.assertTrue(self.supplier.bc_supplier_qualified_date)
