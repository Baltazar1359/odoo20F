import datetime

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import BaseCase, TransactionCase, new_test_user, tagged

from ..tools import gs1
from . import test_greenrx_flow as flow
from .test_greenrx_flow import code


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestManualParser(BaseCase):
    """Handeingabe einer Packung: Prüfung der getippten Werte (ohne Odoo-Daten)."""

    def test_valid_pzn_and_formats(self):
        # Seriennummer wird nur einmal eingegeben (Entscheidung 14.10.2026)
        r = gs1.parse_manual("PZN 1234-5678", "AbC123", "BYE-T01", "05/2028")
        self.assertEqual(r["format"], "manual")
        self.assertEqual(r["pzn"], "12345678")
        self.assertEqual(r["gtin"], gs1.pzn_to_gtin("12345678"))
        self.assertEqual(r["serial"], "AbC123")  # Groß-/Kleinschreibung bleibt
        self.assertEqual(r["expiry"], datetime.date(2028, 5, 31))
        self.assertTrue(r["expiry_month_only"])
        r = gs1.parse_manual("04150123456782", "S1", "L1", "15.05.2028")
        self.assertEqual(r["pzn"], "12345678")
        self.assertEqual(r["expiry"], datetime.date(2028, 5, 15))
        self.assertFalse(r["expiry_month_only"])
        for text, expected in (("5/28", datetime.date(2028, 5, 31)), ("05.2028", datetime.date(2028, 5, 31)),
                               ("02/2028", datetime.date(2028, 2, 29))):
            self.assertEqual(gs1.parse_manual_expiry(text)[0], expected, text)
        # echte PZN aus dem GreenRX-Artikelstamm (Prüfziffer-Algorithmus)
        for pzn in ("13570727", "07560038"):
            self.assertTrue(gs1._pzn_check_ok(pzn), pzn)

    def test_rejects_typos_and_bad_values(self):
        cases = [
            (("12345679", "S1", "L1", "05/2028"), "Prüfziffer"),        # PZN vertippt
            (("04150123456783", "S1", "L1", "05/2028"), "Prüfziffer"),  # GTIN vertippt
            (("1234567890", "S1", "L1", "05/2028"), "8 Stellen"),
            (("12A45678", "S1", "L1", "05/2028"), "nur Ziffern"),
            (("12345678", "S 1", "L1", "05/2028"), "Seriennummer"),
            (("12345678", "X" * 21, "L1", "05/2028"), "Seriennummer"),
            (("12345678", "S1", "", "05/2028"), "Charge fehlt"),
            (("12345678", "S1", "L1", "13/2028"), "Monat"),
            (("12345678", "S1", "L1", "31.02.2028"), "kein gültiges Datum"),
            (("12345678", "S1", "L1", "Mai 28"), "MM/JJJJ"),
            (("", "", "", ""), "PZN fehlt"),
        ]
        for args, text in cases:
            with self.assertRaises(gs1.Gs1Error, msg=args) as cm:
                gs1.parse_manual(*args)
            self.assertIn(text, str(cm.exception), args)


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestManualPack(TransactionCase):
    """Packung von Hand eingeben: gleiche Regeln wie beim Scan, Kennzeichnung, Rechte."""

    _ticket = flow.TestGreenrxFlow._ticket
    _all_yes = flow.TestGreenrxFlow._all_yes
    _fill_box = flow.TestGreenrxFlow._fill_box
    _close = flow.TestGreenrxFlow._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="mp_lager", groups="plcs_greenrx.group_grx_user")
        cls.lager2 = new_test_user(cls.env, login="mp_lager2", groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="mp_vp1", groups="plcs_greenrx.group_grx_vp")
        cls.vp2 = new_test_user(cls.env, login="mp_vp2", groups="plcs_greenrx.group_grx_vp")
        cls.stock_only = new_test_user(cls.env, login="mp_stock", groups="stock.group_stock_user")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_date = fields.Date.today() + relativedelta(years=2)
        cls.exp_ok = cls.exp_date.strftime("%y%m00")      # GS1: Tag 00 = Monatsende
        cls.exp_text = cls.exp_date.strftime("%m/%Y")      # Handeingabe

    def _open(self, ticket, box_code="GX150", user=None):
        Box = self.env["plcs.greenrx.box"].with_user(user or self.lager)
        return Box.browse(Box.grx_open_box(ticket.id, box_code)["id"])

    def _manual(self, box, serial, batch="BYE-T01", expiry=None, pzn="12345678"):
        return box.grx_scan_manual(pzn, serial, batch, expiry or self.exp_text)

    # ------------------------------------------------------------------
    def test_manual_entry_same_rules_and_marking(self):
        ticket = self._ticket(name="MP-1", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._open(ticket)
        # erste Packung von Hand: legt Artikel, Charge und Verfall der Box fest
        r = self._manual(box, "HAND1")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["title"], "Packung von Hand erfasst")
        self.assertEqual((box.product_id, box.batch), (self.prod_a, "BYE-T01"))
        scan = box.scan_ids.filtered("accepted")
        self.assertTrue(scan.manual)
        self.assertEqual(scan.code_format, "manual")
        self.assertEqual(box.manual_qty, 1)
        self.assertIn("von Hand eingegeben", box.message_ids[:1].body)
        # danach normaler Scan mit gleichem Verfall (Monatsende) passt und meldet keine Warnung
        r = box.grx_scan(code(self.gtin_a, "SCAN1", "BYE-T01", self.exp_ok))
        self.assertEqual(r["status"], "ok", r)
        # gleiche Regeln wie beim Scan
        self.assertEqual(self._manual(box, "HAND1")["status"], "info")                # Doppelt in der Box
        self.assertEqual(self._manual(box, "SCAN1")["status"], "info")                # gescannte SN getippt
        r = self._manual(box, "HAND2", batch="BYE-T02")
        self.assertEqual((r["status"], r["title"]), ("error", "Andere Charge"))
        r = self._manual(box, "HAND3", pzn="13570727")                                 # gültige PZN, kein Artikel
        self.assertIn("unbekannt", r["title"])
        self.assertEqual(box.qty, 2)
        # Hinweis für Lager und VP, Ampel bleibt frei (Entscheidung: kein Einfluss auf die Ampel)
        hint = [c for c in box._grx_ui_data()["sys_checks"] if c["hint"]]
        self.assertEqual(len(hint), 1)
        self.assertIn("1 von 2 Packungen von Hand", hint[0]["label"])
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "gruen")
        self.assertFalse(box.reasons)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()  # ohne Pflicht-Grund
        html = self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()
        self.assertIn("davon 1 von Hand", html)
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        lots = box.lot_ids
        self.assertEqual(lots.filtered("plcs_grx_manual").mapped("name"), ["HAND1"])
        self.assertEqual((lots - lots.filtered("plcs_grx_manual")).mapped("name"), ["SCAN1"])
        # VP sieht die Handeingabe in der Liste
        ticket.with_user(self.lager).grx_finish()
        vp_box = next(b for b in self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()["boxes"]
                      if b["id"] == box.id)
        self.assertEqual(vp_box["manual_qty"], 1)
        self.assertTrue(vp_box["can_release"])

    def test_known_serial_rejected_for_manual_entry(self):
        ticket = self._ticket(name="MP-2", lines=[{"pzn": "12345678", "qty": 3}])
        box = self._fill_box(ticket, "GX151", self.gtin_a, ["K1"])
        other = self._open(ticket, "GX152")
        r = self._manual(other, "K1")  # liegt schon in offener Box GX151
        self.assertEqual(r["status"], "error")
        self.assertIn("schon erfasst", r["title"])
        rejected = other.scan_ids.filtered(lambda s: not s.accepted)
        self.assertTrue(rejected.manual)  # abgelehnte Handeingabe wird protokolliert
        self.assertEqual(other.qty, 0)
        box.grx_to_check()
        self._close(box)
        r = self._manual(other, "K1")  # jetzt als Seriennummer im System
        self.assertIn("schon im System", r["title"])

    def test_invalid_input_is_rejected_and_nothing_recorded(self):
        ticket = self._ticket(name="MP-3", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._open(ticket)
        with self.assertRaises(UserError):
            self._manual(box, "TYPO 1")  # Leerzeichen in der SN
        with self.assertRaises(UserError):
            self._manual(box, "TYPO1", pzn="12345679")  # Prüfziffer
        with self.assertRaises(UserError):
            self._manual(box, "TYPO1", expiry="13/2028")
        self.assertEqual(box.qty, 0)
        self.assertFalse(box.scan_ids)
        self.assertFalse(box.product_id)

    def test_month_only_expiry_matches_box_with_exact_day(self):
        ticket = self._ticket(name="MP-4", lines=[{"pzn": "12345678", "qty": 2}])
        exact = self.exp_date.replace(day=15)
        box = self._fill_box(ticket, "GX153", self.gtin_a, ["D1"], exp=exact.strftime("%y%m%d"))
        self.assertEqual(box.expiry_date, exact)
        r = self._manual(box, "D2", expiry=exact.strftime("%m/%Y"))
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(box.scan_ids.filtered(lambda s: s.serial == "D2").expiry_date, exact)
        # anderer Monat bleibt abgelehnt
        r = self._manual(box, "D3", expiry=(exact + relativedelta(months=1)).strftime("%m/%Y"))
        self.assertEqual((r["status"], r["title"]), ("error", "Verfall weicht ab"))

    def test_manual_entry_rights_and_state(self):
        ticket = self._ticket(name="MP-5", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._open(ticket)
        with self.assertRaises(AccessError):  # ohne GreenRX-Rolle
            self._manual(box.with_user(self.stock_only), "R1")
        with self.assertRaises(AccessError):  # fremde Box
            self._manual(box.with_user(self.lager2), "R1")
        self.assertEqual(self._manual(box, "R1")["status"], "ok")
        box.grx_to_check()
        with self.assertRaises(UserError):  # nur im Schritt Packungen
            self._manual(box, "R2")

    def test_vp_with_manual_entry_cannot_release(self):
        ticket = self._ticket(name="MP-6", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX154", self.gtin_a, ["V1"])
        self.assertEqual(self._manual(box.with_user(self.vp1), "V2")["status"], "ok")
        box.grx_to_check()
        self._close(box)
        ticket.with_user(self.lager).grx_finish()
        with self.assertRaises(AccessError):
            box.with_user(self.vp1).grx_vp_release()
        box.with_user(self.vp2).grx_vp_release()
        self.assertEqual(box.state, "released")
