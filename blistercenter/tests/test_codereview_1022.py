"""Code-Review 22.10.2026 zu 1.6.2 (Kamera als Scanner) – Blistercenter 1.6.4.

Befund 1  Bekanntes Verfallsdatum bleibt beim Vorbelegen der Handeingabe vollständig erhalten
          (fehlende Charge und mehrdeutiger Code).
Befund 2  „Übernehmen“/„Hinzufügen“ warten auf alle Scans (Browser-Tour blistercenter_receipt_scan_race).
Befund 3  Dieselbe Packung (Artikel + Seriennummer) zählt in einem Fenster nur einmal (R66).
Befund 4  Alt+029 ohne Pause (Browser-Tour blistercenter_receipt_scan).
Befund 5  Verarbeitungsfehler: Scan bleibt erhalten, Aktion wird abgelehnt, Wiederholen ohne Doppelzählung
          (Browser-Tour blistercenter_receipt_scan_failure; Serverteil: scan_hold).
"""
from datetime import date

from odoo.exceptions import UserError
from odoo.tests import Form, HttpCase, tagged
from odoo.tools import mute_logger

from ..tools import gs1
from ..wizard import receipt_scan
from .test_blistercenter import BlistercenterCommon
from .test_wareneingang_1_6 import ReceiptMixin

GS = "\x1d"


