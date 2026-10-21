"""Blistercenter 1.2.0: Proforma, Rechnung je Lieferung, Zahlungsziel, Rabatt auf den HAP, Hinweis überfällig.

Grundlage: Gespräch mit Herrn Krebs (PLCS), Entscheidungen 19.10.2026. Jede Regel mit Negativtest.
"""
from datetime import timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_blistercenter import BlistercenterCommon


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterBilling(BlistercenterCommon):

    def _proforma_html(self, order):
        report = self.env.ref("blistercenter.action_report_bc_proforma")
        html, _type = self.env["ir.actions.report"]._render_qweb_html(report, order.ids)
        return html.decode()

    def _ship_partial(self, order, qty):
        """Teillieferung mit Vermerk: qty kommissionieren (Person 1), bestätigen (Person 2)."""
        delivery = order.picking_ids.filtered(lambda p: p.picking_type_code == "outgoing" and p.state not in ("done", "cancel"))
        delivery.action_assign()
        delivery.move_line_ids.quantity = qty
        delivery.with_user(self.user_bc).action_bc_mark_picked()
        delivery.with_user(self.user_bc2)._action_done()
        return delivery

    # ------------------------------------------------------------------
    # Vorkasse entfällt, Proforma
    # ------------------------------------------------------------------
    def test_confirm_without_payment_and_proforma_without_entry(self):
        moves_before = self.env["account.move"].search_count([])
        mails_before = self.env["mail.mail"].search_count([])
        order = self._order(self.customer_debit)
        self.assertEqual(order.state, "sale", "Bestätigung ohne Zahlung")
        self.assertFalse(order.invoice_ids, "Keine Anzahlungsrechnung mehr")
        self.assertEqual(order.bc_state, "to_procure")
        self.assertNotIn("awaiting_payment", dict(order._fields["bc_state"].selection))

        html = self._proforma_html(order)
        self.assertIn("Proforma – keine Rechnung", html)
        self.assertIn("SEPA-Lastschrift", html, "Lastschrift-Kunde: Hinweis auf der Proforma")
        self.assertEqual(self.env["account.move"].search_count([]), moves_before, "Proforma bucht nichts")
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before, "Kein E-Mail-Versand")
        action = order.with_user(self.user_bc).action_bc_print_proforma()
        self.assertEqual(action["report_name"], "sale.report_saleorder_pro_forma")

        # Negativ: nur bestätigte Blistercenter-Aufträge
        draft = self._order(self.customer_debit, confirm=False)
        with self.assertRaises(UserError):
            draft.action_bc_print_proforma()
        with self.assertRaises(UserError):
            self._order(self.customer_other).action_bc_print_proforma()
        # Der Hinweis erscheint nur auf der Proforma, nicht im normalen Auftragsdruck
        normal = self.env.ref("sale.action_report_saleorder")
        html_normal, _type = self.env["ir.actions.report"]._render_qweb_html(normal, order.ids)
        self.assertNotIn("Proforma – keine Rechnung", html_normal.decode())

    def test_collective_without_payment_and_no_automatic_purchase(self):
        purchases_before = self.env["purchase.order"].search_count([])
        order = self._order(self.customer_debit, qty=3)
        self.assertEqual(self.env["purchase.order"].search_count([]), purchases_before,
                         "Bestätigung löst keine Herstellerbestellung aus")
        line = order.order_line
        self.assertEqual(line.bc_qty_open, 3, "Erscheint unter „Zu bestellen“")
        self.assertEqual(line.bc_supplier_id, self.supplier, "Hersteller aus der Einkaufskondition")
        collective = self._collect(order)
        self.assertEqual(collective.line_ids.qty, 3, "Bündeln ohne Zahlung möglich")
        self.assertEqual(self.env["purchase.order"].search_count([]), purchases_before,
                         "Bündeln erzeugt noch keine Bestellung")
        collective.action_create_purchase()
        self.assertEqual(collective.purchase_id.state, "draft", "Bestellt wird manuell")

    # ------------------------------------------------------------------
    # Zahlungsziel und Fakturierungsregel
    # ------------------------------------------------------------------
    def test_payment_term_required(self):
        partner = self.env["res.partner"].sudo().create({
            "name": "Blistercenter ohne Zahlungsziel", "is_company": True, "bc_is_customer": True,
        })
        order = self._order(partner, confirm=False)
        self.assertFalse(order.payment_term_id)
        with self.assertRaisesRegex(UserError, "Zahlungsziel"):
            order.action_confirm()
        partner.property_payment_term_id = self.term_7
        order = self._order(partner)
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.payment_term_id, self.term_7, "Auftrag übernimmt das Zahlungsziel des Kunden")
        self.assertEqual(self.term_7.line_ids.nb_days, 7)
        self.assertEqual(self.term_14.line_ids.nb_days, 14)

    def test_invoice_policy_delivery_required(self):
        self.product.invoice_policy = "order"
        order = self._order(self.customer_debit, confirm=False)
        with self.assertRaisesRegex(UserError, "gelieferte Mengen"):
            order.action_confirm()
        self.product.invoice_policy = "delivery"
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    # ------------------------------------------------------------------
    # Rabatt auf den HAP
    # ------------------------------------------------------------------
    def test_customer_discount_on_hap(self):
        self.assertEqual(self.product.list_price, 20.0, "Verkaufspreis = HAP")
        for discount in ("0", "1", "2"):
            self.customer_invoice.bc_discount = discount
            order = self._order(self.customer_invoice, qty=10)
            line = order.order_line
            self.assertEqual(line.price_unit, 20.0, "Preis = HAP")
            self.assertEqual(line.discount, float(discount))
            self.assertAlmostEqual(line.price_subtotal, 200.0 * (1 - float(discount) / 100))
        # Nur 0, 1 oder 2 % zulässig
        with self.assertRaises(ValueError):
            self.customer_invoice.bc_discount = "3"
        # Abweichender Rabatt in der Position verhindert die Bestätigung
        order = self._order(self.customer_debit, confirm=False)
        self.assertEqual(order.order_line.discount, 2.0)
        order.order_line.discount = 5.0
        with self.assertRaisesRegex(UserError, "Rabatt"):
            order.action_confirm()
        order.order_line.discount = 2.0
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        # Rabatt ändert nur die Rolle Freigabe
        with self.assertRaises(AccessError):
            self.customer_debit.with_user(self.user_bc).write({"bc_discount": "1"})
        self.customer_debit.with_user(self.user_approver).write({"bc_discount": "1"})
        self.assertEqual(self.customer_debit.bc_discount, "1")

    def test_price_at_order_time(self):
        order = self._order(self.customer_invoice, qty=2)
        self.product.list_price = 25.0
        self.assertEqual(order.order_line.price_unit, 20.0, "Preis zum Zeitpunkt des Auftrags gilt")
        self.assertEqual(self._order(self.customer_invoice, qty=2).order_line.price_unit, 25.0)

    # ------------------------------------------------------------------
    # Rechnung je Lieferung
    # ------------------------------------------------------------------
    def test_invoice_per_delivery_with_partial(self):
        mails_before = self.env["mail.mail"].search_count([])
        self._stock(10, "CH-RECHNUNG")
        order = self._order(self.customer_debit, qty=4)
        order.order_line.action_bc_from_stock()
        self.assertFalse(order.invoice_ids, "Vor der Lieferung keine Rechnung")
        self.env["blistercenter.partial.wizard"].with_user(self.user_bc).create({
            "order_id": order.id, "note": "Telefonat mit Frau Muster"}).action_apply()

        first = self._ship_partial(order, 2)
        self.assertEqual(first.state, "done")
        invoice_1 = order.invoice_ids
        self.assertEqual(len(invoice_1), 1, "Eine Rechnung je Lieferung")
        self.assertEqual(invoice_1.state, "draft", "Entwurf, kein automatisches Buchen")
        self.assertEqual(invoice_1.bc_picking_id, first)
        self.assertEqual(first.bc_invoice_ids, invoice_1)
        self.assertEqual(invoice_1.invoice_line_ids.quantity, 2, "Nur die gelieferte Menge")
        self.assertEqual(invoice_1.invoice_line_ids.discount, 2.0)
        # Zahlungsziel ab Proforma (21.10.2026): Rechnung übernimmt die Fälligkeit der Proforma
        self.assertFalse(invoice_1.invoice_payment_term_id)
        self.assertEqual(invoice_1.invoice_date_due, order.bc_proforma_due_date)
        self.assertEqual(order.bc_proforma_due_date, order.bc_proforma_date + timedelta(days=14))
        self.assertIn("SEPA-Lastschrift", str(invoice_1.narration))
        self.assertIn(first.name, str(invoice_1.narration))
        self.assertTrue(invoice_1.activity_ids.filtered(lambda a: a.user_id == self.user_accounting))

        second = self._ship_partial(order, 2)
        invoice_2 = order.invoice_ids - invoice_1
        self.assertEqual(len(invoice_2), 1, "Rest → zweite Rechnung")
        self.assertEqual(invoice_2.bc_picking_id, second)
        self.assertEqual(invoice_2.invoice_line_ids.quantity, 2)
        self.assertEqual(order.order_line.qty_invoiced, 4)
        self.assertEqual(self.env["mail.mail"].search_count([]), mails_before, "Kein E-Mail-Versand")

    def test_invoice_per_delivery_switch_and_transfer_customer(self):
        self._stock(10, "CH-UEB")
        order = self._order(self.customer_invoice, qty=2)
        order.order_line.action_bc_from_stock()
        self._ship(self._delivery(order))
        self.assertEqual(len(order.invoice_ids), 1)
        self.assertNotIn("Lastschrift", str(order.invoice_ids.narration), "Überweisung: kein Lastschrift-Hinweis")
        # Regel aus: keine automatische Rechnung
        self.config.rule_invoice_per_delivery = False
        order = self._order(self.customer_invoice, qty=2)
        order.order_line.action_bc_from_stock()
        self._ship(self._delivery(order))
        self.assertFalse(order.invoice_ids)

    def test_no_invoice_without_delivery(self):
        """Wareneingang und Freigabe allein erzeugen keine Kundenrechnung."""
        order = self._order(self.customer_debit, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-OHNE-RE")
        release.with_user(self.user_approver).button_validate()
        self.assertFalse(order.invoice_ids)

    # ------------------------------------------------------------------
    # Hinweis überfällige Rechnungen
    # ------------------------------------------------------------------
    def test_overdue_hint_without_block(self):
        self._stock(10, "CH-OP")
        order = self._order(self.customer_invoice, qty=2)
        self.assertEqual(order.bc_overdue_count, 0)
        overdue = self._overdue_invoice(self.customer_invoice, amount=150.0)
        self._overdue_invoice(self.customer_invoice, days_ago=3)  # noch nicht fällig
        self.env.invalidate_all()
        self.assertEqual(self.customer_invoice.bc_overdue_count, 1)
        self.assertAlmostEqual(self.customer_invoice.bc_overdue_amount, 150.0)
        self.assertEqual(order.bc_overdue_count, 1, "Hinweis im Auftrag")
        # Neue Bestellung und Lieferung bleiben möglich
        order_new = self._order(self.customer_invoice, qty=2)
        self.assertEqual(order_new.state, "sale")
        order.order_line.action_bc_from_stock()
        delivery = self._delivery(order)
        self.assertEqual(delivery.bc_overdue_count, 1, "Hinweis in der Lieferung")
        self._ship(delivery)
        self.assertEqual(delivery.state, "done", "Keine Sperre")
        # Bezahlt → Hinweis weg
        self._register_payment(overdue)
        self.env.invalidate_all()
        self.assertEqual(self.customer_invoice.bc_overdue_count, 0)
        self.assertEqual(self.customer_debit.bc_overdue_count, 0, "Andere Kunden unberührt")
