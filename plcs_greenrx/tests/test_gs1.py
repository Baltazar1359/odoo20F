import datetime

from odoo.tests import BaseCase, tagged

from ..tools import gs1, slip

GS = "\x1d"


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGs1(BaseCase):

    def setUp(self):
        super().setUp()
        self.gtin = gs1.pzn_to_gtin("12345678")

    def test_pzn_gtin_roundtrip(self):
        self.assertEqual(self.gtin, "04150123456782")
        self.assertTrue(gs1.gtin_check_digit_ok(self.gtin))
        self.assertEqual(gs1.gtin_to_pzn(self.gtin), "12345678")

    def test_gs1_with_separator(self):
        r = gs1.parse("01" + self.gtin + "21TSTPLCS00028" + GS + "10BYE-T01" + GS + "17280300")
        self.assertEqual(r["serial"], "TSTPLCS00028")
        self.assertEqual(r["batch"], "BYE-T01")
        self.assertEqual(r["expiry"], datetime.date(2028, 3, 31))
        self.assertEqual(r["pzn"], "12345678")
        self.assertFalse(r["heuristic"])

    def test_gs1_alias_separators_and_symbology(self):
        for sep in ("#", "Alt029", "<GS>"):
            r = gs1.parse("]d201" + self.gtin + "17280315" + "10BYE-T01" + sep + "21SN1")
            self.assertEqual((r["batch"], r["serial"]), ("BYE-T01", "SN1"))
            self.assertEqual(r["expiry"], datetime.date(2028, 3, 15))

    def test_brackets(self):
        r = gs1.parse("(01)%s(21)ABC(10)L1(17)271200" % self.gtin)
        self.assertEqual((r["serial"], r["batch"]), ("ABC", "L1"))
        self.assertEqual(r["expiry"], datetime.date(2027, 12, 31))

    def test_missing_separator_ambiguous(self):
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse("01" + self.gtin + "21TSTPLCS00028" + "10BYE-T01" + "17280300")

    def test_missing_separator_variable_last(self):
        r = gs1.parse("01" + self.gtin + "17280300" + "21TSTPLCS00028")
        self.assertEqual(r["serial"], "TSTPLCS00028")
        self.assertFalse(r["heuristic"])

    def test_ifa(self):
        r = gs1.parse("[)>\x1e06\x1d9N111234567842\x1d1TBYE-T01\x1dD280300\x1dSTSTPLCS00028\x1e\x04")
        self.assertEqual(r["format"], "ifa")
        self.assertEqual(r["pzn"], "12345678")
        self.assertEqual(r["serial"], "TSTPLCS00028")
        self.assertEqual(r["expiry"], datetime.date(2028, 3, 31))

    def test_invalid(self):
        for code in ("", "hallo", "0104150123456783" + "21X"):  # falsche Prüfziffer
            with self.assertRaises(gs1.Gs1Error):
                gs1.parse(code)

    def test_box_code(self):
        self.assertEqual(gs1.parse_box_code("GX008"), "GX008")
        self.assertEqual(gs1.parse_box_code("gx008"), "GX008")
        self.assertFalse(gs1.parse_box_code("12"))
        self.assertEqual(gs1.parse_box_code("12", allow_number_only=True), "GX012")
        self.assertFalse(gs1.parse_box_code("GX0081"))


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestSlip(BaseCase):
    RAW = ("REF:V202601693\nREKL:S23124\nTICKET|PZN|CHARGE|ANZAHL\n"
           "1001982|13570727|214124|4\n1001983|07560038|33t32|3")

    def test_parse(self):
        r = slip.parse(self.RAW)
        self.assertEqual(r["reference"], "V202601693")
        self.assertEqual(r["complaint"], "S23124")
        self.assertEqual(r["lines"][1], {"ticket": "1001983", "pzn": "07560038", "batch": "33t32", "qty": 3})

    def test_parse_split_by_scanner(self):
        # Odoo trennt Tastatur-Scans an Enter und "|" – wieder zusammengesetzt mit Zeilenumbruch
        r = slip.parse(self.RAW.replace("|", "\n").replace("\n", "\r\n"))
        self.assertEqual(len(r["lines"]), 2)

    def test_parse_without_linebreaks_fails_clearly(self):
        with self.assertRaises(slip.SlipError):
            slip.parse(self.RAW.replace("\n", ""))

    def test_not_a_slip(self):
        self.assertFalse(slip.looks_like_slip("0104150123456782"))
        with self.assertRaises(slip.SlipError):
            slip.parse("GX001")
