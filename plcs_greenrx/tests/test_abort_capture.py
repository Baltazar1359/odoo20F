"""1.10.0: „Erfassung abbrechen“ – Vorgang ohne Box zurück auf „Offen“."""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import gs1
from . import test_greenrx_flow as flow


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxAbortCapture(TransactionCase):

    _ticket = flow.TestGreenrxFlow._ticket
    _all_yes = flow.TestGreenrxFlow._all_yes
    _fill_box = flow.TestGreenrxFlow._fill_box
    _close = flow.TestGreenrxFlow._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="grx_lager_abort", groups="plcs_greenrx.group_grx_user")
        cls.lager2 = new_test_user(cls.env, login="grx_lager_abort2", groups="plcs_greenrx.group_grx_user")
        cls.vp = new_test_user(cls.env, login="grx_vp_abort", groups="plcs_greenrx.group_grx_vp")
        cls.stock_only = new_test_user(cls.env, login="grx_stock_abort", groups="stock.group_stock_user")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.env["product.product"].create({
            "name": "Artikel B", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "plcs_pzn": "87654321",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")

    def _started(self, name, user=None):
        ticket = self._ticket(name=name, lines=[{"pzn": "12345678", "qty": 3}])
        ticket.with_user(user or self.lager).grx_start()
        self.assertEqual(ticket.state, "in_progress")
        self.assertTrue(ticket.picking_id)
        return ticket

    # ------------------------------------------------------------------
    def test_abort_without_box(self):
        """Gutfall: Retourenschein gescannt, keine Box → Vorgang wieder offen, Eingang storniert."""
        ticket = self._started("T-AB1")
        picking = ticket.picking_id
        self.assertTrue(ticket.with_user(self.lager).grx_abort())
        self.assertEqual(ticket.state, "open")
        self.assertFalse(ticket.picking_id)
        self.assertEqual(picking.state, "cancel")
        self.assertEqual(picking.plcs_grx_ticket_id, ticket, "Stornierter Eingang bleibt am Vorgang (Nachweis)")
        msg = ticket.message_ids.filtered(lambda m: "Erfassung abgebrochen" in (m.body or ""))[:1]
        self.assertTrue(msg)
        self.assertIn(picking.name, msg.body)
        self.assertIn(self.lager.name, msg.body)
        # Startseite: nicht mehr in Erfassung
        home = self.env["plcs.greenrx.ticket"].with_user(self.lager).grx_home()
        self.assertNotIn(ticket.id, [r["id"] for r in home["in_progress"]])

    def test_restart_after_abort(self):
        """Nach dem Abbruch lässt sich der Vorgang normal erfassen: neuer Eingang, kein Nachtrag."""
        ticket = self._started("T-AB2")
        old = ticket.picking_id
        ticket.with_user(self.lager).grx_abort()
        ticket.with_user(self.lager2).grx_start()
        self.assertEqual(ticket.state, "in_progress")
        self.assertTrue(ticket.picking_id)
        self.assertNotEqual(ticket.picking_id, old)
        self.assertNotIn("Nachtrag", ticket.picking_id.origin or "")
        box = self._fill_box(ticket, "GX121", self.gtin_a, ["SNAB21", "SNAB22", "SNAB23"], user=self.lager2)
        box.grx_to_check()
        self._close(box)
        self.assertEqual(box.state, "to_release")
        # Nur der neue Eingang wird gebucht; der stornierte bleibt storniert
        ticket.with_user(self.lager2).grx_finish()
        self.assertEqual(ticket.state, "done")
        self.assertEqual(ticket.picking_id.state, "done")
        self.assertEqual(old.state, "cancel")

    def test_abort_with_open_box_fails(self):
        """Negativtest: offene Box → Abbruch scheitert; nach „Box abbrechen“ geht es."""
        ticket = self._started("T-AB3")
        box = self._fill_box(ticket, "GX122", self.gtin_a, ["SNAB31"])
        with self.assertRaisesRegex(UserError, "GX122"):
            ticket.with_user(self.lager).grx_abort()
        self.assertEqual(ticket.state, "in_progress")
        self.assertNotEqual(ticket.picking_id.state, "cancel")
        box.grx_cancel()
        ticket.with_user(self.lager).grx_abort()
        self.assertEqual(ticket.state, "open")

    def test_abort_with_closed_box_fails(self):
        """Negativtest: abgeschlossene Box → Abbruch scheitert, Eingang und Box unverändert."""
        ticket = self._started("T-AB4")
        box = self._fill_box(ticket, "GX123", self.gtin_a, ["SNAB41", "SNAB42", "SNAB43"])
        box.grx_to_check()
        self._close(box)
        picking = ticket.picking_id
        with self.assertRaises(UserError):
            ticket.with_user(self.lager).grx_abort()
        self.assertEqual(ticket.state, "in_progress")
        self.assertEqual(ticket.picking_id, picking)
        self.assertNotEqual(picking.state, "cancel")
        self.assertEqual(box.state, "to_release")

    def test_abort_wrong_state_fails(self):
        """Negativtest: offener (nie gestarteter) und erledigter Vorgang lassen sich nicht abbrechen."""
        ticket = self._ticket(name="T-AB5")
        with self.assertRaisesRegex(UserError, "nicht in Erfassung"):
            ticket.with_user(self.lager).grx_abort()
        done = self._started("T-AB6")
        box = self._fill_box(done, "GX124", self.gtin_a, ["SNAB61", "SNAB62", "SNAB63"])
        box.grx_to_check()
        self._close(box)
        done.with_user(self.lager).grx_finish()
        self.assertEqual(done.state, "done")
        with self.assertRaisesRegex(UserError, "nicht in Erfassung"):
            done.with_user(self.lager).grx_abort()
        self.assertEqual(done.picking_id.state, "done")

    def test_abort_requires_greenrx_role(self):
        """Negativtest: Lagerbenutzer ohne GreenRX-Rolle (direkter RPC-Aufruf) → AccessError."""
        ticket = self._started("T-AB7")
        with self.assertRaises(AccessError):
            ticket.with_user(self.stock_only).grx_abort()
        self.assertEqual(ticket.state, "in_progress")
        self.assertNotEqual(ticket.picking_id.state, "cancel")

    def test_abort_other_company_fails(self):
        """Negativtest: Vorgang einer fremden Firma → AccessError."""
        company_b = self.env["res.company"].create({"name": "Andere Firma (Abbruch-Test)"})
        Ticket = self.env["plcs.greenrx.ticket"].with_company(company_b)
        res = Ticket.api_upsert([{
            "external_id": "T-AB8", "name": "T-AB8", "partner_name": "Linden-Apotheke",
            "lines": [{"pzn": "12345678", "qty": 1}],
        }])
        self.assertFalse(res["errors"], res)
        ticket = Ticket.sudo().search([("name", "=", "T-AB8"), ("company_id", "=", company_b.id)])
        self.assertTrue(ticket)
        ticket.sudo().state = "in_progress"
        with self.assertRaises(AccessError):
            ticket.with_user(self.lager).grx_abort()
        self.assertEqual(ticket.state, "in_progress")

    def test_vp_and_other_user_may_abort(self):
        """Ohne Box darf jede GreenRX-Rolle abbrechen (auch die VP, auch ein anderer Erfasser)."""
        ticket = self._started("T-AB9", user=self.lager)
        ticket.with_user(self.lager2).grx_abort()
        self.assertEqual(ticket.state, "open")
        ticket.with_user(self.lager).grx_start()
        ticket.with_user(self.vp).grx_abort()
        self.assertEqual(ticket.state, "open")

    def test_ui_flag(self):
        """Scan-App: Knopf nur ohne Box (Feld abortable)."""
        ticket = self._started("T-AB10")
        self.assertTrue(ticket.with_user(self.lager).grx_data()["abortable"])
        self._fill_box(ticket, "GX125", self.gtin_a, ["SNAB101"])
        self.assertFalse(ticket.with_user(self.lager).grx_data()["abortable"])
