import base64

from odoo.tests import tagged

from .ativ_fake import PNG_B64
from .test_ativ_sync import AtivCase


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestAtivProducts(AtivCase):
    """Artikelstamm aus ATIV (1.10.0): alle Artikel, nur leere Felder füllen, Bild, Webhook."""

    def setUp(self):
        super().setUp()
        self.connector.ativ_product_sync = True

    def sync_products(self, mode="incremental"):
        run, _msg = self.connector._ativ_plan_products(mode, "manual")
        self.connector._ativ_work()
        run.invalidate_recordset()
        return run

    def product(self, pzn):
        return self.env["product.product"].with_context(active_test=False).search([("plcs_pzn", "=", pzn)])

    def test_p01_initial_load(self):
        run = self.sync_products()
        self.assertEqual(run.mode, "products_full", "ohne Fortschrittsstand werden alle Artikel geprüft")
        self.assertEqual(run.state, "done", run.message)
        self.assertEqual(run.count_planned, 4, "Artikel ohne interne Referenz fallen über den Filter heraus")
        self.assertEqual(run.count_products_created, 1)
        self.assertEqual(run.count_products_updated, 2, "interne Referenz bei BYETTA und BRILIQUE ergänzt")
        self.assertEqual(run.count_products_skipped, 1, "PZN 00000019 hat eine falsche Prüfziffer")
        self.assertEqual(run.count_products_differ, 2)
        self.assertEqual(run.count_images, 1)
        self.assertEqual(self.connector.ativ_product_cursor, run.cursor_candidate)
        self.assertFalse(self.connector.ativ_cursor, "Gutschriften-Stand bleibt unberührt")

        forxiga = self.product("04114918")
        self.assertEqual(forxiga.name, "FORXIGA 10 mg")
        self.assertEqual(forxiga.barcode, "04150041149186")
        self.assertEqual(forxiga.default_code, "04114918")
        self.assertEqual(forxiga.plcs_storage_temp, "ambient")
        self.assertEqual(forxiga.tracking, "lot")
        self.assertTrue(forxiga.image_1920, "Produktbild aus ATIV übernommen")
        self.assertEqual(forxiga.plcs_ativ_key, "ativ-test:product.product:35")
        self.assertFalse(forxiga.plcs_ativ_diff)

        # Vorhandene Artikel: nichts überschrieben, nur leere Felder gefüllt
        self.prod_byetta.invalidate_recordset()
        self.assertEqual(self.prod_byetta.name, "BYETTA")
        self.assertEqual(self.prod_byetta.plcs_storage_temp, "ambient")
        self.assertEqual(self.prod_byetta.default_code, "12345678")
        self.assertEqual(self.prod_byetta.plcs_ativ_name, "BYETTA 250")
        self.assertEqual(self.prod_byetta.plcs_ativ_storage, "2–8 °C")
        self.assertIn("Lagertemperatur", self.prod_byetta.plcs_ativ_diff)
        self.assertIn("Bezeichnung", self.prod_byetta.plcs_ativ_diff)
        self.assertIn("Weicht von ATIV ab", self.prod_byetta.product_tmpl_id.message_ids[:1].body)

        self.assertFalse(self.product("00000019"), "ungültige PZN: kein Artikel")
        skipped = self.env["plcs.ativ.queue"].search([("res_id", "=", 37), ("kind", "=", "product")])
        self.assertEqual(skipped.state, "done")
        self.assertIn("Keine gültige PZN", skipped.error)
        self.assertEqual(self.connector.ativ_product_count, 3)

    def test_p02_rerun_is_idempotent(self):
        self.sync_products()
        messages = len(self.prod_byetta.product_tmpl_id.message_ids)
        count = self.env["product.product"].search_count([])
        run = self.sync_products("full")
        self.assertEqual(run.state, "done", run.message)
        self.assertEqual(run.count_products_created, 0)
        self.assertEqual(run.count_unchanged, 3)
        self.assertEqual(self.env["product.product"].search_count([]), count, "keine doppelten Artikel")
        self.assertEqual(len(self.prod_byetta.product_tmpl_id.message_ids), messages,
                         "gleiche Abweichung wird nicht erneut notiert")

    def test_p03_no_overwrite_on_change(self):
        self.sync_products()
        forxiga = self.product("04114918")
        forxiga.name = "Forxiga (PLCS)"
        self.later()
        self.fake.touch("product.product", 35, name="FORXIGA 10 mg Filmtabletten",
                        x_studio_storage_conditions=[2, "Kühlware 2 - 8 °C"])
        run = self.sync_products()
        self.assertEqual(run.mode, "products")
        self.assertEqual(run.count_planned, 1, "nur der geänderte Artikel")
        forxiga.invalidate_recordset()
        self.assertEqual(forxiga.name, "Forxiga (PLCS)", "PLCS-Bezeichnung bleibt")
        self.assertEqual(forxiga.plcs_storage_temp, "ambient", "Lagertemperatur bleibt")
        self.assertEqual(forxiga.plcs_ativ_name, "FORXIGA 10 mg Filmtabletten")
        self.assertIn("Lagertemperatur", forxiga.plcs_ativ_diff)
        self.assertEqual(run.count_products_differ, 1)

    def test_p04_webhook_template_change(self):
        self.sync_products()
        self.assertFalse(self.prod_byetta.image_1920)
        self.later()
        self.fake.touch("product.product", 33, image_1920=PNG_B64)
        self.fake.touch("product.template", 33)
        self.assertTrue(self.connector._ativ_webhook_receive({"_model": "product.template", "_id": 33}))
        self.connector._ativ_work()
        self.prod_byetta.invalidate_recordset()
        self.assertTrue(self.prod_byetta.image_1920, "leeres Bild wird per Webhook gefüllt")
        item = self.env["plcs.ativ.queue"].search([("kind", "=", "product"), ("res_id", "=", 33)], order="id desc",
                                                   limit=1)
        self.assertEqual(item.state, "done")

        self.connector.ativ_product_sync = False
        self.assertFalse(self.connector._ativ_webhook_receive({"_model": "product.template", "_id": 33}),
                         "ohne Artikelstamm werden Artikel-Webhooks ignoriert")

    def test_p05_invalid_image_does_not_block(self):
        self.fake.db["product.product"][35]["image_1920"] = base64.b64encode(b"kein Bild").decode()
        run = self.sync_products()
        self.assertEqual(run.state, "done", run.message)
        forxiga = self.product("04114918")
        self.assertTrue(forxiga, "Artikel trotzdem angelegt")
        self.assertFalse(forxiga.image_1920)
        item = self.env["plcs.ativ.queue"].search([("kind", "=", "product"), ("res_id", "=", 35)])
        self.assertIn("Produktbild", item.error)

    def test_p06_independent_from_credit_notes(self):
        product_run, _msg = self.connector._ativ_plan_products("full", "manual")
        run = self.sync()
        self.assertEqual(run.state, "done", "laufender Artikel-Lauf blockiert die Gutschriften nicht")
        product_run.invalidate_recordset()
        self.assertEqual(product_run.state, "done")
        self.assertEqual(self.connector.last_success_run_id, run)
        self.assertEqual(self.connector.ativ_product_run_id, product_run)

    def test_p07_cron_and_disable(self):
        self.connector._cron_ativ_schedule()
        runs = self.env["plcs.ativ.run"].search([("connector_id", "=", self.connector.id)])
        self.assertEqual(set(runs.mapped("mode")), {"full", "products_full"})
        self.connector.with_user(self.manager).action_ativ_disable()
        self.assertFalse(self.connector.ativ_product_sync)

    def test_p08_barcode_conflict(self):
        other = self.env["product.product"].create({"name": "Fremd", "barcode": "04150041149186"})
        run = self.sync_products()
        self.assertEqual(run.count_products_created, 0)
        other.invalidate_recordset()
        self.assertEqual(other.plcs_ativ_key, "ativ-test:product.product:35",
                         "über den Barcode gefunden und zugeordnet")
        self.assertEqual(other.plcs_pzn, "04114918", "leere PZN ergänzt")
        self.assertEqual(other.name, "Fremd", "Bezeichnung bleibt")
        self.assertIn("Bezeichnung", other.plcs_ativ_diff)
