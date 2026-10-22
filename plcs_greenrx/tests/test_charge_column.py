"""1.10.1: Spalte „Charge“ für Ware außerhalb GreenRX (z. B. Blistercenter): Los = Charge.

Anlass: Mehrdad 22.10.2026 – im Blistercenter-Eingang stand die Charge unter „Los-/Seriennummer“,
die Spalte „Charge“ (aus GreenRX) war leer und wirkte vertauscht.
"""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestChargeColumn(TransactionCase):

    def test_non_greenrx_lot_is_its_own_batch(self):
        product = self.env["product.product"].create({
            "name": "Charge-Spalte Test", "is_storable": True, "tracking": "lot"})
        lot = self.env["stock.lot"].create({"name": "CH-BC-1", "product_id": product.id})
        stock = self.env.ref("stock.warehouse0").lot_stock_id
        self.env["stock.quant"]._update_available_quantity(product, stock, 3, lot_id=lot)
        quant = self.env["stock.quant"].search([("lot_id", "=", lot.id), ("location_id", "=", stock.id)])
        self.assertEqual(quant.plcs_batch, "CH-BC-1", "Bestand: Spalte Charge = Los")
        self.assertEqual(self.env["stock.quant"].search_count([("plcs_batch", "=", "CH-BC-1")]), 1,
                         "Gespeichert und suchbar")
        line = self.env["stock.move.line"].new({"product_id": product.id, "lot_name": "CH-NEU"})
        self.assertEqual(line.plcs_batch, "CH-NEU", "Neue Charge im Eingang: Spalte Charge = eingegebene Charge")
        line = self.env["stock.move.line"].new({"product_id": product.id, "lot_id": lot.id})
        self.assertEqual(line.plcs_batch, "CH-BC-1")

    def test_greenrx_serial_keeps_separate_batch(self):
        product = self.env["product.product"].create({
            "name": "GreenRX-Spalte Test", "is_storable": True, "tracking": "lot"})
        lot = self.env["stock.lot"].sudo().create({
            "name": "SN0001", "product_id": product.id, "plcs_is_grx_serial": True, "plcs_batch": "BATCH-9"})
        line = self.env["stock.move.line"].new({"product_id": product.id, "lot_id": lot.id})
        self.assertEqual(line.plcs_batch, "BATCH-9", "GreenRX: Los = Seriennummer, Charge separat")
