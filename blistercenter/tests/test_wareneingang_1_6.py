"""Wareneingang einfach (1.6.0, Test Mehrdad 22.10.2026): Handeingabe, Scanner ohne Trennzeichen, Übersicht.

R55 Handeingabe von Artikel, Charge, Verfall und Anzahl im Fenster „Packungen erfassen“.
R56 Scan-Feld setzt die Trennzeichen-Tasten des Handscanners als GS ein, Enter schickt ab (Browser-Test).
R57 Code ohne Trennzeichen: zählen nur, wenn alle Lesarten dasselbe ergeben; sonst Handeingabe vorbelegen.
R58 Übersicht je Artikel: bestellt, schon im Eingang, in dieser Liste.
"""
from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, HttpCase, tagged

from ..tools import gs1
from .test_blistercenter import BlistercenterCommon

GS = "\x1d"


class ReceiptMixin:
    """Hilfen wie in test_chargen (ohne dessen Tests zu erben)."""

    @classmethod
    def _setup_receipt_product(cls):
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

    def _code(self, lot, expiry, serial):
        return "01%s17%s10%s%s21%s" % (self.gtin, expiry.strftime("%y%m%d"), lot, GS, serial)


@tagged("post_install", "-at_install", "blistercenter")
class TestWareneingangEinfach(ReceiptMixin, BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_receipt_product()

    def _wizard(self, receipt, user=None):
        return self.env["blistercenter.receipt.scan"].with_user(user or self.user_bc).create(
            {"picking_id": receipt.id})

    def _manual(self, wizard, lot="CH-HAND-1", expiry="05/2028", qty=1, product=None):
        wizard.write({
            "manual_product_id": (product or self.product).id, "manual_lot": lot,
            "manual_expiry": expiry, "manual_qty": qty,
        })
        return wizard.action_add_manual()

    # ------------------------------------------------------------------
    # R55 Handeingabe
    # ------------------------------------------------------------------
    def test_manual_entry_books_lot_and_expiry(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self._wizard(receipt)
        self.assertEqual(wizard.allowed_product_ids, self.product)
        action = self._manual(wizard, lot="CH-HAND-1", expiry="05/2028", qty=2)
        self.assertEqual(action["res_id"], wizard.id, "Fenster bleibt offen für die nächste Eingabe")
        self.assertEqual(wizard.message_type, "ok")
        self.assertFalse(wizard.manual_lot, "Eingabefelder für die nächste Charge geleert")
        self.assertEqual(wizard.manual_qty, 1)
        self._manual(wizard, lot="CH-HAND-1", expiry="31.05.2028", qty=1)
        self.assertEqual(len(wizard.line_ids), 1, "Gleiche Charge und gleicher Verfall: Menge addiert")
        self.assertEqual(wizard.line_ids.qty, 3)
        self.assertEqual(wizard.line_ids.expiration_date, date(2028, 5, 31), "MM/JJJJ = Monatsende")
        wizard.action_apply()
        receipt.with_user(self.user_bc).button_validate()
        self.assertEqual(receipt.state, "done")
        lot = receipt.move_line_ids.lot_id
        self.assertEqual(lot.name, "CH-HAND-1")
        self.assertEqual(lot.expiration_date.date(), date(2028, 5, 31))
        self.assertEqual(receipt.move_line_ids.bc_scanned_expiry, date(2028, 5, 31))

    def test_manual_entry_rejects_bad_input(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self._wizard(receipt)
        foreign = self.env["product.product"].create({
            "name": "Fremder Artikel", "is_storable": True, "tracking": "lot", "use_expiration_date": True})
        cases = [
            ({"product": foreign}, "gehört nicht"),
            ({"lot": ""}, "Charge fehlt"),
            ({"lot": "AB 12"}, "ohne Leerzeichen"),
            ({"expiry": ""}, "Verfall fehlt"),
            ({"expiry": "13/2028"}, "ungültig"),
            ({"expiry": "irgendwann"}, "nicht lesbar"),
            ({"qty": 0}, "mindestens 1"),
        ]
        for kwargs, text in cases:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(UserError, text):
                self._manual(wizard, **kwargs)
        self.assertFalse(wizard.line_ids, "Fehleingaben landen nicht in der Liste")
        wizard.write({"manual_product_id": False, "manual_lot": "X1", "manual_expiry": "05/2028"})
        with self.assertRaisesRegex(UserError, "Artikel fehlt"):
            wizard.action_add_manual()

    def test_manual_entry_same_lot_other_expiry_rejected(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self._wizard(receipt)
        self._manual(wizard, lot="CH-HAND-2", expiry="05/2028")
        with self.assertRaisesRegex(UserError, "weicht von der ersten Packung ab"):
            self._manual(wizard, lot="CH-HAND-2", expiry="06/2028")
        self.assertEqual(wizard.line_ids.qty, 1)
        self.assertEqual(wizard.line_ids.expiration_date, date(2028, 5, 31))

    def test_manual_entry_after_apply_or_on_closed_receipt_rejected(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self._wizard(receipt)
        self._manual(wizard, qty=3)
        wizard.action_apply()
        with self.assertRaisesRegex(UserError, "nicht mehr offen"):
            self._manual(wizard, lot="CH-HAND-NEU")
        receipt.with_user(self.user_bc).button_validate()
        self.assertEqual(receipt.state, "done")
        late = self._wizard(receipt)
        with self.assertRaisesRegex(UserError, "nicht mehr offen"):
            self._manual(late, lot="CH-HAND-SPAET")

    def test_manual_and_scan_mixed_in_one_window(self):
        """Erst scannen, dann von Hand ergänzen, dann weiter scannen: alles in einer Liste."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        expiry = fields.Date.today() + timedelta(days=700)
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.scan = self._code("CH-MIX-A", expiry, "M1")
        wizard = form.save()
        self._manual(wizard, lot="CH-MIX-B", expiry="05/2028")
        form = Form(wizard)
        form.scan = self._code("CH-MIX-A", expiry, "M2")
        wizard = form.save()
        self.assertEqual({(l.lot_name, l.qty) for l in wizard.line_ids}, {("CH-MIX-A", 2), ("CH-MIX-B", 1)})
        wizard.action_apply()
        self.assertEqual(sum(receipt.move_line_ids.mapped("quantity")), 3)

    # ------------------------------------------------------------------
    # R57 Code ohne Trennzeichen
    # ------------------------------------------------------------------
    def test_scan_without_gs_ambiguous_prefills_manual_entry(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        expiry = fields.Date.today() + timedelta(days=700)
        # Seriennummer vor Charge, ohne Trennzeichen: Charge nicht sicher lesbar
        code = "01%s21SER1010CH2117%s" % (self.gtin, expiry.strftime("%y%m%d"))
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse(code)
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.scan = code
        self.assertEqual(form.message_type, "error")
        self.assertIn("Charge", form.message)
        self.assertEqual(len(form.line_ids), 0, "Nichts geraten gezählt")
        self.assertEqual(form.manual_product_id, self.product, "Artikel aus dem Code vorbelegt")
        self.assertEqual(form.manual_expiry, expiry.strftime("%m/%Y"), "Verfall aus dem Code vorbelegt")
        self.assertFalse(form.manual_lot, "Charge schreibt das Lager von der Packung ab")

    def test_scan_without_gs_unique_reading_is_counted(self):
        """Alle Lesarten mit Charge und Verfall ergeben dasselbe: Packung zählt (mit Hinweis)."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        code = "01%s21SERX1728033110AB12" % self.gtin
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse(code)
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.scan = code
        self.assertEqual(form.message_type, "warning")
        self.assertIn("ohne Trennzeichen", form.message)
        wizard = form.save()
        self.assertEqual([(l.lot_name, l.expiration_date, l.qty) for l in wizard.line_ids],
                         [("AB12", date(2028, 3, 31), 1)])

    def test_scan_without_gs_foreign_product(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        code = "01%s21SER1010CH2117280331" % gs1.pzn_to_gtin("87654321")
        form = Form(self.env["blistercenter.receipt.scan"].with_context(default_picking_id=receipt.id))
        form.scan = code
        self.assertEqual(form.message_type, "error")
        self.assertIn("87654321", form.message)
        self.assertFalse(form.manual_product_id)

    def test_scan_with_hash_separator_works(self):
        """Scanner, die GS als # ausgeben (Einstellung im Scanner), werden ohne Raten gelesen."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = Form(self.env["blistercenter.receipt.scan"].with_context(default_picking_id=receipt.id))
        form.scan = "01%s21SER10#10CH21#17280331" % self.gtin
        self.assertEqual(form.message_type, "ok")
        self.assertEqual(form.save().line_ids.lot_name, "CH21")

    # ------------------------------------------------------------------
    # 1.6.1 Scanner-Eigenheiten (R59)
    # ------------------------------------------------------------------
    def _scan(self, receipt, code):
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.scan = code
        return form

    def test_scanner_variants_are_read(self):
        """Alt+29 als „↔“, Symbologie-Kennung ]d2 bzw. +d2, FNC1 am Anfang: alle gezählt."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=5)
        variants = {
            "pfeil": "↔".join(["01%s21S1" % self.gtin, "10CH-V1", "17280331"]),
            "aim": "]d2" + "\x1d".join(["01%s21S2" % self.gtin, "10CH-V2", "17280331"]),
            "aim_de": "+d2" + "\x1d".join(["01%s21S3" % self.gtin, "10CH-V3", "17280331"]),
            "fnc1": "\x1d" + "\x1d".join(["01%s21S4" % self.gtin, "10CH-V4", "17280331"]),
            "crlf": "\x1d".join(["01%s21S5" % self.gtin, "10CH-V5", "17280331"]) + "\r\n",
        }
        for name, code in variants.items():
            with self.subTest(variant=name):
                form = self._scan(receipt, code)
                self.assertIn(form.message_type, ("ok", "warning"), form.message)
                self.assertIn("CH-V", form.message)

    def test_scan_into_lot_field_is_treated_as_scan(self):
        """Cursor stand im Feld „Charge“: der Scan landet dort und wird trotzdem gezählt."""
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = Form(self.env["blistercenter.receipt.scan"].with_user(self.user_bc).with_context(
            default_picking_id=receipt.id))
        form.manual_lot = "\x1d".join(["01%s21S9" % self.gtin, "10CH-FELD", "17280331"])
        self.assertFalse(form.manual_lot)
        self.assertIn("CH-FELD", form.message)
        self.assertEqual(form.save().line_ids.lot_name, "CH-FELD")
        form = Form(form.save())
        form.manual_lot = "AB1234"
        self.assertEqual(form.manual_lot, "AB1234", "Normale Charge bleibt stehen")

    def test_scan_error_shows_what_was_read(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        form = self._scan(receipt, "kein\x07Code")
        self.assertEqual(form.message_type, "error")
        self.assertIn("Gelesen: kein‹07›Code", form.message)
        self.assertIn("Kein Trennzeichen", form.message)

    def test_scan_with_wrong_keyboard_layout_hints(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        # US-Scanner auf deutschem PC: aus „-“ wird „ß“, GS fehlt → nicht lesbar
        form = self._scan(receipt, "01%s21SER1010CHß2117280331" % self.gtin)
        self.assertEqual(form.message_type, "error")
        self.assertIn("Tastatursprache", form.message)
        self.assertIn("Gelesen:", form.message)

    # ------------------------------------------------------------------
    # R58 Übersicht
    # ------------------------------------------------------------------
    def test_overview_shows_ordered_and_captured(self):
        _order, receipt = self._open_receipt(self.customer_invoice, qty=3)
        wizard = self._wizard(receipt)
        self.assertIn("fehlt 3", wizard.overview)
        self._manual(wizard, qty=2)
        wizard.invalidate_recordset(["overview"])
        self.assertIn("fehlt 1", wizard.overview)
        self._manual(wizard, qty=1)
        wizard.invalidate_recordset(["overview"])
        self.assertIn("vollständig", wizard.overview)
        self._manual(wizard, qty=1)
        wizard.invalidate_recordset(["overview"])
        self.assertIn("mehr als bestellt", wizard.overview)


@tagged("post_install", "-at_install", "blistercenter")
class TestWareneingangScanUi(ReceiptMixin, BlistercenterCommon, HttpCase):
    """R56 im Browser: Strg+] und Alt+029 als Trennzeichen, Enter, schnelle Scans, Handeingabe."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_receipt_product()

    def test_scan_field_and_manual_entry_in_browser(self):
        self.assertEqual(self.gtin, "04150123456782", "Die Tour scannt diese GTIN fest codiert")
        _order, receipt = self._open_receipt(self.customer_invoice, qty=7)
        self.start_tour("/odoo/action-blistercenter.action_bc_receipts/%s" % receipt.id,
                        "blistercenter_receipt_scan", login=self.user_bc.login)
        lines = receipt.move_line_ids
        self.assertEqual(
            sorted((l.lot_name or l.lot_id.name, l.quantity) for l in lines),
            [("CH-ALT", 1), ("CH-AUTO", 1), ("CH-BODY", 1), ("CH-HAND", 2), ("CH-STRG", 2)],
            "Strg+], Alt+029 und Handeingabe kommen an; kein Scan geht verloren")
        self.assertEqual({l.bc_scanned_expiry for l in lines}, {date(2028, 3, 31), date(2028, 5, 31)})
