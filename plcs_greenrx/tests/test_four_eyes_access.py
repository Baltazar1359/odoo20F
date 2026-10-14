from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import gs1
from . import test_greenrx_flow as flow
from .test_greenrx_flow import code


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxFourEyesAccess(TransactionCase):
    """Vier-Augen-Prinzip bei der VP-Freigabe, Rollen- und Firmenprüfung, Druckberechtigung.

    Negativtests: Jeder Fehlversuch muss mit AccessError scheitern.
    """

    # Hilfsfunktionen aus dem Ablauf-Test wiederverwenden (ohne dessen Tests erneut auszuführen)
    _ticket = flow.TestGreenrxFlow._ticket
    _all_yes = flow.TestGreenrxFlow._all_yes
    _fill_box = flow.TestGreenrxFlow._fill_box
    _close = flow.TestGreenrxFlow._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="fa_lager", groups="plcs_greenrx.group_grx_user")
        cls.lager2 = new_test_user(cls.env, login="fa_lager2", groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="fa_vp1", groups="plcs_greenrx.group_grx_vp")
        cls.vp2 = new_test_user(cls.env, login="fa_vp2", groups="plcs_greenrx.group_grx_vp")
        cls.stock_only = new_test_user(cls.env, login="fa_stock", groups="stock.group_stock_user")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")

    def _scan(self, box, user, serial):
        res = box.with_user(user).grx_scan(code(self.gtin_a, serial, "BYE-T01", self.exp_ok))
        self.assertIn(res["status"], ("ok", "warning"), res)
        return res

    def _finish(self, ticket):
        ticket.with_user(self.lager).grx_finish()

    # ------------------------------------------------------------------
    # 1. Vier-Augen-Prinzip
    # ------------------------------------------------------------------
    def test_vp_scanned_in_foreign_box_cannot_release(self):
        ticket = self._ticket(name="FA-1", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX101", self.gtin_a, ["A1"])
        self._scan(box, self.vp1, "A2")  # VP legt eine Packung in die Box von lager
        box.grx_to_check()
        self._close(box)
        self._finish(ticket)
        self.assertIn(self.vp1, box._grx_participants())
        with self.assertRaises(AccessError):
            box.with_user(self.vp1).grx_vp_release()
        box.with_user(self.vp2).grx_vp_release()
        self.assertEqual(box.state, "released")
        self.assertEqual(box.vp_user_id, self.vp2)

    def test_vp_only_did_check_cannot_release(self):
        ticket = self._ticket(name="FA-2", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX102", self.gtin_a, ["B1"])
        box.grx_to_check()
        vp_box = box.with_user(self.vp1)
        self._all_yes(vp_box)
        vp_box.grx_save_check(ampel="gruen")
        vp_box.grx_to_print()
        self.assertEqual(box.checked_by, self.vp1)
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self._finish(ticket)
        with self.assertRaises(AccessError):
            box.with_user(self.vp1).grx_vp_release()
        box.with_user(self.vp2).grx_vp_release()
        self.assertEqual(box.state, "released")

    def test_vp_only_closed_cannot_release(self):
        ticket = self._ticket(name="FA-3", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX103", self.gtin_a, ["C1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        self.assertNotIn(self.vp2, box._grx_participants())
        box.with_user(self.vp2).grx_close()  # VP schließt die Box von lager ab
        self.assertEqual(box.closed_by, self.vp2)
        self.assertEqual(box.checked_by, self.lager)
        self._finish(ticket)
        with self.assertRaises(AccessError):
            box.with_user(self.vp2).grx_vp_release()
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")

    def test_vp_removed_scan_cannot_release(self):
        ticket = self._ticket(name="FA-4", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX104", self.gtin_a, [])
        res = self._scan(box, self.vp1, "D9")
        box.with_user(self.vp1).grx_remove_scan(res["scan_id"])
        self.assertFalse(box.accepted_scan_ids, "Packung wieder entfernt")
        self._scan(box, self.lager, "D1")
        box.grx_to_check()
        self._close(box)
        self._finish(ticket)
        self.assertIn(self.vp1, box._grx_participants())
        with self.assertRaises(AccessError):
            box.with_user(self.vp1).grx_vp_release()

    def test_vp_rejected_scan_counts_as_participation(self):
        """Auch ein abgelehnter Scan (z. B. andere Charge) zählt als Beteiligung."""
        ticket = self._ticket(name="FA-5", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX105", self.gtin_a, ["E1"])
        res = box.with_user(self.vp1).grx_scan(code(self.gtin_a, "E9", "ANDERE", self.exp_ok))
        self.assertEqual(res["status"], "error")
        box.grx_to_check()
        self._close(box)
        self._finish(ticket)
        with self.assertRaises(AccessError):
            box.with_user(self.vp1).grx_vp_release()

    def test_participant_vp_may_block_but_not_release(self):
        ticket = self._ticket(name="FA-6", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX106", self.gtin_a, ["F1"], user=self.vp1)
        box = box.with_user(self.vp1)
        box.grx_to_check()
        self._close(box)
        self._finish(ticket)
        listing = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        row = next(b for b in listing["boxes"] if b["id"] == box.id)
        self.assertFalse(row["can_release"], "Beteiligte VP sieht die Box nur zum Sperren")
        self.assertEqual(listing["own_count"], 1)
        home = self.env["plcs.greenrx.ticket"].with_user(self.vp1).grx_home()
        self.assertEqual(home["vp_count"], 0, "Startseite zählt nur freigebbare Boxen")
        # Sperren durch beteiligte VP ist erlaubt und wird vermerkt
        box.grx_vp_block("Umkarton nass")
        self.assertEqual(box.state, "blocked")
        self.assertTrue(any("beteiligte VP" in (m.body or "") for m in box.message_ids))
        # Freigabe danach nur durch eine andere VP (mit Begründung, weil gesperrt)
        with self.assertRaises(AccessError):
            box.grx_vp_release(note="doch ok")
        box.with_user(self.vp2).grx_vp_release(note="Packungen geprüft, trocken")
        self.assertEqual(box.state, "released")
        self.assertEqual(box.vp_user_id, self.vp2)

    def test_uninvolved_vp_releases(self):
        ticket = self._ticket(name="FA-7", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX107", self.gtin_a, ["G1"])
        box.grx_to_check()
        self._close(box)
        self._finish(ticket)
        self.assertEqual(box._grx_participants(), self.lager)
        listing = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        row = next(b for b in listing["boxes"] if b["id"] == box.id)
        self.assertTrue(row["can_release"])
        self.assertEqual(listing["own_count"], 0)
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")

    # ------------------------------------------------------------------
    # 2. Rollen- und Firmenprüfung
    # ------------------------------------------------------------------
    def test_stock_user_without_greenrx_role_is_rejected(self):
        ticket = self._ticket(name="FA-8", lines=[{"pzn": "12345678", "qty": 1}])
        Box = self.env["plcs.greenrx.box"].with_user(self.stock_only)
        T = self.env["plcs.greenrx.ticket"].with_user(self.stock_only)
        with self.assertRaises(AccessError):
            Box.grx_open_box(ticket.id, "GX108")
        with self.assertRaises(AccessError):
            T.browse(ticket.id).grx_start()
        with self.assertRaises(AccessError):
            T.grx_search("FA-8")
        with self.assertRaises(AccessError):
            T.grx_home()
        with self.assertRaises(AccessError):
            T.grx_from_slip("REF:FA-8")
        box = self._fill_box(ticket, "GX108", self.gtin_a, ["H1"])
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_scan(code(self.gtin_a, "H2", "BYE-T01", self.exp_ok))
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_to_check()
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_cancel()
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_close()
        box.grx_close()
        with self.assertRaises(AccessError):
            T.browse(ticket.id).grx_finish()
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_vp_release()
        # Gegenprobe: mit Rolle geht es
        self._finish(ticket)
        self.assertEqual(ticket.state, "done")

    def test_other_company_is_rejected(self):
        other = self.env["res.company"].create({"name": "Fremdfirma GmbH"})
        ticket2 = self.env["plcs.greenrx.ticket"].create({"name": "FA-FREMD", "company_id": other.id})
        box2 = self.env["plcs.greenrx.box"].sudo().create({
            "ticket_id": ticket2.id,
            "package_id": self.env["stock.package"].search([("name", "=", "GX199")], limit=1).id,
            "user_id": self.lager.id,
        })
        self.assertEqual(box2.company_id, other)
        with self.assertRaises(AccessError):
            self.env["plcs.greenrx.box"].with_user(self.lager).grx_open_box(ticket2.id, "GX198")
        with self.assertRaises(AccessError):
            self.env["plcs.greenrx.ticket"].with_user(self.lager).browse(ticket2.id).grx_start()
        with self.assertRaises(AccessError):
            box2.with_user(self.lager).grx_scan(code(self.gtin_a, "X1", "BYE-T01", self.exp_ok))
        with self.assertRaises(AccessError):
            box2.with_user(self.lager).grx_print()

    # ------------------------------------------------------------------
    # 3. Druckberechtigung
    # ------------------------------------------------------------------
    def test_print_only_by_owner_or_vp(self):
        ticket = self._ticket(name="FA-9", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX109", self.gtin_a, ["J1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        with self.assertRaises(AccessError):
            box.with_user(self.lager2).grx_print()
        self.assertEqual(box.print_count, 0, "Fehlversuch zählt keinen Druck")
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self._finish(ticket)
        # Nachdruck nach dem Abschluss: Erfasser und VP ja, fremder Lagerbenutzer nein
        with self.assertRaises(AccessError):
            box.with_user(self.lager2).grx_print()
        box.grx_print()
        box.with_user(self.vp1).grx_print()
        self.assertEqual(box.print_count, 3)
        # Ein Nachdruck durch die VP macht sie nicht zur Beteiligten
        self.assertNotIn(self.vp1, box._grx_participants())
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")

    def test_internal_superuser_calls_unaffected(self):
        """Migration und interne Aufrufe (Superuser) laufen ohne Rollenprüfung und ohne Beteiligung."""
        ticket = self._ticket(name="FA-10", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX110", self.gtin_a, ["K1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        before = box._grx_participants()
        self.assertEqual(self.env.uid, SUPERUSER_ID)
        self.env["plcs.greenrx.box"].browse(box.id)._grx_changed()  # wie in der Migration
        self.assertEqual(box._grx_participants(), before)
        with self.assertRaises(UserError):
            box.grx_close()  # Ausdruck ungültig, nicht gedruckt seit Änderung
