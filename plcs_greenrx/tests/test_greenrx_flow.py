import importlib.util
import os

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import gs1

GS = "\x1d"


def code(gtin, serial, batch, exp):
    return "01" + gtin + "21" + serial + GS + "10" + batch + GS + "17" + exp


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.cfg = cls.company
        cls.lager = new_test_user(cls.env, login="grx_lager", groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="grx_vp1", groups="plcs_greenrx.group_grx_vp")
        cls.vp2 = new_test_user(cls.env, login="grx_vp2", groups="plcs_greenrx.group_grx_vp")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.gtin_b = gs1.pzn_to_gtin("87654321")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.prod_b = cls.env["product.product"].create({
            "name": "Artikel B", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "plcs_pzn": "87654321",
        })
        cls.partner = cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")
        cls.exp_short = (fields.Date.today() + relativedelta(months=1)).strftime("%y%m00")

    def _ticket(self, name="T-0826", lines=None):
        res = self.env["plcs.greenrx.ticket"].api_upsert([{
            "external_id": name, "name": name, "complaint_number": "S-" + name, "credit_note": "GS-" + name,
            "partner_name": "Linden-Apotheke", "customer_number": "K-10234",
            "lines": lines if lines is not None else [{"pzn": "12345678", "qty": 3}, {"pzn": "87654321", "qty": 2}],
        }])
        self.assertFalse(res["errors"], res)
        return self.env["plcs.greenrx.ticket"].search([("name", "=", name)])

    def _all_yes(self, box, **override):
        answers = {f: "ja" for f in ("q_damage", "q_temp_controlled", "q_temp_log",
                                     "q_manufacturer", "q_storage_proof", "q_seal")}
        answers.update(override)
        return box.grx_save_check(answers=answers, ampel=None, note=None)

    def _fill_box(self, ticket, box_code, product_gtin, serials, batch="BYE-T01", exp=None, user=None):
        Box = self.env["plcs.greenrx.box"].with_user(user or self.lager)
        data = Box.grx_open_box(ticket.id, box_code)
        box = Box.browse(data["id"])
        for sn in serials:
            res = box.grx_scan(code(product_gtin, sn, batch, exp or self.exp_ok))
            self.assertIn(res["status"], ("ok", "warning"), res)
        return box

    def _close(self, box, ampel="gruen", note=False, **answers):
        self._all_yes(box, **answers)
        box.grx_save_check(ampel=ampel, note=note)
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()

    # ------------------------------------------------------------------
    def test_setup(self):
        self.assertTrue(self.cfg.plcs_grx_quarantine_location_id)
        self.assertEqual(self.cfg.plcs_grx_receipt_type_id.sequence_code, "GRXIN")
        boxes = self.env["stock.package"].search([("plcs_is_grx_box", "=", True)])
        self.assertEqual(len(boxes), 200)
        self.assertEqual(self.company._plcs_grx_setup(), True)  # idempotent
        self.assertEqual(len(self.env["stock.package"].search([("plcs_is_grx_box", "=", True)])), 200)

    def test_ticket_import_and_search(self):
        ticket = self._ticket()
        self.assertEqual(ticket.partner_id, self.partner)
        self.assertEqual(ticket.line_ids.product_id, self.prod_a | self.prod_b)
        self.assertEqual(ticket.qty_expected_total, 5)
        T = self.env["plcs.greenrx.ticket"].with_user(self.lager)
        self.assertEqual(T.grx_search("linden")[0]["id"], ticket.id)
        self.assertEqual(T.grx_search("k 10234")[0]["id"], ticket.id)
        self.assertEqual(T.grx_search("T-0826")[0]["match"], "Treffer: Referenz")
        self.assertEqual(T.grx_search("S-T-0826")[0]["match"], "Treffer: Reklamation")
        self.assertEqual(T.grx_search("byetta")[0]["match"], "Treffer: Medikament")
        # Update vor Beginn ersetzt Positionen, danach nur Kopfdaten
        self.env["plcs.greenrx.ticket"].api_upsert([{"external_id": "T-0826", "name": "T-0826",
                                                    "lines": [{"pzn": "12345678", "qty": 4}]}])
        self.assertEqual(ticket.qty_expected_total, 4)

    def test_full_flow_green_release(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 3}])
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        data = Box.grx_open_box(ticket.id, "GX001")
        box = Box.browse(data["id"])
        self.assertEqual(ticket.state, "in_progress")
        self.assertTrue(ticket.picking_id)
        r = box.grx_scan(code(self.gtin_a, "SN1", "BYE-T01", self.exp_ok))
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual((box.product_id, box.batch), (self.prod_a, "BYE-T01"))
        # Doppelscan: Info, nicht gezählt
        r = box.grx_scan(code(self.gtin_a, "SN1", "BYE-T01", self.exp_ok))
        self.assertEqual(r["status"], "info")
        # andere Charge abgelehnt
        r = box.grx_scan(code(self.gtin_a, "SN9", "BYE-T02", self.exp_ok))
        self.assertEqual(r["status"], "error")
        self.assertIn("Andere Charge", r["title"])
        # anderer Artikel abgelehnt
        r = box.grx_scan(code(self.gtin_b, "SN8", "X1", self.exp_ok))
        self.assertEqual(r["status"], "error")
        r = box.grx_scan(code(self.gtin_a, "SN2", "BYE-T01", self.exp_ok))
        r = box.grx_scan(code(self.gtin_a, "SN3", "BYE-T01", self.exp_ok))
        self.assertEqual(box.qty, 3)
        self.assertEqual(len(box.scan_ids), 6)  # alle Scans protokolliert
        # Box-Code statt Packung
        self.assertEqual(box.grx_scan("GX002")["status"], "error")
        # Vorprüfung
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "gruen")
        with self.assertRaises(UserError):
            box.grx_to_print()  # Ampel fehlt
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        with self.assertRaises(UserError):
            box.grx_close()  # nicht gedruckt
        action = box.grx_print()
        self.assertEqual(action["type"], "ir.actions.report")
        html = self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()
        self.assertIn("Checkliste Warenannahme", html)
        self.assertIn("SN3", html)
        self.assertNotIn("GESPERRT", html)
        with self.assertRaises(UserError):
            box.grx_close()  # Unterschrift nicht bestätigt
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "to_release")
        lots = box.lot_ids
        self.assertEqual(sorted(lots.mapped("name")), ["SN1", "SN2", "SN3"])
        self.assertEqual(set(lots.mapped("plcs_batch")), {"BYE-T01"})
        self.assertTrue(all(lots.mapped("expiration_date")))
        # Paket fertig
        ticket.with_user(self.lager).grx_finish()
        self.assertEqual(ticket.picking_id.state, "done")
        quants = box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)
        self.assertEqual(quants.location_id, self.cfg.plcs_grx_quarantine_location_id)
        self.assertEqual(sum(quants.mapped("quantity")), 3)
        self.assertEqual(len(quants), 3)
        # Seriennummer kann nicht ein zweites Mal erfasst werden
        t2 = self._ticket("T-0900", lines=[{"pzn": "12345678", "qty": 1}])
        b2 = Box.browse(Box.grx_open_box(t2.id, "GX002")["id"])
        r = b2.grx_scan(code(self.gtin_a, "SN2", "BYE-T01", self.exp_ok))
        self.assertEqual(r["status"], "error")
        self.assertIn("schon im System", r["title"])
        # Box GX001 ist belegt
        with self.assertRaises(UserError):
            Box.grx_open_box(t2.id, "GX001")
        # Lager darf nicht freigeben
        with self.assertRaises(AccessError):
            box.with_user(self.lager).grx_vp_release()
        # Lager darf Ware nicht an der Freigabe vorbei aus der Quarantäne holen
        move_out = self.env["stock.picking"].with_user(self.lager).create({
            "picking_type_id": self.env.ref("stock.picking_type_internal").id,
            "location_id": self.cfg.plcs_grx_quarantine_location_id.id,
            "location_dest_id": self.env.ref("stock.stock_location_stock").id,
            "move_ids": [(0, 0, {"product_id": self.prod_a.id, "product_uom_qty": 1,
                                 "uom_id": self.prod_a.uom_id.id,
                                 "location_id": self.cfg.plcs_grx_quarantine_location_id.id,
                                 "location_dest_id": self.env.ref("stock.stock_location_stock").id})],
        })
        move_out.action_confirm()
        move_out.action_assign()
        with self.assertRaises(UserError):
            move_out.button_validate()
        # VP-Liste und Freigabe
        listing = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        self.assertIn(box.id, [b["id"] for b in listing["boxes"]])
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")
        quants = box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)
        self.assertEqual(quants.location_id, self.cfg.plcs_grx_stock_location_id)
        self.assertEqual(box.vp_user_id, self.vp1)

    def test_two_boxes_one_package(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 4}])
        b1 = self._fill_box(ticket, "GX090", self.gtin_a, ["M1", "M2"], batch="L1")
        b1.grx_to_check()
        self._close(b1)
        b2 = self._fill_box(ticket, "GX091", self.gtin_a, ["M3", "M4"], batch="L2")
        b2.grx_to_check()
        self._close(b2, ampel="rot", q_seal="nein")
        ticket.with_user(self.lager).grx_finish()
        self.assertEqual(ticket.state, "done")
        self.assertEqual(ticket.picking_id.state, "done")
        self.assertEqual(b1.location_id, self.cfg.plcs_grx_quarantine_location_id)
        self.assertEqual(b2.location_id, self.cfg.plcs_grx_blocked_location_id)

    def test_two_boxes_same_product_same_destination(self):
        """Zwei Boxen mit gleichem Artikel in die Quarantäne: keine Zusammenführung der Bewegungen."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 4}])
        b1 = self._fill_box(ticket, "GX092", self.gtin_a, ["S1", "S2"], batch="L1")
        b1.grx_to_check()
        self._close(b1)
        b2 = self._fill_box(ticket, "GX093", self.gtin_a, ["S3", "S4"], batch="L1")
        b2.grx_to_check()
        self._close(b2)
        self.assertTrue(b1.move_id.exists() and b2.move_id.exists())
        self.assertNotEqual(b1.move_id, b2.move_id)
        ticket.with_user(self.lager).grx_finish()
        self.assertEqual(ticket.picking_id.state, "done")
        self.assertEqual(b1.location_id, self.cfg.plcs_grx_quarantine_location_id)
        self.assertEqual(b2.location_id, self.cfg.plcs_grx_quarantine_location_id)
        self.assertEqual(len(b1.package_id.quant_ids.filtered(lambda q: q.quantity > 0)), 2)
        self.assertEqual(len(b2.package_id.quant_ids.filtered(lambda q: q.quantity > 0)), 2)

    def test_four_eyes(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX010", self.gtin_a, ["V1"], user=self.vp1)
        box = box.with_user(self.vp1)
        box.grx_to_check()
        self._close(box)
        ticket.with_user(self.vp1).grx_finish()
        listing = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        self.assertNotIn(box.id, [b["id"] for b in listing["boxes"]])
        self.assertEqual(listing["own_count"], 1)
        with self.assertRaises(AccessError):
            box.grx_vp_release()
        box.with_user(self.vp2).grx_vp_release()
        self.assertEqual(box.state, "released")

    def test_red_box_and_rules(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX020", self.gtin_a, ["R1", "R2"])
        box.grx_to_check()
        self._all_yes(box, q_seal="nein")
        self.assertEqual(box.ampel_suggest, "rot")
        box.grx_save_check(ampel="gelb")
        self.assertFalse(box.ampel, "milder als Vorschlag wird verworfen")
        box.grx_save_check(ampel="rot")
        box.grx_to_print()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()
        self.assertIn("GESPERRT", html)
        self.assertIn("Originalitätsverschluss nicht intakt", html)
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "blocked")
        ticket.with_user(self.lager).grx_finish()
        quants = box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)
        self.assertEqual(quants.location_id, self.cfg.plcs_grx_blocked_location_id)
        # Freigabe einer gesperrten Box nur mit Begründung
        with self.assertRaises(UserError):
            box.with_user(self.vp1).grx_vp_release()
        box.with_user(self.vp1).grx_vp_release(note="Nachprüfung OK")
        self.assertEqual(box.state, "released")

    def test_yellow_release_needs_reason(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX025", self.gtin_a, ["Y1"])
        box.grx_to_check()
        self._close(box, ampel="gelb", q_damage="nein")
        self.assertEqual(box.ampel, "gelb")
        self.assertEqual(box.state, "to_release")
        ticket.with_user(self.lager).grx_finish()
        # Freigabe einer gelben Box nur mit Begründung
        with self.assertRaises(UserError):
            box.with_user(self.vp1).grx_vp_release()
        with self.assertRaises(UserError):
            box.with_user(self.vp1).grx_vp_release(note="   ")
        box.with_user(self.vp1).grx_vp_release(note="Umkarton beschädigt, Packungen unversehrt")
        self.assertEqual(box.state, "released")
        self.assertEqual(box.vp_reason, "Umkarton beschädigt, Packungen unversehrt")
        quants = box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)
        self.assertEqual(quants.location_id, self.cfg.plcs_grx_stock_location_id)
        self.assertEqual(box.location_id, self.cfg.plcs_grx_stock_location_id)
        # Übersicht „Bestand GreenRX“ zeigt die Box im Bestand
        action = self.env["plcs.greenrx.box"].with_user(self.lager).action_grx_stock()
        found = self.env["stock.quant"].with_user(self.lager).search(action["domain"])
        self.assertIn(box.package_id, found.package_id)
        mine = found.filtered(lambda q: q.package_id == box.package_id)
        self.assertEqual(set(mine.mapped("plcs_batch")), {"BYE-T01"})
        self.assertTrue(all(mine.mapped("expiration_date")))

    def test_short_expiry_suggests_red(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        box = Box.browse(Box.grx_open_box(ticket.id, "GX030")["id"])
        r = box.grx_scan(code(self.gtin_a, "E1", "L1", self.exp_short))
        self.assertEqual(r["status"], "warning")
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "rot")

    def test_stricter_ampel_needs_note(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX040", self.gtin_a, ["N1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gelb")
        with self.assertRaises(UserError):
            box.grx_to_print()
        box.grx_save_check(note="Verpackung verschmutzt")
        box.grx_to_print()
        self.assertEqual(box.state, "print")

    def test_deviation_and_extra_product(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 3}])
        box = self._fill_box(ticket, "GX050", self.gtin_a, ["D1", "D2"])
        box.grx_to_check()
        self._close(box)
        with self.assertRaises(UserError):
            ticket.grx_finish()  # 1 offen → Begründung nötig
        ticket.grx_finish(deviation_note="Eine Packung fehlte im Paket")
        self.assertEqual(ticket.state, "done")
        self.assertTrue(ticket.deviation_note)

        # Artikel nicht laut Ticket → Warnung und Vorschlag gelb
        t2 = self._ticket("T-0950", lines=[{"pzn": "12345678", "qty": 1}])
        self.env["plcs.greenrx.box"].with_user(self.lager).grx_open_box(t2.id, "GX051")
        b2 = t2.box_ids
        r = b2.with_user(self.lager).grx_scan(code(self.gtin_b, "X1", "B1", self.exp_ok))
        self.assertEqual(r["status"], "warning")
        b2.with_user(self.lager).grx_to_check()
        self._all_yes(b2.with_user(self.lager))
        self.assertEqual(b2.ampel_suggest, "gelb")

    def test_remove_scan_and_cancel(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX060", self.gtin_a, ["C1"])
        scan = box.scan_ids.filtered("accepted")
        box.grx_remove_scan(scan.id)
        self.assertEqual(box.qty, 0)
        self.assertFalse(box.product_id)
        # nach Entnahme darf eine andere Charge in die Box
        r = box.grx_scan(code(self.gtin_a, "C2", "OTHER", self.exp_ok))
        self.assertEqual(r["status"], "ok", r)
        box.grx_cancel()
        self.assertEqual(box.state, "cancel")
        # Box ist wieder frei
        self.env["plcs.greenrx.box"].with_user(self.lager).grx_open_box(ticket.id, "GX060")

    def test_manual_box_and_invalid_codes(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        data = Box.grx_open_box(ticket.id, "70", manual=True)
        self.assertEqual(data["box"], "GX070")
        self.assertTrue(data["box_manual"])
        # gleiche Box erneut → Fortsetzen
        self.assertEqual(Box.grx_open_box(ticket.id, "GX070")["id"], data["id"])
        for bad in ("GX201", "GX000", "XY123", "70"):
            with self.assertRaises(UserError):
                Box.grx_open_box(ticket.id, bad)
        # fremder Benutzer darf die Box nicht befüllen
        other = new_test_user(self.env, login="grx_lager2", groups="plcs_greenrx.group_grx_user")
        with self.assertRaises(AccessError):
            Box.with_user(other).browse(data["id"]).grx_scan(code(self.gtin_a, "Z1", "L", self.exp_ok))
        box = Box.browse(data["id"])
        r = box.grx_scan("01" + self.gtin_a + "21NOEXP")  # unvollständig
        self.assertEqual(r["status"], "error")
        r = box.grx_scan(code(gs1.pzn_to_gtin("11111111"), "U1", "L", self.exp_ok))
        self.assertIn("unbekannt", r["title"])

    def test_heuristic_rejected_by_default(self):
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX080", self.gtin_a, [])
        raw = "01" + self.gtin_a + "17" + self.exp_ok + "10BYE-T01" + "21TSTPLCS00028"
        self.assertEqual(box.grx_scan(raw)["status"], "error")
        self.company.plcs_grx_allow_heuristic = True
        self.assertEqual(box.grx_scan(raw)["status"], "ok")

    def test_no_direct_writes(self):
        """Status und Ergebnis lassen sich nicht an den Regeln vorbei per API setzen."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX100", self.gtin_a, ["W1"])
        with self.assertRaises(AccessError):
            box.write({"state": "released"})
        with self.assertRaises(AccessError):
            box.scan_ids.write({"accepted": False})
        with self.assertRaises(AccessError):
            ticket.with_user(self.lager).write({"state": "done"})
        with self.assertRaises(AccessError):
            self.env["plcs.greenrx.box"].with_user(self.lager).create(
                {"ticket_id": ticket.id, "package_id": box.package_id.id})

    # ------------------------------------------------------------------
    # Druck und Daten (Review-Befund 2)
    # ------------------------------------------------------------------
    def _render(self, box):
        return self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()

    def test_change_after_print_needs_reprint(self):
        """Zurück nach dem Druck, Antwort und Ampel ändern: alter Ausdruck ungültig, Abschluss erst nach Neudruck."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX120", self.gtin_a, ["P1", "P2"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        self.assertNotIn("Neuausdruck", self._render(box))

        box.grx_back()
        self.assertEqual(box.state, "check")
        self.assertFalse(box.printed_at, "Ausdruck muss nach Zurück ungültig sein")
        self.assertFalse(box.signed_confirmed)
        self._all_yes(box, q_damage="nein")
        box.grx_save_check(ampel="gelb")
        box.grx_to_print()
        with self.assertRaises(UserError):
            box.grx_confirm_signed(True)  # alter Ausdruck zählt nicht
        with self.assertRaises(UserError):
            box.grx_close()

        box.grx_print()
        self.assertEqual(box.print_count, 2)
        html = self._render(box)
        self.assertIn("Neuausdruck Nr. 1", html)
        self.assertIn("Uhr", html)
        self.assertIn("GELB", html)
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual((box.state, box.ampel), ("to_release", "gelb"))

    def test_rescan_after_print_needs_reprint(self):
        """Zurück bis zum Scannen, weitere Packung: Abschluss erst nach neuem Druck mit 3 Packungen."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 3}])
        box = self._fill_box(ticket, "GX121", self.gtin_a, ["R10", "R11"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_back()
        box.grx_back()
        self.assertEqual(box.state, "scan")
        self.assertEqual(box.grx_scan(code(self.gtin_a, "R12", "BYE-T01", self.exp_ok))["status"], "ok")
        box.grx_to_check()
        box.grx_to_print()
        self.assertFalse(box.printed_at, "Ausdruck mit 2 Packungen darf nicht mehr gelten")
        with self.assertRaises(UserError):
            box.grx_confirm_signed(True)
        with self.assertRaises(UserError):
            box.grx_close()
        box.grx_print()
        self.assertIn("R12", self._render(box))
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(len(box.lot_ids), 3)

    def test_every_change_invalidates_print(self):
        """grx_scan, grx_remove_scan, grx_save_check, grx_to_print und grx_back machen den Ausdruck ungültig."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 3}])
        box = self._fill_box(ticket, "GX122", self.gtin_a, ["I1"])

        def stamp():
            box.sudo().write({"printed_at": fields.Datetime.now(), "signed_confirmed": True,
                              "printed_rev": box.content_rev})

        def assert_invalid(step):
            self.assertFalse(box.printed_at, step)
            self.assertFalse(box.signed_confirmed, step)

        stamp()
        res = box.grx_scan(code(self.gtin_a, "I2", "BYE-T01", self.exp_ok))
        assert_invalid("grx_scan")
        stamp()
        box.grx_remove_scan(res["scan_id"])
        assert_invalid("grx_remove_scan")
        box.grx_to_check()
        stamp()
        self._all_yes(box)
        assert_invalid("grx_save_check (Antworten)")
        stamp()
        box.grx_save_check(ampel="gruen")
        assert_invalid("grx_save_check (Ampel)")
        stamp()
        box.grx_to_print()
        assert_invalid("grx_to_print")
        stamp()
        box.grx_back()
        assert_invalid("grx_back")

    def test_close_rejects_change_since_last_print(self):
        """grx_close prüft selbst, ob der Ausdruck zum aktuellen Stand passt."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX123", self.gtin_a, ["K1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        # Änderung, die den Ausdruck nicht zurückgesetzt hat (z. B. ein künftiger Weg an grx_* vorbei)
        box.sudo().content_rev += 1
        with self.assertRaises(UserError):
            box.grx_close()
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "to_release")

    def test_legacy_print_invalidated_by_migration(self):
        """1.3.1: Ausdrucke offener Boxen aus der Zeit vor 1.3.0 werden beim Update ungültig."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 2}])
        legacy = self._fill_box(ticket, "GX124", self.gtin_a, ["L1"])
        legacy.grx_to_check()
        self._all_yes(legacy)
        legacy.grx_save_check(ampel="gruen")
        legacy.grx_to_print()
        # Stand wie vor 1.3.0: gedruckt und bestätigt, aber ohne Änderungsstand
        legacy.sudo().write({"printed_at": fields.Datetime.now(), "print_count": 1, "signed_confirmed": True,
                             "content_rev": 0, "printed_rev": 0})
        current = self._fill_box(ticket, "GX125", self.gtin_a, ["L2"])
        current.grx_to_check()
        self._all_yes(current)
        current.grx_save_check(ampel="gruen")
        current.grx_to_print()
        current.grx_print()
        current.grx_confirm_signed(True)

        path = os.path.join(os.path.dirname(__file__), "..", "migrations", "20.0.1.3.1", "post-migrate.py")
        spec = importlib.util.spec_from_file_location("plcs_greenrx_migrate_1_3_1", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        script.migrate(self.env.cr, "20.0.1.3.0")
        self.env.invalidate_all()

        self.assertFalse(legacy.printed_at, "alter Ausdruck ungültig")
        self.assertFalse(legacy.signed_confirmed)
        self.assertTrue(current.printed_at, "Ausdruck aus 1.3.0 bleibt gültig")
        self.assertTrue(current.signed_confirmed)
        with self.assertRaises(UserError):
            legacy.grx_close()
        legacy.grx_print()
        self.assertIn("Neuausdruck Nr. 1", self._render(legacy))
        legacy.grx_confirm_signed(True)
        legacy.grx_close()
        current.grx_close()
        self.assertEqual((legacy.state, current.state), ("to_release", "to_release"))

    def test_finish_names_open_boxes(self):
        """Paket fertig mit offener Box: Meldung nennt Box, Schritt und Person."""
        ticket = self._ticket(lines=[{"pzn": "12345678", "qty": 2}])
        done = self._fill_box(ticket, "GX126", self.gtin_a, ["O1"])
        done.grx_to_check()
        self._close(done)
        other = new_test_user(self.env, login="grx_lager3", name="Lager Drei", groups="plcs_greenrx.group_grx_user")
        self._fill_box(ticket, "GX127", self.gtin_a, ["O2"], user=other).grx_to_check()
        with self.assertRaises(UserError) as err:
            ticket.with_user(self.lager).grx_finish(deviation_note="egal")
        message = str(err.exception)
        self.assertIn("GX127", message)
        self.assertIn("Vorprüfung", message)
        self.assertIn("Lager Drei", message)
        self.assertNotIn("GX126", message)
        self.assertEqual(ticket.state, "in_progress")

    # ------------------------------------------------------------------
    # Suche (Review-Befund 3)
    # ------------------------------------------------------------------
    def test_search_beyond_1000_open(self):
        """Die Suche findet auch Vorgänge hinter den ersten 1.000 offenen; Rangfolge bleibt gleich."""
        T = self.env["plcs.greenrx.ticket"].with_context(tracking_disable=True, mail_create_nolog=True)
        today = fields.Date.today()
        T.create([{"name": "BULK-%04d" % i, "date_ticket": today} for i in range(1100)])
        target = self._ticket("ALT-4711", lines=[{"pzn": "87654321", "qty": 1, "ticket_number": "9004711"}])
        similar = self._ticket("ALT-47119", lines=[{"pzn": "12345678", "qty": 1}])
        # ganz hinten in der Standard-Reihenfolge (Anmeldedatum absteigend)
        (target | similar).write({"date_ticket": today - relativedelta(years=3)})
        self.assertGreater(T.search_count([("state", "in", ("open", "in_progress"))]), 1000)

        S = self.env["plcs.greenrx.ticket"].with_user(self.lager)
        res = S.grx_search("ALT-4711")
        self.assertEqual([r["id"] for r in res], [target.id, similar.id], "exakter Treffer zuerst")
        self.assertEqual(res[0]["match"], "Treffer: Referenz")
        self.assertEqual(set(res[0]), set(target._grx_ui_data(with_boxes=False)) | {"match"})
        self.assertEqual(S.grx_search("alt 4711")[0]["id"], target.id)
        self.assertEqual(S.grx_search("9004711")[0]["match"], "Treffer: Ticket")
        self.assertEqual(S.grx_search("S-ALT-4711")[0]["match"], "Treffer: Reklamation")
        self.assertEqual(S.grx_search("gs alt 4711")[0]["match"], "Treffer: Gutschrift")
        self.assertEqual(S.grx_search("Artikel B")[0]["id"], target.id)
        self.assertEqual(S.grx_search("Artikel B")[0]["match"], "Treffer: Medikament")
        self.assertEqual(len(S.grx_search("BULK")), 20, "Begrenzung auf 20 Treffer bleibt")
        self.assertEqual(S.grx_search("BULK-1099")[0]["match"], "Treffer: Referenz")
        self.assertFalse(S.grx_search("gibt es nicht 0815"))


    # ------------------------------------------------------------------
    # Retourenschein-QR
    # ------------------------------------------------------------------
    SLIP = ("REF:V202601693\nREKL:S23124\nTICKET|PZN|CHARGE|ANZAHL\n"
            "1001982|12345678|214124|2\n1001983|87654321|33t32|1")

    def test_slip_creates_and_finds_vorgang(self):
        T = self.env["plcs.greenrx.ticket"].with_user(self.lager)
        data = T.grx_from_slip(self.SLIP)
        self.assertTrue(data["created_from_slip"])
        ticket = self.env["plcs.greenrx.ticket"].browse(data["id"])
        self.assertEqual(ticket.name, "V202601693")
        self.assertEqual(ticket.complaint_number, "S23124")
        self.assertEqual(ticket.source, "slip")
        self.assertEqual(ticket.line_ids.mapped("ticket_number"), ["1001982", "1001983"])
        self.assertEqual(ticket.line_ids.product_id, self.prod_a | self.prod_b)
        self.assertEqual(ticket.qty_expected_total, 3)
        # zweiter Scan findet denselben Vorgang, auch wenn der Scanner an | und Zeilen trennt
        again = T.grx_from_slip(self.SLIP.replace("|", "\n"))
        self.assertEqual(again["id"], ticket.id)
        self.assertFalse(again["created_from_slip"])
        # Suche nach Ticketnummer
        self.assertEqual(T.grx_search("1001983")[0]["match"], "Treffer: Ticket")

    def test_slip_box_matches_ticket_and_batch(self):
        T = self.env["plcs.greenrx.ticket"].with_user(self.lager)
        ticket = T.browse(T.grx_from_slip(self.SLIP)["id"])
        box = self._fill_box(ticket, "GX110", self.gtin_a, ["Q1", "Q2"], batch="214124")
        self.assertEqual(box.ticket_number, "1001982")
        self.assertEqual(box.slip_match, "ok")
        box.grx_to_check()
        self._close(box)
        # andere Charge als auf dem Schein → Gelb-Vorschlag
        box2 = self._fill_box(ticket, "GX111", self.gtin_b, ["Q3"], batch="FALSCH")
        self.assertEqual(box2.slip_match, "batch_differs")
        self.assertEqual(box2.ticket_number, "1001983")
        box2.grx_to_check()
        self._all_yes(box2)
        self.assertEqual(box2.ampel_suggest, "gelb")
        self._close(box2, ampel="gelb")
        rows = {r["ticket"]: r for r in ticket._grx_soll_ist()}
        self.assertEqual(rows["1001982"]["status"], "ok")
        self.assertEqual(rows["1001983"]["status"], "ok")
        ticket.with_user(self.lager).grx_finish()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box2.ids)[0].decode()
        self.assertIn("1001983", html)
        self.assertIn("S23124", html)
        # erfasster Vorgang darf nicht noch einmal angenommen werden
        with self.assertRaises(UserError):
            T.grx_from_slip(self.SLIP)

    def test_slip_difference_to_erp(self):
        # Vorgang kam schon aus dem ERP, Schein meldet andere Anzahl
        self.env["plcs.greenrx.ticket"].api_upsert([{"name": "V202601693", "lines": [
            {"ticket_number": "1001982", "pzn": "12345678", "batch": "214124", "qty": 5},
            {"ticket_number": "1001983", "pzn": "87654321", "batch": "33t32", "qty": 1}]}])
        data = self.env["plcs.greenrx.ticket"].with_user(self.lager).grx_from_slip(self.SLIP)
        self.assertIn("Anzahl Schein 2, Odoo 5", data["slip_note"])

    def test_slip_unknown_when_creation_off(self):
        self.company.plcs_grx_create_from_slip = False
        with self.assertRaises(UserError):
            self.env["plcs.greenrx.ticket"].with_user(self.lager).grx_from_slip(self.SLIP)
