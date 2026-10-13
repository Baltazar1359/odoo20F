from odoo.tests import HttpCase, tagged

from ..tools import gs1

PZN = "04711003"


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGreenrxScanQueue(HttpCase):
    """Scan-Oberfläche im Browser: schnelle Scans gehen nicht verloren (Review-Befund 1)."""

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
