from odoo.tests import HttpCase, new_test_user, tagged

from ..tools import gs1

PZN = "04711003"
GS = "\x1d"


def pack(serial, batch="QLOT"):
    return "01" + gs1.pzn_to_gtin(PZN) + "21" + serial + GS + "10" + batch + GS + "17301231"


class GreenrxUiCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company._plcs_grx_setup()
        cls.admin = cls.env.ref("base.user_admin")
        cls.admin.group_ids |= cls.env.ref("plcs_greenrx.group_grx_user")
        cls.product = cls.env["product.product"].create({
            "name": "Queue-Testartikel", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": gs1.pzn_to_gtin(PZN), "plcs_pzn": PZN,
        })

    def _ticket(self, name, qty):
        res = self.env["plcs.greenrx.ticket"].api_upsert([{
            "name": name, "partner_name": "Queue-Apotheke",
            "lines": [{"ticket_number": "95" + name[-4:], "pzn": PZN, "batch": "QLOT", "qty": qty}],
        }])
        self.assertFalse(res["errors"], res)
        return self.env["plcs.greenrx.ticket"].search([("name", "=", name)])

    def _box(self, ticket, box_code, serials, user, close=True):
        """Box befüllen; mit close=True bis zum Abschluss, sonst bis zum Schritt Drucken."""
        Box = self.env["plcs.greenrx.box"].with_user(user)
        box = Box.browse(Box.grx_open_box(ticket.id, box_code)["id"])
        for sn in serials:
            self.assertIn(box.grx_scan(pack(sn))["status"], ("ok", "warning"))
        box.grx_to_check()
        box.grx_save_check(answers={f: "ja" for f in ("q_damage", "q_temp_controlled", "q_temp_log",
                                                       "q_manufacturer", "q_storage_proof", "q_seal")})
        box.grx_save_check(ampel=box.ampel_suggest, note="Test")
        box.grx_to_print()
        box.grx_print()
        if close:
            box.grx_confirm_signed(True)
            box.grx_close()
        return box


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxScanQueue(GreenrxUiCase):
    """Scan-Oberfläche im Browser: schnelle Scans gehen nicht verloren (Review-Befund 1)."""

    def test_five_fast_scans_all_arrive(self):
        # Die Tour scannt diese GTIN fest codiert
        self.assertEqual(gs1.pzn_to_gtin(PZN), "04150047110036")
        res = self.env["plcs.greenrx.ticket"].api_upsert([{
            "name": "V-QUEUE-1", "partner_name": "Queue-Apotheke",
            "lines": [{"ticket_number": "9500001", "pzn": PZN, "batch": "QLOT", "qty": 5}],
        }])
        self.assertFalse(res["errors"], res)
        ticket = self.env["plcs.greenrx.ticket"].search([("name", "=", "V-QUEUE-1")])
        Box = self.env["plcs.greenrx.box"].with_user(self.admin)
        box = Box.browse(Box.grx_open_box(ticket.id, "GX150")["id"])

        self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_scan_queue", login="admin")

        box.invalidate_recordset()
        self.assertEqual(box.qty, 5)
        self.assertEqual(sorted(box.scan_ids.filtered("accepted").mapped("serial")),
                         ["QUEUE1", "QUEUE2", "QUEUE3", "QUEUE4", "QUEUE5"])
        self.assertEqual(len(box.scan_ids), 5, "jeder Scan genau einmal verarbeitet")

    def test_scans_before_next_step_all_arrive(self):
        """Code-Review R03: Scans in der Warteschlange + sofort F9 → alle Scans vor dem Schrittwechsel."""
        ticket = self._ticket("V-NEXT-1", qty=5)
        Box = self.env["plcs.greenrx.box"].with_user(self.admin)
        box = Box.browse(Box.grx_open_box(ticket.id, "GX151")["id"])

        self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_scan_then_next", login="admin")

        box.invalidate_recordset()
        self.assertEqual(box.state, "check")
        self.assertEqual(sorted(box.scan_ids.filtered("accepted").mapped("serial")),
                         ["NEXT1", "NEXT2", "NEXT3", "NEXT4", "NEXT5"])


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxPackageFinish(GreenrxUiCase):
    """„Paket fertig“ in Schritt 2: offene Boxen werden genannt, die Begründung ist sichtbar (1.3.1)."""

    def test_finish_reports_open_box_of_other_user(self):
        lager = new_test_user(self.env, login="grx_lager_fertig", name="Lager Fertig",
                              groups="plcs_greenrx.group_grx_user")
        ticket = self._ticket("V-FIN-OPEN", qty=3)
        self._box(ticket, "GX160", ["F1"], self.admin)
        self._box(ticket, "GX161", ["F2"], lager, close=False)  # bleibt im Schritt Drucken

        self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_finish_open_box", login="admin")

        ticket.invalidate_recordset()
        self.assertEqual(ticket.state, "in_progress")

    def test_finish_with_deviation_from_step_2(self):
        ticket = self._ticket("V-FIN-DEV", qty=1)
        self._box(ticket, "GX162", ["D1", "D2"], self.admin)

        self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_finish_deviation", login="admin")

        ticket.invalidate_recordset()
        self.assertEqual(ticket.state, "done")
        self.assertEqual(ticket.deviation_note, "Eine Packung mehr im Paket als angemeldet")
        self.assertEqual(ticket.picking_id.state, "done")


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxManualPackUi(GreenrxUiCase):
    """Handeingabe einer Packung in der Oberfläche, Scan in ein Eingabefeld, Sicherheitsabfrage (1.5.0)."""

    def test_manual_pack_in_browser(self):
        product = self.env["product.product"].create({
            "name": "Handeingabe-Testartikel", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": gs1.pzn_to_gtin("12345678"), "plcs_pzn": "12345678",
        })
        res = self.env["plcs.greenrx.ticket"].api_upsert([{
            "name": "V-HAND-1", "partner_name": "Hand-Apotheke",
            "lines": [{"ticket_number": "9600001", "pzn": "12345678", "batch": "MLOT", "qty": 2}],
        }])
        self.assertFalse(res["errors"], res)
        ticket = self.env["plcs.greenrx.ticket"].search([("name", "=", "V-HAND-1")])
        Box = self.env["plcs.greenrx.box"].with_user(self.admin)
        box = Box.browse(Box.grx_open_box(ticket.id, "GX160")["id"])

        self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_manual_pack", login="admin")

        box.invalidate_recordset()
        self.assertEqual(box.product_id, product)
        self.assertEqual(box.scan_ids.filtered("accepted").mapped("serial"), ["SCANIN1"])
        man = box.scan_ids.filtered(lambda s: s.serial == "MAN1")
        self.assertEqual(man.mapped("manual"), [True])
        self.assertEqual(man.result, "removed")
        self.assertEqual(len(man), 1, "PZN mit falscher Prüfziffer wird nicht erfasst")
