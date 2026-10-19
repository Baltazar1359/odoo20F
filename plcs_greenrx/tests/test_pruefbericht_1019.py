"""Prüfbericht 19.10.2026 (externes Audit): GreenRX-Befunde R08–R11 als Regressionstests."""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import gs1
from . import test_greenrx_flow as flow


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestPruefbericht1019Greenrx(TransactionCase):

    _ticket = flow.TestGreenrxFlow._ticket

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="grx_lager_r", groups="plcs_greenrx.group_grx_user")
        cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": gs1.pzn_to_gtin("12345678"), "plcs_pzn": "12345678",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.company_b = cls.env["res.company"].create({"name": "Andere Firma (Test)"})
        cls.manager_a = new_test_user(
            cls.env, login="grx_mgr_a", groups="stock.group_stock_manager,plcs_greenrx.group_grx_vp",
            company_id=cls.company.id, company_ids=[(6, 0, cls.company.ids)])

    def _ticket_b(self, name):
        Ticket = self.env["plcs.greenrx.ticket"].with_company(self.company_b)
        res = Ticket.api_upsert([{
            "external_id": name, "name": name, "complaint_number": "S-" + name, "credit_note": "GS-" + name,
            "partner_name": "Linden-Apotheke", "customer_number": "K-10234",
            "lines": [{"pzn": "12345678", "qty": 1}],
        }])
        self.assertFalse(res["errors"], res)
        ticket = Ticket.sudo().search([("name", "=", name), ("company_id", "=", self.company_b.id)])
        self.assertTrue(ticket)
        return ticket

    # R08 -----------------------------------------------------------------
    def test_r08_override_cannot_touch_other_company(self):
        ticket_b = self._ticket_b("V-FREMD-1")
        ticket_b.write({"state": "cancel"})
        with self.assertRaises(AccessError):
            ticket_b.with_user(self.manager_a).action_ativ_override()
        self.assertEqual(ticket_b.state, "cancel", "Fremder Vorgang bleibt unverändert")
        ticket_b.write({"ativ_override": True})
        with self.assertRaises(AccessError):
            ticket_b.with_user(self.manager_a).action_ativ_release_override()
        self.assertTrue(ticket_b.ativ_override)
        # Gegenprobe: eigene Firma geht weiter
        own = self._ticket("V-EIGEN-1")
        own.sudo().write({"state": "cancel"})
        own.with_user(self.manager_a).action_ativ_override()
        self.assertEqual(own.state, "open")

    # R09 -----------------------------------------------------------------
    def test_r09_second_company_uses_own_box_prefix(self):
        """Boxen sind firmenübergreifend eindeutig; eine zweite Firma bekommt ein eigenes Präfix."""
        self.company_b.write({"plcs_grx_box_prefix": "GY", "plcs_grx_box_max": 3})
        created = self.env["stock.package"].sudo().plcs_grx_ensure_boxes(self.company_b)
        self.assertEqual(created, 3)
        names = self.env["stock.package"].sudo().search([("name", "like", "GY%")]).mapped("name")
        self.assertEqual(sorted(names), ["GY001", "GY002", "GY003"])
        self.assertEqual(self.env["stock.package"].sudo().plcs_grx_ensure_boxes(self.company_b), 0,
                         "Mehrfach ausführbar, keine Duplikate")

    # R10 -----------------------------------------------------------------
    RS, GS = "\x1e", "\x1d"

    def _ifa(self, *parts):
        return "[)>" + self.RS + "06" + self.GS + self.GS.join(parts) + self.RS + "\x04"

    def test_r10_short_ifa_gtin_rejected(self):
        with self.assertRaisesRegex(gs1.Gs1Error, "GTIN .* ungültig"):
            gs1.parse(self._ifa("8P0", "1TLOT", "D280300", "SX1"))

    def test_r10_ifa_gtin_and_ppn_must_match(self):
        other_gtin = gs1.pzn_to_gtin("10126593")
        with self.assertRaisesRegex(gs1.Gs1Error, "widersprüchlich"):
            gs1.parse(self._ifa("9N111234567842", "8P" + other_gtin, "1TLOT", "D280300", "SX1"))
        # Gegenprobe: passende GTIN und PPN
        res = gs1.parse(self._ifa("9N111234567842", "8P04150123456782", "1TLOT", "D280300", "SX1"))
        self.assertEqual(res["pzn"], "12345678")
        # nur GTIN: PZN kommt aus der GTIN
        res = gs1.parse(self._ifa("8P04150123456782", "1TLOT", "D280300", "SX1"))
        self.assertEqual(res["pzn"], "12345678")

    def test_r10_gs1_ntin_and_nhrn_must_match(self):
        with self.assertRaisesRegex(gs1.Gs1Error, "widersprüchlich"):
            gs1.parse("(01)04150123456782(21)SN1(10)LOT(17)280300(710)10126593")
        res = gs1.parse("(01)04150123456782(21)SN1(10)LOT(17)280300(710)12345678")
        self.assertEqual(res["pzn"], "12345678")

    def test_r10_broken_bracket_rest_rejected(self):
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse("(01)04150123456782(21)SN1(10)LOT(17)280300(BROKEN")
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse("MÜLL(01)04150123456782(21)SN1")
        res = gs1.parse("(01)04150123456782(21)SN1(10)LOT(17)280300")
        self.assertEqual(res["serial"], "SN1")

    # R11 -----------------------------------------------------------------
    def test_r11_auto_create_rejects_invalid_pzn(self):
        Product = self.env["product.product"]
        self.assertFalse(Product._plcs_ensure_from_ativ(pzn="12345679", name="Falsche PZN"))
        self.assertFalse(Product.search([("plcs_pzn", "=", "12345679")]))
        # Gegenprobe: gültige PZN wird angelegt
        product = Product._plcs_ensure_from_ativ(pzn="10126587", name="XIGDUO 5/1000 mg 56 St (Test)")
        self.assertEqual(product.plcs_pzn, "10126587")
        self.assertEqual(product.barcode, gs1.pzn_to_gtin("10126587"))