@tagged("post_install", "-at_install", "blistercenter")
class TestCodeReview1022(ReceiptMixin, BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_receipt_product()

    def _form(self, receipt):
        return Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))

    def _add_manual_from_prefill(self, form, lot):
        """Handeingabe so übernehmen, wie sie vorbelegt wurde (nur die Charge wird abgeschrieben)."""
        form.manual_lot = lot
        wizard = form.save()
        wizard.action_add_manual()
        return wizard

    # ------------------------------------------------------------------
    # Befund 1
    # ------------------------------------------------------------------
    def test_expiry_text_keeps_day(self):
        expiry_text = receipt_scan.expiry_text
        self.assertEqual(expiry_text(date(2028, 3, 15)), "15.03.2028")
        self.assertEqual(expiry_text(date(2028, 3, 31)), "03/2028", "Monatsende darf kurz bleiben")
        self.assertEqual(expiry_text(date(2028, 2, 29)), "02/2028")
        self.assertFalse(expiry_text(False))
        for value in (date(2028, 3, 15), date(2028, 3, 31), date(2028, 2, 29), date(2027, 2, 28)):
            with self.subTest(value=value):
                self.assertEqual(gs1.parse_manual_expiry(expiry_text(value))[0], value, "verlustfrei")

    def test_missing_batch_keeps_known_day(self):
        """Code ohne Charge, Verfall 15.03.2028: Handeingabe bucht 15.03.2028, nicht 31.03.2028."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._form(receipt)
        form.scan = "01%s17280315%s21SNOLOT1" % (self.gtin, "")
        self.assertEqual(form.message_type, "error")
        self.assertIn("Charge", form.message)
        self.assertEqual(form.manual_expiry, "15.03.2028")
        wizard = self._add_manual_from_prefill(form, "CH-OHNE")
        self.assertEqual(wizard.line_ids.expiration_date, date(2028, 3, 15))
        wizard.action_apply()
        self.assertEqual(receipt.move_line_ids.bc_scanned_expiry, date(2028, 3, 15))
        self.assertEqual(receipt.move_line_ids.expiration_date.date(), date(2028, 3, 15))

    def test_ambiguous_code_keeps_known_day(self):
        """Code ohne Trennzeichen, mehrdeutig: Verfall 15.03.2028 bleibt vollständig."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        code = "01%s21SER1010CH2117280315" % self.gtin
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse(code)
        form = self._form(receipt)
        form.scan = code
        self.assertEqual(form.message_type, "error")
        self.assertFalse(form.line_ids)
        self.assertEqual(form.manual_expiry, "15.03.2028")
        wizard = self._add_manual_from_prefill(form, "CH21")
        self.assertEqual(wizard.line_ids.expiration_date, date(2028, 3, 15))

    def test_month_end_code_still_prefills_month(self):
        """GS1-Tag 00 (= Monatsende) bleibt als MM/JJJJ vorbelegt."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._form(receipt)
        form.scan = "01%s1728030021SNOLOT2" % self.gtin
        self.assertEqual(form.manual_expiry, "03/2028")
        wizard = self._add_manual_from_prefill(form, "CH-ENDE")
        self.assertEqual(wizard.line_ids.expiration_date, date(2028, 3, 31))

    # ------------------------------------------------------------------
    # Befund 3 (R66)
    # ------------------------------------------------------------------
    def test_same_pack_counts_once_other_serial_counts(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=5)
        expiry = date(2028, 3, 31)
        form = self._form(receipt)
        form.scan = self._code("CH-DUP", expiry, "D1")
        self.assertEqual(form.message_type, "ok")
        form.scan = self._code("CH-DUP", expiry, "D1")
        self.assertEqual(form.message_type, "warning")
        self.assertIn("schon gezählt", form.message)
        self.assertIn("D1", form.message)
        form.scan = self._code("CH-DUP", expiry, "D2")
        self.assertEqual(form.message_type, "ok")
        self.assertIn("Menge 2", form.message)
        wizard = form.save()
        self.assertEqual(wizard.line_ids.qty, 2, "D1 einmal, D2 einmal")
        self.assertEqual(wizard.line_ids.serials.split("\n"), ["D1", "D2"])
        # Nach dem Speichern (Fenster neu geladen, z. B. nach „Hinzufügen“) gilt es weiter
        form = Form(wizard)
        form.scan = "]d2" + self._code("CH-DUP", expiry, "D2")
        self.assertIn("schon gezählt", form.message)
        wizard = form.save()
        self.assertEqual(wizard.line_ids.qty, 2)

    def test_same_serial_other_product_or_lot(self):
        """Dieselbe Seriennummer in einer anderen Charge desselben Artikels ist dieselbe Packung-ID: nicht zählen."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=5)
        form = self._form(receipt)
        form.scan = self._code("CH-X1", date(2028, 3, 31), "SAME")
        form.scan = self._code("CH-X2", date(2028, 3, 31), "SAME")
        self.assertIn("schon gezählt", form.message)
        self.assertEqual(sum(form.save().line_ids.mapped("qty")), 1)

    def test_manual_qty_change_still_possible(self):
        """Bewusst die Menge ändern (viele gleiche Packungen, ein Scan) bleibt möglich."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=6)
        form = self._form(receipt)
        form.scan = self._code("CH-MENGE", date(2028, 3, 31), "M1")
        with form.line_ids.edit(0) as line:
            line.qty = 5
        form.scan = self._code("CH-MENGE", date(2028, 3, 31), "M2")
        self.assertIn("Menge 6", form.message)
        form.scan = self._code("CH-MENGE", date(2028, 3, 31), "M1")
        self.assertIn("schon gezählt", form.message)
        wizard = form.save()
        self.assertEqual(wizard.line_ids.qty, 6)
        wizard.action_apply()
        self.assertEqual(sum(receipt.move_line_ids.mapped("quantity")), 6)

    def test_duplicate_without_gs_unique_reading(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        code = "01%s21SERX1728033110AB12" % self.gtin
        form = self._form(receipt)
        form.scan = code
        self.assertEqual(form.message_type, "warning")
        form.scan = code
        self.assertIn("schon gezählt", form.message)
        self.assertEqual(form.save().line_ids.qty, 1)

    def test_manual_entries_are_not_deduplicated(self):
        """Handeingabe hat keine Seriennummer: jede Eingabe zählt wie gewohnt."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self.env["blistercenter.receipt.scan"].with_user(self.user_bc).create({"picking_id": receipt.id})
        for _i in range(2):
            wizard.write({"manual_product_id": self.product.id, "manual_lot": "CH-H",
                          "manual_expiry": "05/2028", "manual_qty": 1})
            wizard.action_add_manual()
        self.assertEqual(wizard.line_ids.qty, 2)

    # ------------------------------------------------------------------
    # Befund 2/5 Serverteil: offene Scans halten die Aktion an
    # ------------------------------------------------------------------
    def test_scan_hold_blocks_apply_and_manual_add(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._form(receipt)
        form.scan = self._code("CH-HOLD", date(2028, 3, 31), "H1")
        wizard = form.save()
        wizard.scan_hold = "1 Scan(s) nicht verarbeitet"
        with self.assertRaisesRegex(UserError, "nicht verarbeitet"):
            wizard.action_apply()
        self.assertFalse(wizard.applied)
        self.assertFalse(receipt.move_line_ids.filtered("lot_name"), "nichts gebucht")
        wizard.write({"manual_product_id": self.product.id, "manual_lot": "CH-H2", "manual_expiry": "05/2028"})
        with self.assertRaisesRegex(UserError, "nicht verarbeitet"):
            wizard.action_add_manual()
        self.assertEqual(len(wizard.line_ids), 1)
        wizard.scan_hold = False
        wizard.action_apply()
        self.assertTrue(wizard.applied)
        self.assertEqual(receipt.move_line_ids.filtered("lot_name").lot_name, "CH-HOLD")


@tagged("post_install", "-at_install", "blistercenter")
class TestCodeReview1022Ui(ReceiptMixin, BlistercenterCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_receipt_product()

    def _lots(self, receipt):
        return sorted((l.lot_name or l.lot_id.name, l.quantity) for l in receipt.move_line_ids
                      if l.lot_name or l.lot_id)

    def test_actions_wait_for_scan_queue(self):
        """Befund 2: verzögerte Serverantwort, Scan ohne Enter, sofort „Hinzufügen“ bzw. „Übernehmen“."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=7)
        self.start_tour("/odoo/action-blistercenter.action_bc_receipts/%s" % receipt.id,
                        "blistercenter_receipt_scan_race", login=self.user_bc.login)
        self.assertEqual(self._lots(receipt), [
            ("CH-HAND-R", 1), ("CH-RACE-A", 2), ("CH-RACE-B", 3), ("CH-RACE-C", 1)])

    def test_processing_error_keeps_scans(self):
        """Befund 5: Fehler bei der Verarbeitung – aufbewahrt, abgelehnt, wiederholt, nichts doppelt."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        with mute_logger("odoo.http"):  # erwartete Warnung: „Übernehmen“ wird absichtlich abgelehnt
            self.start_tour("/odoo/action-blistercenter.action_bc_receipts/%s" % receipt.id,
                            "blistercenter_receipt_scan_failure", login=self.user_bc.login)
        self.assertEqual(self._lots(receipt), [("CH-FAIL-A", 3)])
