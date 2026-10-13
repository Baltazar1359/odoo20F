from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..tools import gs1


class FakeProxy:
    """Simuliert das GreenRX-ERP (Helpdesk-Tickets mit Studio-Feldern)."""

    def __init__(self, endpoint):
        self.endpoint = endpoint

    def authenticate(self, db, login, key, ctx):
        return 7

    def execute_kw(self, db, uid, key, model, method, args, kw):
        if model == "helpdesk.ticket" and method == "search_read":
            return [{
                "id": 501, "ticket_ref": "4711", "x_studio_credit_note": "GS-1", "x_ref": "V900",
                "partner_id": [9, "Stern-Apotheke"], "x_studio_product": [33, "BYETTA 250"],
                "x_studio_product_number": 4, "x_studio_batch_number": "L-77",
            }, {
                "id": 502, "ticket_ref": "4712", "x_studio_credit_note": "GS-2", "x_ref": "V900",
                "partner_id": [9, "Stern-Apotheke"], "x_studio_product": [33, "BYETTA 250"],
                "x_studio_product_number": 1, "x_studio_batch_number": "L-78",
            }]
        if model == "helpdesk.ticket" and method == "search_count":
            return 1
        if model == "product.product" and method == "read":
            return [{"id": 33, "barcode": gs1.pzn_to_gtin("12345678"), "default_code": False,
                     "display_name": "BYETTA 250"}]
        if model == "res.partner" and method == "read":
            return [{"id": 9, "ref": "K-10512"}]
        raise AssertionError((model, method))


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestConnector(TransactionCase):

    def test_sync(self):
        product = self.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "barcode": gs1.pzn_to_gtin("12345678")})
        connector = self.env["plcs.greenrx.connector"].create({
            "name": "Test", "url": "https://greenrx.test", "db": "x", "login": "api", "api_key": "secret",
            "f_reference": "x_ref",
        })
        with patch.object(type(connector), "_proxy", lambda self, endpoint: FakeProxy(endpoint)):
            connector.action_test()
            connector.action_sync()
            connector.action_sync()  # zweiter Abruf: Update, kein Duplikat
        ticket = self.env["plcs.greenrx.ticket"].search([("external_id", "=", "V900")])
        self.assertEqual(len(ticket), 1)
        self.assertEqual(ticket.name, "V900")
        self.assertEqual(ticket.customer_number, "K-10512")
        self.assertEqual(ticket.line_ids.product_id, product)
        self.assertEqual(ticket.line_ids.mapped("ticket_number"), ["4711", "4712"])
        self.assertEqual(ticket.line_ids.mapped("batch_expected"), ["L-77", "L-78"])
        self.assertEqual(ticket.line_ids.mapped("credit_note"), ["GS-1", "GS-2"])
        self.assertEqual(ticket.qty_expected_total, 5)
        self.assertEqual(ticket.source, "api")
        self.assertIn("1 aktualisiert", connector.last_result)
