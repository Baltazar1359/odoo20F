from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..models.plcs_ativ_sync import CONTRACT_VERSION, PlcsGreenrxConnector
from ..tools.ativ_client import READ_METHODS, AtivClient, AtivError
from .ativ_fake import FakeAtiv, FakeClient


class AtivCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.manager = new_test_user(cls.env, login="ativ_mgr", groups="stock.group_stock_manager,plcs_greenrx.group_grx_vp")
        cls.lager = new_test_user(cls.env, login="ativ_lager", groups="plcs_greenrx.group_grx_user")
        cls.prod_byetta = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "plcs_pzn": "12345678"})
        cls.prod_brilique = cls.env["product.product"].create({
            "name": "BRILIQUE", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "plcs_pzn": "07560038"})
        cls.connector = cls.env["plcs.greenrx.connector"].create({
            "name": "ATIV Test", "mode": "ativ", "url": "https://ativ.example.test", "db": "ativ-test",
            "login": "green-rx@plcs.example", "api_key": "geheim", "sync_enabled": True,
        })

    def setUp(self):
        super().setUp()
        self.fake = FakeAtiv()
        patcher = patch.object(PlcsGreenrxConnector, "_ativ_client", lambda connector: FakeClient(self.fake))
        patcher.start()
        self.addCleanup(patcher.stop)

    # helpers ------------------------------------------------------------
    def sync(self, mode="incremental"):
        run, _msg = self.connector._ativ_plan(mode, "manual")
        self.connector._ativ_work()
        run.invalidate_recordset()
        return run

    def later(self, hours=1):
        self.fake.clock = fields.Datetime.to_string(fields.Datetime.now() + timedelta(hours=hours))

    def grx(self, name):
        return self.env["plcs.greenrx.ticket"].search([("name", "=", name)])

    def cn(self, name):
        return self.env["plcs.ativ.credit.note"].search([("name", "=", name)])


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestAtivSync(AtivCase):

    def test_01_initial_load(self):
        run = self.sync()
        self.assertEqual(run.mode, "full", "ohne Fortschrittsstand wird alles übernommen")
        self.assertEqual(run.state, "done", run.message)
        self.assertEqual(run.count_planned, 6)
        self.assertEqual(run.count_cn_created, 6)
        self.assertEqual(run.count_created, 5, "5 Vorgänge, V901 hat zwei Gutschriften")
        self.assertEqual(self.connector.ativ_cursor, run.cursor_candidate)
        self.assertEqual(self.connector.last_success_run_id, run)

        cn = self.cn("20260001")
        self.assertEqual(cn.contract_version, CONTRACT_VERSION)
        self.assertEqual(cn.source_key, "ativ-test:purchase.order:201")
        self.assertEqual(cn.customer_number, "70105")
        self.assertEqual(cn.partner_name, "Stern-Apotheke")
        self.assertEqual(cn.partner_city, "Köln")
        self.assertEqual(cn.vorgang_name, "V900")
        self.assertEqual(cn.complaint_number, "R-1")
        self.assertEqual(set(cn.ativ_ticket_ids.mapped("ticket_number")), {"4711", "4712"})
        self.assertEqual(cn.line_ids.mapped("qty"), [4.0, 1.0])
        self.assertEqual(cn.line_ids.mapped("taxes"), ["19 % Vorsteuer", "19 % Vorsteuer"])
        self.assertEqual(cn.status_category, "expected")
        self.assertTrue(cn.pickup_date)

        ticket = cn.ativ_ticket_ids.filtered(lambda t: t.ticket_number == "4711")
        self.assertEqual(ticket.stage_name, "Genehmigt")
        self.assertEqual(ticket.stage_key, "Approved")
        self.assertEqual(ticket.batch, "L-77")
        self.assertEqual(ticket.gdp_confirmation, "Ja")
        self.assertEqual(len(ticket.message_ids), 2)
        tracked = ticket.message_ids.filtered(lambda m: m.tracking)
        self.assertIn("Neu → Genehmigt", tracked.tracking)
        self.assertEqual(tracked.author, "Carolina Strack", "Autor bleibt als Herkunftsangabe erhalten")
        self.assertFalse(self.env["res.users"].search([("name", "=", "Carolina Strack")]),
                         "keine Benutzerkonten für ATIV-Autoren")

        grx = self.grx("V900")
        self.assertEqual(grx.source, "api")
        self.assertEqual(grx.external_id, "ativ-test:x_vorgang:100")
        self.assertEqual(grx.state, "open")
        self.assertEqual(grx.ativ_status, "expected")
        self.assertEqual(grx.customer_number, "70105")
        self.assertEqual(grx.credit_note, "20260001")
        self.assertEqual(grx.qty_expected_total, 5)
        self.assertEqual(set(grx.line_ids.mapped("ticket_number")), {"4711", "4712"})
        self.assertEqual(grx.line_ids.product_id, self.prod_byetta | self.prod_brilique, "PZN aufgelöst")
        self.assertEqual(set(grx.line_ids.mapped("batch_expected")), {"L-77", "L-78"})

        v901 = self.grx("V901")
        self.assertEqual(len(v901.ativ_credit_note_ids), 2)
        self.assertEqual(v901.credit_note, "20260002, 20260003")
        self.assertEqual(v901.qty_expected_total, 5)
        # ältere Gutschrift: Ticket nur am Kopf
        self.assertEqual(self.grx("V904").line_ids.ticket_number, "4717")
        # Status: Wareneingang bei ATIV abgeschlossen bzw. abgelehnt → nicht als offene Lieferung
        self.assertEqual(self.grx("V902").state, "closed")
        self.assertEqual(self.grx("V902").ativ_status, "closed")
        self.assertEqual(self.grx("V903").state, "cancel")

    def test_02_documents(self):
        self.sync()
        grx = self.grx("V900")
        docs = grx.ativ_document_ids
        self.assertEqual(set(docs.mapped("name")), {"Lieferschein 4711.pdf", "Foto1.jpeg", "Gutschrift_20260001.pdf"})
        self.assertEqual(set(docs.mapped("state")), {"copied"}, "erwarteter Vorgang: Dateien kopiert")
        att = docs.filtered(lambda d: d.name == "Foto1.jpeg").attachment_id
        self.assertEqual(att.res_model, "plcs.greenrx.ticket")
        self.assertEqual(att.res_id, grx.id)
        self.assertEqual(bytes(att.raw), b"\xff\xd8\xff JPEG")
        # Berechtigte PLCS-Mitarbeiter sehen die Datei
        self.assertEqual(bytes(att.with_user(self.lager).raw), b"\xff\xd8\xff JPEG")
        self.assertEqual(grx.with_user(self.lager).ativ_document_count, 3)
        # Nicht erwarteter Vorgang: nur Angaben, Abruf bei Bedarf
        old = self.grx("V902").ativ_document_ids
        self.assertEqual(old.state, "meta")
        old.with_user(self.lager).action_fetch()
        self.assertEqual(old.state, "copied")
        self.assertEqual(bytes(old.attachment_id.raw), b"%PDF alt")

    def test_03_search(self):
        self.sync()
        T = self.env["plcs.greenrx.ticket"].with_user(self.lager)
        v900 = self.grx("V900")
        for query, label in (("70105", "Kundennr."), ("stern-apotheke", "Apotheke"), ("V900", "Referenz"),
                             ("4711", "Ticket"), ("20260001", "Gutschrift"), ("R-1", "Reklamation")):
            res = T.grx_search(query)
            self.assertTrue(res, query)
            self.assertEqual(res[0]["id"], v900.id, query)
            self.assertTrue(res[0]["startable"], query)
            self.assertIn(label, res[0]["match"], query)
        # zweite Gutschrift desselben Vorgangs
        self.assertEqual(T.grx_search("20260003")[0]["id"], self.grx("V901").id)
        # Mehrere Treffer: genug Angaben zur Auswahl, nicht erwartete am Ende und nicht startbar
        res = T.grx_search("stern")
        names = [r["name"] for r in res]
        self.assertEqual(names[0], "V900")
        self.assertIn("V902", names)
        v902 = next(r for r in res if r["name"] == "V902")
        self.assertFalse(v902["startable"])
        self.assertEqual(v902["state_label"], "Nicht erwartet")
        self.assertIn("Wareneingang bei ATIV abgeschlossen", v902["ativ_reason"])
        self.assertTrue(res[0]["city"] and res[0]["date"] and res[0]["credit_note"])
        self.assertIn("Genehmigt", res[0]["ativ_stages"])
        # nicht erwarteter Vorgang lässt sich nicht starten
        with self.assertRaises(UserError):
            self.grx("V902").with_user(self.lager).grx_start()

    def test_04_idempotent(self):
        self.sync()
        counts = {m: self.env[m].search_count([]) for m in (
            "plcs.ativ.credit.note", "plcs.ativ.credit.note.line", "plcs.ativ.ticket", "plcs.ativ.message",
            "plcs.ativ.document", "plcs.greenrx.ticket", "plcs.greenrx.ticket.line", "ir.attachment")}
        run = self.sync("full")
        self.assertEqual(run.state, "done")
        self.assertEqual(run.count_unchanged, 6)
        self.assertEqual(run.count_cn_created + run.count_created, 0)
        for model, count in counts.items():
            self.assertEqual(self.env[model].search_count([]), count, model)

    def test_05_incremental_changes(self):
        self.sync()
        self.later()
        # Nur Position geändert (Gutschrift selbst unverändert)
        self.fake.touch("purchase.order.line", 301, product_qty=7)
        # Nur Ticket geändert
        self.fake.touch("helpdesk.ticket", 503, x_studio_batch_number="FPPV-2", name="FPPV-2")
        # Nur neuer Anhang / neue Nachricht
        self.fake.add_attachment(805, "helpdesk.ticket", 507, "Nachtrag.pdf", "application/pdf", b"%PDF neu")
        self.fake.add_message(903, 506, "comment", "Discussions", "<p>Abholung verschoben</p>")
        # Kopf der Gutschrift geändert
        self.fake.touch("purchase.order", 205, x_studio_complaint_number="R-4b")
        run = self.sync()
        self.assertEqual(run.mode, "incremental")
        self.assertEqual(run.state, "done", run.message)
        self.assertEqual(run.count_planned, 5, "201, 202, 203, 205, 206 – nicht die unveränderte 204")
        self.assertEqual(self.grx("V900").line_ids.filtered(lambda l: l.ticket_number == "4711").qty_expected, 7)
        self.assertEqual(self.grx("V901").line_ids.filtered(lambda l: l.ticket_number == "4713").batch_expected,
                         "FPPV-2")
        self.assertIn("Nachtrag.pdf", self.grx("V904").ativ_document_ids.mapped("name"))
        msgs = self.cn("20260003").ativ_ticket_ids.message_ids
        self.assertIn("Abholung verschoben", "".join(msgs.mapped("body")))
        self.assertEqual(self.cn("20260005").complaint_number, "R-4b")
        # Datei geändert → neue Fassung
        self.later(2)
        self.fake.files[801] = b"%PDF-1.4 LS v2"
        self.fake.touch("ir.attachment", 801, checksum="", file_size=14)
        self.fake.db["ir.attachment"][801]["checksum"] = __import__("hashlib").sha1(b"%PDF-1.4 LS v2").hexdigest()
        self.sync()
        doc = self.grx("V900").ativ_document_ids.filtered(lambda d: d.source_id == 801)
        self.assertEqual(bytes(doc.attachment_id.raw), b"%PDF-1.4 LS v2")
        self.assertTrue(self.env["ir.attachment"].search([("name", "=", "Lieferschein 4711.pdf (alte Fassung)")]))

    def test_06_local_receipt_data_preserved(self):
        self.sync()
        grx = self.grx("V900")
        grx.with_user(self.lager).grx_start()
        picking = grx.picking_id
        self.assertEqual(grx.state, "in_progress")
        grx.sudo().write({"slip_note": "Schein weicht ab", "deviation_note": "1 Packung fehlt"})
        line = grx.line_ids.filtered(lambda l: l.ticket_number == "4711")
        # ATIV ändert Menge und lehnt einen Teil ab
        self.later()
        self.fake.touch("purchase.order.line", 301, product_qty=9)
        self.fake.touch("helpdesk.ticket", 501, stage_id=[5, "Rejected"])
        self.fake.touch("helpdesk.ticket", 502, stage_id=[5, "Rejected"])
        self.sync()
        self.assertEqual(grx.state, "in_progress", "begonnene Erfassung bleibt")
        self.assertEqual(grx.picking_id, picking)
        self.assertEqual(grx.slip_note, "Schein weicht ab")
        self.assertEqual(grx.deviation_note, "1 Packung fehlt")
        self.assertEqual(line.qty_expected, 9, "Soll folgt ATIV")
        self.assertEqual(grx.ativ_status, "cancel")
        bodies = " ".join(grx.message_ids.mapped("body"))
        self.assertIn("nach Erfassungsbeginn", bodies)
        self.assertIn("abgelehnt/storniert", bodies)
        # erfasster Vorgang: Soll bleibt eingefroren
        grx.sudo().state = "done"
        self.later(2)
        self.fake.touch("purchase.order.line", 301, product_qty=11)
        self.sync()
        self.assertEqual(line.qty_expected, 9)
        self.assertEqual(grx.state, "done")
        self.assertEqual(self.cn("20260001").line_ids.filtered(lambda l: l.source_id == 301).qty, 11,
                         "die ATIV-Daten selbst sind trotzdem aktuell")

    def test_07_transient_error_no_data_loss(self):
        self.sync()
        cursor = self.connector.ativ_cursor
        self.later()
        self.fake.touch("purchase.order.line", 302, product_qty=3)
        self.fake.fail[("purchase.order", "read")] = AtivError("Zeitüberschreitung", transient=True)
        run = self.sync()
        self.assertEqual(run.state, "running")
        item = run.queue_ids
        self.assertEqual(item.state, "pending")
        self.assertEqual(item.attempts, 1)
        self.assertTrue(item.next_try)
        self.assertEqual(self.connector.ativ_cursor, cursor, "Fortschritt erst nach Erfolg")
        # Verbindung wieder da, nächster Versuch fällig
        del self.fake.fail[("purchase.order", "read")]
        item.next_try = fields.Datetime.now() - timedelta(minutes=1)
        self.connector._ativ_work()
        run.invalidate_recordset()
        self.assertEqual(run.state, "done")
        self.assertEqual(self.connector.ativ_cursor, run.cursor_candidate)
        self.assertEqual(self.grx("V900").line_ids.filtered(lambda l: l.ticket_number == "4712").qty_expected, 3)

    @mute_logger("odoo.addons.plcs_greenrx.models.plcs_ativ_sync")
    def test_08_permanent_error_retry_button(self):
        self.fake.db["x_vorgang"][101]["x_name"] = False
        run = self.sync()
        self.assertEqual(run.state, "partial")
        failed = run.queue_ids.filtered(lambda q: q.state == "failed")
        self.assertEqual(set(failed.mapped("res_id")), {202, 203})
        self.assertIn("Vorgangsnummer", failed[0].error)
        self.assertFalse(self.connector.ativ_cursor, "kein Fortschritt bei Fehlern")
        self.assertTrue(self.grx("V900"), "die übrigen Gutschriften sind übernommen")
        self.fake.db["x_vorgang"][101]["x_name"] = "V901"
        run.with_user(self.manager).action_retry_failed()
        self.connector._ativ_work()
        run.invalidate_recordset()
        self.assertEqual(run.state, "done")
        self.assertEqual(len(self.grx("V901").ativ_credit_note_ids), 2)
        self.assertTrue(self.connector.ativ_cursor)

    def test_09_overlapping_runs(self):
        run1, _m = self.connector._ativ_plan("incremental", "manual")
        run2, msg = self.connector._ativ_plan("incremental", "cron")
        self.assertEqual(run1, run2)
        self.assertIn("schon in Arbeit", msg)
        self.assertEqual(self.env["plcs.ativ.queue"].search_count([("kind", "=", "po")]), 6)
        # Webhook während des Laufs: keine doppelten Schritte
        self.connector._ativ_webhook_receive({"_model": "purchase.order", "_id": 201})
        self.connector._ativ_work()
        self.assertEqual(self.env["plcs.ativ.credit.note"].search_count([]), 6)
        self.assertEqual(len(self.grx("V900")), 1)

    def test_10_status_transitions_and_override(self):
        self.sync()
        grx = self.grx("V900")
        self.later()
        for tid in (501, 502):
            self.fake.touch("helpdesk.ticket", tid, stage_id=[7, "Canceled"])
        self.sync()
        self.assertEqual(grx.state, "cancel")
        self.assertTrue(grx.ativ_auto_state)
        self.later(2)
        self.fake.touch("helpdesk.ticket", 501, stage_id=[4, "Approved"])
        self.sync()
        self.assertEqual(grx.state, "open", "wieder erwartet")
        # Stornierung bleibt sichtbar: Ticketverlauf enthält den beobachteten Wechsel
        observed = grx.ativ_ticket_ids.message_ids.filtered(lambda m: m.kind == "observed")
        self.assertTrue(observed)
        # Lagerleitung öffnet einen nicht erwarteten Vorgang von Hand
        v902 = self.grx("V902")
        with self.assertRaises(UserError):
            v902.with_user(self.lager).action_ativ_override()
        v902.with_user(self.manager).action_ativ_override()
        self.assertEqual(v902.state, "open")
        self.later(3)
        self.fake.touch("purchase.order", 204, amount_total=120.0)
        self.sync()
        self.assertEqual(v902.state, "open", "Festlegung von Hand bleibt")

    def test_11_cutoff_date(self):
        self.connector.expected_since = fields.Date.to_date("2026-10-01")
        self.sync()
        self.assertEqual(self.grx("V900").state, "closed")
        self.assertIn("Stichtag", self.grx("V900").ativ_status_reason)
        self.assertEqual(self.grx("V900").ativ_document_ids.mapped("state"), ["meta"] * 3)
        self.connector.expected_since = fields.Date.to_date("2026-09-01")
        self.sync("full")
        self.assertEqual(self.grx("V900").state, "open")

    def test_12_missing_in_source_not_deleted(self):
        self.sync()
        del self.fake.db["purchase.order"][205]
        run = self.sync("full")
        self.assertEqual(run.count_missing, 1)
        cn = self.cn("20260005")
        self.assertTrue(cn.source_missing)
        self.assertTrue(cn.grx_ticket_id.exists())

    def test_13_read_only_access_to_ativ(self):
        self.sync()
        self.assertTrue(self.fake.calls)
        self.assertTrue(all(method in READ_METHODS for _model, method in self.fake.calls))
        with self.assertRaises(AtivError):
            AtivClient("https://ativ.example.test", "db", "u", "k").call("purchase.order", "write", ids=[1])
        with self.assertRaises(AtivError):
            AtivClient("http://ativ.example.test", "db", "u", "k")  # nur HTTPS

    def test_14_slip_for_not_expected(self):
        self.sync()
        raw = "REF:V902\nREKL:-\nTICKET|PZN|CHARGE|ANZAHL\n4714|12345678|OLD1|5"
        with self.assertRaises(UserError) as err:
            self.env["plcs.greenrx.ticket"].with_user(self.lager).grx_from_slip(raw)
        self.assertIn("nicht erwartet", str(err.exception))

    def test_15_removed_line(self):
        self.sync()
        self.later()
        po = self.fake.db["purchase.order"][201]
        po["order_line"] = [301]
        self.fake.touch("purchase.order", 201)
        self.sync()
        grx = self.grx("V900")
        removed = grx.line_ids.filtered(lambda l: l.ticket_number == "4712")
        self.assertTrue(removed.ativ_removed, "nicht gelöscht, nur gekennzeichnet")
        self.assertEqual(grx.qty_expected_total, 4)
        mirror = self.cn("20260001").line_ids.filtered(lambda l: l.source_id == 302)
        self.assertTrue(mirror.removed, "auch die gespiegelte Position bleibt erhalten")

    def test_16_webhook_and_disable(self):
        self.sync()
        action = self.connector.with_user(self.manager).action_generate_webhook_token()
        token = action["params"]["message"].split("token=")[1]
        C = self.env["plcs.greenrx.connector"]
        self.assertEqual(C._ativ_webhook_connector(token), self.connector)
        self.assertFalse(C._ativ_webhook_connector("falsch"))
        self.assertNotIn(token, self.connector.sudo().webhook_token_hash, "nur Prüfsumme gespeichert")
        self.later()
        self.fake.touch("helpdesk.ticket", 507, x_studio_batch_number="LEG2", name="LEG2")
        self.assertTrue(self.connector._ativ_webhook_receive({"_model": "helpdesk.ticket", "_id": 507}))
        self.assertFalse(self.connector._ativ_webhook_receive({"_model": "res.users", "_id": 2}))
        self.connector._ativ_work()
        self.assertEqual(self.grx("V904").line_ids.batch_expected, "LEG2")
        self.connector.with_user(self.manager).action_ativ_disable()
        self.assertFalse(self.connector.sync_enabled)
        self.assertFalse(C._ativ_webhook_connector(token))

    def test_17_reconciliation(self):
        """Mengen- und Referenzabgleich wie bei der Abnahme gegen das Quellsystem."""
        self.sync()
        source_names = {r["name"] for r in self.fake.db["purchase.order"].values()}
        target = self.env["plcs.ativ.credit.note"].search([("connector_id", "=", self.connector.id)])
        self.assertEqual(set(target.mapped("name")), source_names)
        source_qty = sum(l["product_qty"] for l in self.fake.db["purchase.order.line"].values())
        self.assertEqual(sum(target.line_ids.mapped("qty")), source_qty)
        plcs_qty = sum(self.env["plcs.greenrx.ticket.line"].search([("ativ_line_key", "!=", False)])
                       .mapped("qty_expected"))
        self.assertEqual(plcs_qty, source_qty)

    def test_18_missing_products_created(self):
        """1.7.3: Fehlt der Artikel zur PZN, legt die Synchronisierung ihn an – genau einmal."""
        (self.prod_byetta | self.prod_brilique).write({"plcs_pzn": False})
        self.prod_byetta.plcs_pzn = "99999998"
        self.prod_brilique.plcs_pzn = "99999997"
        self.sync()
        lines = self.grx("V900").line_ids
        byetta = lines.filtered(lambda l: l.pzn == "12345678").product_id
        brilique = lines.filtered(lambda l: l.pzn == "07560038").product_id
        self.assertTrue(byetta and brilique, "alle Positionen haben einen Artikel")
        self.assertNotIn(byetta, self.prod_byetta | self.prod_brilique)
        self.assertEqual(byetta.plcs_pzn, "12345678")
        self.assertEqual(byetta.barcode, "04150123456782")
        self.assertEqual(byetta.tracking, "lot")
        self.assertTrue(byetta.use_expiration_date)
        self.assertEqual(byetta.plcs_storage_temp, "cool", "„2–8 °C“ bei ATIV → kühl")
        self.assertEqual(brilique.plcs_pzn, "07560038")
        self.assertEqual(brilique.plcs_storage_temp, "ambient")
        # Zweiter Lauf und Scan-Suche finden denselben Artikel, kein Duplikat
        self.later()
        self.fake.touch("purchase.order.line", 301, product_qty=7)
        self.sync()
        Product = self.env["product.product"]
        self.assertEqual(Product.search_count([("plcs_pzn", "=", "12345678")]), 1)
        self.assertEqual(Product._plcs_find_by_code(gtin="04150123456782"), byetta)

    def test_19_missing_products_option_off(self):
        """Option aus: keine Artikelanlage, Position bleibt ohne Artikel."""
        self.connector.ativ_create_products = False
        self.prod_brilique.plcs_pzn = "99999997"
        self.sync()
        line = self.grx("V900").line_ids.filtered(lambda l: l.pzn == "07560038")
        self.assertFalse(line.product_id)
        self.assertFalse(self.env["product.product"].search([("plcs_pzn", "=", "07560038")]))


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append((url, data, headers, timeout))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestAtivClient(TransactionCase):

    def test_json2_request_and_errors(self):
        import json
        import requests
        session = _Session([_Resp(200, [1, 2]), _Resp(401, {"message": "x"}), _Resp(403, {"message": "kein Recht"}),
                            _Resp(503, {"message": "busy"}), requests.Timeout()])
        client = AtivClient("https://ativ.example.test/", "ativ-db", "u", "KEY", timeout=7, session=session)
        self.assertEqual(client.call("purchase.order", "search", domain=[("id", ">", 0)]), [1, 2])
        url, body, headers, timeout = session.requests[0]
        self.assertEqual(url, "https://ativ.example.test/json/2/purchase.order/search")
        self.assertEqual(headers["Authorization"], "Bearer KEY")
        self.assertEqual(headers["X-Odoo-Database"], "ativ-db")
        self.assertEqual(timeout, 7)
        body = json.loads(body)
        self.assertEqual(body["domain"], [["id", ">", 0]])
        self.assertEqual(body["context"], {"lang": "en_US", "active_test": False})
        with self.assertRaises(AtivError) as e:
            client.call("purchase.order", "search", domain=[])
        self.assertFalse(e.exception.transient)
        self.assertIn("API-Schlüssel", str(e.exception))
        with self.assertRaises(AtivError) as e:
            client.call("purchase.order", "read", ids=[1])
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(AtivError) as e:
            client.call("purchase.order", "read", ids=[1])
        self.assertTrue(e.exception.transient)
        with self.assertRaises(AtivError) as e:
            client.call("purchase.order", "read", ids=[1])
        self.assertTrue(e.exception.transient)
        self.assertNotIn("KEY", str(e.exception), "Schlüssel nie in Meldungen")


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestAtivWebhookHttp(HttpCase):

    def test_webhook_endpoint(self):
        connector = self.env["plcs.greenrx.connector"].create({
            "name": "ATIV Webhook", "mode": "ativ", "url": "https://ativ.example.test", "db": "ativ-test",
            "login": "x", "api_key": "geheim"})
        token = connector.action_generate_webhook_token()["params"]["message"].split("token=")[1]
        url = "/plcs_greenrx/ativ/webhook"
        res = self.url_open(url, data='{"_model": "purchase.order", "_id": 5}',
                            headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 401)
        res = self.url_open(url + "?token=" + token, data='{"_model": "purchase.order", "_id": 5}',
                            headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["queued"])
        item = self.env["plcs.ativ.queue"].search([("connector_id", "=", connector.id), ("kind", "=", "ref")])
        self.assertEqual((item.res_model, item.res_id), ("purchase.order", 5))
        res = self.url_open(url + "?token=" + token, data="kein json", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
