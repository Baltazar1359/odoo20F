"""GPT-Prüfbericht 23.10.2026 (Grundlage main / Blistercenter 1.2.1), umgesetzt in 1.6.3.

Schon in 1.5.1 behoben und hier nur gegengeprüft: Rechnung je Lieferung (R51), offene Posten nur
der eigenen Firma. Neu:
- Beschaffungszuordnung: Position der Herstellerbestellung nicht löschen/ersetzen/Artikel tauschen,
  Prüfung je Position (B2).
- Verfallene Chargen nie freigeben oder versenden, auch nicht mit Kurzläufer-Freigabe (B3).
- Zuordnung Blistercenter: Lieferung und Bündelung folgen dem Auftrag, nicht dem aktuellen
  Kennzeichen am Kontakt (B6).
- HAP 0 € verhindert die Bestätigung (B5).
- Menü „Kurzläufer im Bestand“ = Kachel (B7).
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_blistercenter import BlistercenterCommon


@tagged("post_install", "-at_install", "blistercenter")
class TestPruefbericht1023(BlistercenterCommon):

    def _purchase(self, qty=3):
        order = self._order(self.customer_invoice, qty=qty)
        collective = self._collect(order)
        collective.action_create_purchase()
        return order, collective, collective.purchase_id

    # ------------------------------------------------------------------
    # B2 Beschaffungszuordnung
    # ------------------------------------------------------------------
    def test_purchase_line_with_customer_qty_cannot_be_replaced(self):
        _order, collective, purchase = self._purchase()
        line = purchase.order_line
        with self.assertRaisesRegex(UserError, "nicht gelöscht oder ersetzt"):
            line.unlink()
        with self.assertRaisesRegex(UserError, "nicht gelöscht oder ersetzt"):
            purchase.write({"order_line": [
                Command.delete(line.id),
                Command.create({"product_id": self.product.id, "product_qty": 3}),
            ]})
        other = self.product.copy({"name": "Anderer Artikel"})
        with self.assertRaisesRegex(UserError, "nicht getauscht"):
            line.product_id = other
        self.assertEqual(collective.line_ids.purchase_line_id, line, "Zuordnung bleibt erhalten")
        line.product_qty = 5  # Menge ändern geht
        purchase.button_confirm()
        self.assertEqual(purchase.state, "purchase")

    def test_confirm_checks_each_purchase_line(self):
        """Zweite Position desselben Artikels darf die Unterdeckung der verbundenen Position nicht verdecken."""
        _order, _collective, purchase = self._purchase(qty=3)
        purchase.order_line.product_qty = 1
        purchase.write({"order_line": [Command.create({"product_id": self.product.id, "product_qty": 5})]})
        with self.assertRaisesRegex(UserError, "bestellt werden nur"):
            purchase.button_confirm()

    def test_confirm_rejects_lost_link(self):
        _order, collective, purchase = self._purchase()
        # Zuordnung verloren (z. B. Altdaten vor 1.6.3)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE blistercenter_collective_order_line SET purchase_line_id = NULL WHERE id IN %s",
            (tuple(collective.line_ids.ids),))
        collective.line_ids.invalidate_recordset(["purchase_line_id"])
        with self.assertRaisesRegex(UserError, "mit keiner Position"):
            purchase.button_confirm()

    def test_cancel_and_redraft_collective_still_possible(self):
        _order, collective, purchase = self._purchase()
        collective.action_cancel()
        self.assertEqual(purchase.state, "cancel")
        collective.action_set_draft()
        collective.action_create_purchase()
        self.assertEqual(collective.line_ids.purchase_line_id.order_id, collective.purchase_id)

    # ------------------------------------------------------------------
    # B3 Verfallene Ware
    # ------------------------------------------------------------------
    def test_expired_lot_never_released_even_with_short_approval(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-ABGELAUFEN", days=20)
        release.move_line_ids.lot_id.expiration_date = fields.Datetime.now() - timedelta(days=5)
        self.assertIn(release.move_line_ids.lot_id, release.bc_short_lot_ids)
        with self.assertRaisesRegex(UserError, "verfallen"):
            release.with_user(self.user_approver)._bc_approve_short("Kunde einverstanden")
        # Auch eine Freigabe, die vorher (noch nicht verfallen) erteilt wurde, hilft nicht
        release.sudo().write({"bc_short_approved_lot_ids": [Command.set(release.move_line_ids.lot_id.ids)]})
        # skip_expired = Odoos Warnung „abgelaufene Lose“ mit „Bestätigen“ übergangen
        with self.assertRaisesRegex(UserError, "verfallene Chargen"):
            release.with_user(self.user_approver).with_context(skip_expired=True).button_validate()

    def test_expired_lot_never_shipped(self):
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-BALD", days=20)
        release.with_user(self.user_approver)._bc_approve_short("Kunde einverstanden")
        release.with_user(self.user_approver).button_validate()
        delivery = self._delivery(order)
        delivery.with_user(self.user_approver)._bc_approve_short("Abgestimmt")
        lot = delivery.move_line_ids.lot_id
        lot.expiration_date = fields.Datetime.now() - timedelta(hours=1)  # inzwischen verfallen
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        with self.assertRaisesRegex(UserError, "verfallene Chargen"):
            delivery.with_user(self.user_bc2).with_context(skip_expired=True).button_validate()

    # ------------------------------------------------------------------
    # B5 HAP
    # ------------------------------------------------------------------
    def test_zero_hap_blocks_confirmation(self):
        self.product.list_price = 0.0
        order = self._order(self.customer_invoice, qty=1, confirm=False)
        with self.assertRaisesRegex(UserError, "kein HAP"):
            order.action_confirm()

    # ------------------------------------------------------------------
    # B6 Zuordnung Blistercenter
    # ------------------------------------------------------------------
    def test_delivery_of_normal_order_to_bc_address_is_not_bc(self):
        normal = self.env["res.partner"].create({"name": "Normaler Kunde", "is_company": True})
        order = self._order(normal, qty=1, confirm=False, partner_shipping_id=self.customer_invoice.id)
        self.product.bc_shop_product = False
        self._stock(5, "CH-NORMAL")
        order.action_confirm()
        self.assertFalse(order.bc_is_blistercenter)
        delivery = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        self.assertEqual(delivery.partner_id.commercial_partner_id, self.customer_invoice)
        self.assertFalse(delivery.bc_is_blistercenter, "Auftrag ist kein Blistercenter-Auftrag")
        self.assertFalse(delivery.bc_kind)

    def test_confirmed_bc_order_stays_bc_after_flag_removed(self):
        order = self._order(self.customer_invoice, qty=2)
        self.customer_invoice.with_user(self.user_approver).bc_is_customer = False
        order.invalidate_recordset()
        self.assertTrue(order.bc_is_blistercenter)
        collective = self._collect(order)  # Bündeln folgt dem Auftrag
        self.assertEqual(collective.line_ids.sale_order_id, order)
        delivery = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing")
        self.assertTrue(delivery.bc_is_blistercenter)
        self.assertEqual(delivery.bc_kind, "delivery")

    def test_order_confirmed_before_flag_is_not_bundled(self):
        normal = self.env["res.partner"].create({"name": "Später Blistercenter", "is_company": True})
        order = self._order(normal, qty=1)
        self.assertFalse(order.bc_is_blistercenter)
        normal.with_user(self.user_approver).write({
            "bc_is_customer": True, "property_payment_term_id": self.term_14.id})
        order.invalidate_recordset()
        self.assertFalse(order.bc_is_blistercenter, "Zuordnung bei Bestätigung festgehalten")
        with self.assertRaises((UserError, ValidationError)):
            self.env["blistercenter.collective.order.line"].with_user(self.user_bc).create({
                "order_id": self.env["blistercenter.collective.order"].with_user(self.user_bc).create({
                    "supplier_id": self.supplier.id}).id,
                "sale_line_id": order.order_line.id, "qty": 1,
            })

    # ------------------------------------------------------------------
    # B7 Menü = Kachel
    # ------------------------------------------------------------------
    def test_short_dated_menu_equals_tile(self):
        self._stock(3, "CH-KURZ-M", days=30)
        self._stock(3, "CH-LANG-M", days=500)
        tile = self.env.ref("blistercenter.tile_short_dated").with_user(self.user_bc)
        action = self.env.ref("blistercenter.action_bc_short_quants_menu").with_user(self.user_bc).run()
        quants = self.env["stock.quant"].with_user(self.user_bc).search(action["domain"])
        self.assertIn("CH-KURZ-M", quants.lot_id.mapped("name"))
        self.assertNotIn("CH-LANG-M", quants.lot_id.mapped("name"), "Menü zeigt nur Kurzläufer")
        self.assertEqual(len(quants), tile.count)

    # ------------------------------------------------------------------
    # Gegenprüfung: schon in 1.5.1 behoben
    # ------------------------------------------------------------------
    def test_findings_fixed_in_1_5_1_still_covered(self):
        from . import test_pruefbericht_1021
        names = dir(test_pruefbericht_1021.TestPruefbericht1021)
        for test in ("test_two_deliveries_together_get_own_invoices", "test_overdue_only_own_company",
                     "test_price_must_equal_hap"):
            self.assertIn(test, names)
