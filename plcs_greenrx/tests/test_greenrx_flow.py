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
            "external_id": name, "name": name, "reference": "REF-" + name, "credit_note": "GS-" + name,
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
        self.assertEqual(T.grx_search("REF-T-0826")[0]["match"], "Treffer: Referenz")
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
