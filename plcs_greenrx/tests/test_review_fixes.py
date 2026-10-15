"""Regressionstests zum Code-Review vom 14.10.2026 (Befunde R01–R18).

Jeder Test prüft den Fehlerfall (muss scheitern) und, wo sinnvoll, den Gutfall.
"""
from unittest.mock import patch

import psycopg2

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..tools import gs1
from . import test_greenrx_flow as flow
from .test_greenrx_flow import code


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestReviewFixes(TransactionCase):

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
        cls.cfg = cls.company
        cls.lager = new_test_user(cls.env, login="rv_lager", name="Lager Eins", groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="rv_vp1", name="VP Eins", groups="plcs_greenrx.group_grx_vp")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.prod_b = cls.env["product.product"].create({
            "name": "Artikel B", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "plcs_pzn": "87654321",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")
        cls.lager2 = new_test_user(cls.env, login="rv_lager2", name="Lager Zwei",
                                   groups="plcs_greenrx.group_grx_user")

    def _closed_box(self, name, box_code, serials, ampel="gruen", note=False, **answers):
        """Vorgang mit einer abgeschlossenen Box, Paket fertig (Ware in Quarantäne/Sperrlager)."""
        ticket = self._ticket(name=name, lines=[{"pzn": "12345678", "qty": len(serials), "ticket_number": "T1",
                                                 "batch": "BYE-T01"}])
        box = self._fill_box(ticket, box_code, self.gtin_a, serials)
        box.grx_to_check()
        self._close(box, ampel=ampel, note=note, **answers)
        ticket.with_user(self.lager).grx_finish()
        return ticket, box

    # ------------------------------------------------------------------
    # R01: Kontextwerte aus RPC-Aufrufen geben keine Berechtigung
    # ------------------------------------------------------------------
    def test_r01_context_flag_does_not_release_quarantine(self):
        ticket, box = self._closed_box("RV-01", "GX140", ["R01A", "R01B"])
        quants = box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)
        self.assertEqual(quants.location_id, self.cfg.plcs_grx_quarantine_location_id)
        stock = self.cfg.plcs_grx_stock_location_id
        for user in (self.lager, self.vp1):
            with self.assertRaisesRegex(UserError, "GreenRX-Freigabe der VP"):
                quants.with_user(user).with_context(plcs_grx_controlled_move=True).move_quants(
                    location_dest_id=stock)
        # auch über einen normalen Transfer mit dem Kontextwert
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.cfg.plcs_grx_release_type_id.id,
            "location_id": quants.location_id.id, "location_dest_id": stock.id,
        })
        self.env["stock.move"].create({
            "product_id": self.prod_a.id, "product_uom_qty": 2, "uom_id": self.prod_a.uom_id.id,
            "picking_id": picking.id, "location_id": quants.location_id.id, "location_dest_id": stock.id,
            "move_line_ids": [(0, 0, {"product_id": self.prod_a.id, "lot_id": q.lot_id.id, "quantity": 1,
                                      "location_id": q.location_id.id, "location_dest_id": stock.id,
                                      "package_id": box.package_id.id, "result_package_id": box.package_id.id})
                              for q in quants],
        })
        picking.action_confirm()
        picking.move_ids.picked = True
        with self.assertRaisesRegex(UserError, "GreenRX-Freigabe der VP"):
            picking.with_user(self.lager).with_context(
                plcs_grx_controlled_move=True, skip_backorder=True).button_validate()
        self.assertEqual(box.package_id.quant_ids.filtered(lambda q: q.quantity > 0).location_id,
                         self.cfg.plcs_grx_quarantine_location_id)
        # Gegenprobe: VP-Freigabe bewegt die Ware
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")
        self.assertEqual(box.location_id, stock)

    def test_r01_finish_flag_does_not_book_receipt(self):
        ticket = self._ticket(name="RV-01B", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX141", self.gtin_a, ["R01C"])
        box.grx_to_check()
        self._close(box)
        # zweite Box offen: „Paket fertig“ wäre nicht erlaubt
        self._fill_box(ticket, "GX142", self.gtin_a, ["R01D"])
        picking = ticket.picking_id
        picking.move_ids.picked = True
        with self.assertRaisesRegex(UserError, "Paket fertig"):
            picking.with_user(self.lager).with_context(
                plcs_grx_finish=True, skip_backorder=True, cancel_backorder=True).button_validate()
        self.assertNotEqual(picking.state, "done")
        # Verknüpfung lösen, um die Prüfung zu umgehen: abgewiesen
        with self.assertRaises(UserError):
            picking.with_user(self.lager).write({"plcs_grx_ticket_id": False})
        self.assertEqual(picking.plcs_grx_ticket_id, ticket)

    # ------------------------------------------------------------------
    # R02: Freigabe nur für genau die erfasste Ware
    # ------------------------------------------------------------------
    def test_r02_unpack_in_quarantine_is_blocked(self):
        ticket, box = self._closed_box("RV-02", "GX143", ["R02A"])
        with self.assertRaisesRegex(UserError, "ausgepackt oder umgepackt"):
            box.package_id.with_user(self.lager).unpack()
        self.assertEqual(len(box.package_id.quant_ids.filtered(lambda q: q.quantity > 0)), 1)

    def test_r02_release_refuses_when_content_differs(self):
        ticket, box = self._closed_box("RV-02B", "GX144", ["R02B", "R02C"])
        quarantine = self.cfg.plcs_grx_quarantine_location_id
        # Fremde Packung landet (z. B. per Inventur) in derselben Box
        foreign = self.env["stock.lot"].create({"name": "FREMD-1", "product_id": self.prod_a.id})
        self.env["stock.quant"]._update_available_quantity(
            self.prod_a, quarantine, 1, lot_id=foreign, package_id=box.package_id)
        with self.assertRaisesRegex(UserError, "passt nicht zur Erfassung"):
            box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "to_release")
        self.assertFalse(box.vp_picking_id)
        self.assertEqual(box.package_id.quant_ids.filtered(lambda q: q.quantity > 0).location_id, quarantine)

    # ------------------------------------------------------------------
    # R07: gleichzeitige Annahme derselben Packung in zwei Boxen
    # ------------------------------------------------------------------
    def test_r07_parallel_acceptance_is_rejected_by_database(self):
        t1 = self._ticket(name="RV-07A", lines=[{"pzn": "12345678", "qty": 1}])
        t2 = self._ticket(name="RV-07B", lines=[{"pzn": "12345678", "qty": 1}])
        self._fill_box(t1, "GX145", self.gtin_a, ["R07-SAME"])
        Box = self.env["plcs.greenrx.box"].with_user(self.lager2)
        box2 = Box.browse(Box.grx_open_box(t2.id, "GX146")["id"])
        Scan = type(self.env["plcs.greenrx.scan"])
        original = Scan.search

        def blind_search(records, domain, *args, **kwargs):
            # Simuliert den zweiten Arbeitsplatz, der die Annahme des ersten noch nicht sieht
            if any(isinstance(t, (list, tuple)) and t[0] == "serial" for t in domain):
                return records.browse()
            return original(records, domain, *args, **kwargs)

        with patch.object(Scan, "search", blind_search), mute_logger("odoo.sql_db"):
            res = box2.grx_scan(code(self.gtin_a, "R07-SAME", "BYE-T01", self.exp_ok))
        self.assertEqual(res["status"], "error", res)
        self.assertIn("anderen Arbeitsplatz", res["message"])
        self.assertEqual(box2.qty, 0)
        self.assertFalse(box2.product_id)
        # direkt in der Datenbank: zweite Annahme verboten
        with self.assertRaises(psycopg2.errors.UniqueViolation), mute_logger("odoo.sql_db"), \
                self.env.cr.savepoint():
            self.env["plcs.greenrx.scan"].sudo().create({
                "box_id": box2.id, "serial": "R07-SAME", "product_id": self.prod_a.id, "accepted": True,
                "result": "ok"})

    # ------------------------------------------------------------------
    # R08: Identität einer erfassten Packung ist fest
    # ------------------------------------------------------------------
    def test_r08_grx_lot_identity_locked(self):
        ticket, box = self._closed_box("RV-08", "GX147", ["R08A"])
        lot = box.lot_ids
        self.assertEqual(lot.name, "R08A")
        for vals in ({"name": "R08X"}, {"plcs_batch": "ANDERS"}, {"product_id": self.prod_b.id}):
            with self.assertRaisesRegex(UserError, "kann nicht geändert werden"):
                lot.with_user(self.lager).write(vals)
        with self.assertRaises(UserError):
            lot.with_user(self.lager).unlink()
        lot.with_user(self.lager).write({"note": "<p>Hinweis</p>"})  # andere Felder bleiben änderbar
        self.assertEqual(lot.name, "R08A")
        # Selbst wenn das Los über Superuser umbenannt wird: die Seriennummer bleibt gesperrt
        lot.sudo().write({"name": "R08-UMBENANNT"})
        t2 = self._ticket(name="RV-08B", lines=[{"pzn": "12345678", "qty": 1}])
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        box2 = Box.browse(Box.grx_open_box(t2.id, "GX148")["id"])
        res = box2.grx_scan(code(self.gtin_a, "R08A", "BYE-T01", self.exp_ok))
        self.assertEqual(res["status"], "error", res)
        self.assertEqual(res["title"], "Diese Packung ist schon erfasst")

    # ------------------------------------------------------------------
    # R09: strenger als der Vorschlag nur mit Grund (auch Gelb → Rot)
    # ------------------------------------------------------------------
    def test_r09_yellow_to_red_needs_reason(self):
        ticket = self._ticket(name="RV-09", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX149", self.gtin_a, ["R09A"])
        box.grx_to_check()
        self._all_yes(box, q_damage="nein")
        self.assertEqual(box.ampel_suggest, "gelb")
        box.grx_save_check(ampel="rot", note="")
        with self.assertRaisesRegex(UserError, "strenger als der Vorschlag"):
            box.grx_to_print()
        self.assertEqual(box.state, "check")
        box.grx_save_check(note="Verpackung eingedrückt, Inhalt fraglich")
        box.grx_to_print()
        self.assertEqual(box.state, "print")
        # Gegenprobe: Ampel = Vorschlag braucht keinen eigenen Grund
        box2 = self._fill_box(ticket, "GX150", self.gtin_a, ["R09B"])
        box2.grx_to_check()
        self._all_yes(box2, q_damage="nein")
        box2.grx_save_check(ampel="gelb", note="")
        box2.grx_to_print()
        self.assertEqual(box2.state, "print")

    # ------------------------------------------------------------------
    # R10: Ticketvergleich im Formblatt
    # ------------------------------------------------------------------
    def _row(self, values, text):
        return next(r for r in values["rows_eingang"] if r["text"] == text)

    def test_r10_ticket_comparison_without_line_is_no(self):
        ticket = self._ticket(name="RV-10", lines=[{"pzn": "87654321", "qty": 1}])  # nur Artikel B angemeldet
        box = self._fill_box(ticket, "GX151", self.gtin_a, ["R10A"])  # Artikel A gescannt
        self.assertEqual(box.slip_match, "not_on_slip")
        values = box._grx_report_values()
        self.assertEqual(values["rows_annahme"][0]["value"], "nein")
        for text in ("– PZN", "– Bezeichnung", "– Charge"):
            row = self._row(values, text)
            self.assertEqual(row["value"], "nein", row)
            self.assertIn("keine Position im Ticket", row["remark"])

    def test_r10_ticket_comparison_with_line(self):
        ticket = self._ticket(name="RV-10B", lines=[
            {"pzn": "12345678", "qty": 1, "batch": "BYE-T01", "ticket_number": "A"},
            {"pzn": "87654321", "qty": 1, "ticket_number": "B"}])
        box = self._fill_box(ticket, "GX152", self.gtin_a, ["R10B"])
        values = box._grx_report_values()
        self.assertEqual([self._row(values, t)["value"] for t in ("– PZN", "– Bezeichnung", "– Charge")],
                         ["ja", "ja", "ja"])
        # Position ohne Charge: Charge nicht prüfbar (leer), nicht „ja“
        gtin_b = gs1.pzn_to_gtin("87654321")
        self.prod_b.barcode = gtin_b
        box2 = self._fill_box(ticket, "GX153", gtin_b, ["R10C"], batch="XY1")
        row = self._row(box2._grx_report_values(), "– Charge")
        self.assertEqual(row["value"], "")
        self.assertIn("keine Charge angegeben", row["remark"])

    # ------------------------------------------------------------------
    # R11: Druckinhalt und Daten bleiben zusammen
    # ------------------------------------------------------------------
    def test_r11_master_data_change_after_print_invalidates(self):
        ticket = self._ticket(name="RV-11", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX154", self.gtin_a, ["R11A"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        self.assertFalse(box._grx_print_outdated())
        self.prod_a.plcs_storage_temp = "cool" if self.prod_a.plcs_storage_temp != "cool" else "ambient"
        self.assertTrue(box._grx_print_outdated())
        with self.assertRaisesRegex(UserError, "nach dem letzten Druck geändert"):
            box.grx_close()
        # Neu drucken → Abschluss möglich
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "to_release")
        printed = box._grx_report_values()
        # Nach dem Abschluss: Nachdruck zeigt den bestätigten Inhalt, auch wenn Stammdaten sich ändern
        ticket.sudo().partner_id = False
        ticket.sudo().partner_name = "Andere Apotheke"
        self.prod_a.plcs_storage_temp = "cool" if self.prod_a.plcs_storage_temp != "cool" else "ambient"
        later = box._grx_report_values()
        self.assertEqual(later["sender"], printed["sender"])
        self.assertEqual(later["rows_annahme"], printed["rows_annahme"])

    def test_r11_other_box_of_package_does_not_invalidate(self):
        ticket = self._ticket(name="RV-11B", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX155", self.gtin_a, ["R11B"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        other = self._fill_box(ticket, "GX156", self.gtin_a, ["R11C"], user=self.lager2)
        other.grx_to_check()
        self._close(other)
        self.assertFalse(box._grx_print_outdated(), "Zwischenstand „im Paket bisher“ zählt nicht")
        box.grx_close()
        self.assertEqual(box.state, "to_release")

    # ------------------------------------------------------------------
    # R18: Prüfer und Erfasser auf dem Formblatt
    # ------------------------------------------------------------------
    def test_r18_checker_and_capturer_separate(self):
        ticket = self._ticket(name="RV-18", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX157", self.gtin_a, ["R18A"])  # erfasst von lager
        box.grx_to_check()
        vp_box = box.with_user(self.vp1)  # VP übernimmt die Vorprüfung
        self._all_yes(vp_box)
        vp_box.grx_save_check(ampel="gruen")
        vp_box.grx_to_print()
        self.assertEqual(box.checked_by, self.vp1)
        values = box._grx_report_values()
        self.assertTrue(values["checked_by"].startswith(self.vp1.name))
        self.assertEqual(values["captured_by"], self.lager.name)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()
        self.assertIn("Erfasst von: <span class=\"odoo\">%s</span>" % self.lager.name, html)

    # ------------------------------------------------------------------
    # R05, R06, R13: Datenübernahme
    # ------------------------------------------------------------------
    def test_r05_legacy_connector_reads_all_pages(self):
        class Proxy:
            def __init__(self, endpoint):
                pass

            def authenticate(self, *args):
                return 7

            def execute_kw(self, db, uid, key, model, method, args, kw):
                if model == "helpdesk.ticket" and method == "search_read":
                    rows = [{"id": i, "ticket_ref": "T%s" % i, "x_ref": "LV%04d" % i} for i in range(1, 2002)]
                    offset, limit = kw.get("offset", 0), kw.get("limit")
                    return rows[offset:offset + limit] if limit else rows[offset:]
                raise AssertionError((model, method))

        connector = self.env["plcs.greenrx.connector"].create({
            "name": "Alt", "mode": "legacy", "url": "https://greenrx.test", "db": "x", "login": "api",
            "api_key": "secret", "f_reference": "x_ref", "f_product": False,
        })
        with patch.object(type(connector), "_proxy", lambda self, endpoint: Proxy(endpoint)):
            connector.action_sync()
        self.assertEqual(self.env["plcs.greenrx.ticket"].search_count([("name", "=like", "LV%")]), 2001)
        self.assertTrue(connector.last_sync)

    def test_r06_upsert_only_touches_target_company(self):
        other = self.env["res.company"].create({"name": "Zweitfirma GmbH"})
        T = self.env["plcs.greenrx.ticket"]
        b = T.create({"name": "RV-06", "company_id": other.id, "external_id": "RV-06", "source": "api",
                      "line_ids": [(0, 0, {"pzn": "12345678", "qty_expected": 7, "ticket_number": "X"})]})
        both = T.with_context(allowed_company_ids=[self.company.id, other.id]).with_company(self.company)
        res = both.api_upsert([{"external_id": "RV-06", "name": "RV-06",
                                "lines": [{"pzn": "12345678", "qty": 1, "ticket_number": "X"}]}])
        self.assertFalse(res["errors"], res)
        self.assertEqual(res["created"], 1)
        self.assertEqual(b.line_ids.qty_expected, 7, "Vorgang der anderen Firma unverändert")
        a = T.search([("name", "=", "RV-06"), ("company_id", "=", self.company.id)])
        self.assertEqual(a.line_ids.qty_expected, 1)

    def test_r13_zero_quantity_and_empty_lines(self):
        ticket = self._ticket(name="RV-13", lines=[{"pzn": "12345678", "qty": 2, "ticket_number": "A"},
                                                   {"pzn": "87654321", "qty": 1, "ticket_number": "B"}])
        T = self.env["plcs.greenrx.ticket"]
        T.api_upsert([{"external_id": "RV-13", "name": "RV-13",
                       "lines": [{"pzn": "12345678", "qty": 0, "ticket_number": "A"}]}])
        line_a = ticket.line_ids.filtered(lambda l: l.ticket_number == "A")
        self.assertEqual(line_a.qty_expected, 0)
        self.assertTrue(any("Soll auf 0" in (m.body or "") for m in ticket.message_ids))
        # Kopfdaten ohne Positionen: Positionen bleiben
        T.api_upsert([{"external_id": "RV-13", "name": "RV-13", "complaint_number": "NEU"}])
        self.assertEqual(ticket.complaint_number, "NEU")
        self.assertEqual(len(ticket.line_ids), 2)
        # Neue Position mit Menge 0 wird nicht angelegt
        T.api_upsert([{"external_id": "RV-13", "name": "RV-13",
                       "lines": [{"pzn": "11112222", "qty": 0, "ticket_number": "C"}]}])
        self.assertEqual(len(ticket.line_ids), 2)

    # ------------------------------------------------------------------
    # R14: GS1 ohne Trennzeichen nur bei echter Eindeutigkeit
    # ------------------------------------------------------------------
    def test_r14_ambiguous_code_without_separators(self):
        with self.assertRaises(gs1.Gs1Error):
            gs1.parse("01041501234567821730123110A301240A201211250101")
        # Gegenprobe: eindeutiger Code ohne Trennzeichen (Seriennummer am Ende) bleibt lesbar
        parsed = gs1.parse("01" + gs1.pzn_to_gtin("12345678") + "17280131" + "21ABC123")
        self.assertEqual(parsed["serial"], "ABC123")

    # ------------------------------------------------------------------
    # R15: Untermodelle nur für die eigene Firma lesbar
    # ------------------------------------------------------------------
    def test_r15_sub_models_restricted_to_company(self):
        other = self.env["res.company"].create({"name": "Drittfirma GmbH"})
        ticket = self.env["plcs.greenrx.ticket"].create({
            "name": "RV-15", "company_id": other.id,
            "line_ids": [(0, 0, {"pzn": "12345678", "qty_expected": 1, "ticket_number": "FREMD"})]})
        box = self.env["plcs.greenrx.box"].sudo().create({
            "ticket_id": ticket.id, "user_id": self.lager.id,
            "package_id": self.env["stock.package"].search([("name", "=", "GX198")], limit=1).id})
        self.env["plcs.greenrx.scan"].sudo().create({"box_id": box.id, "serial": "FREMD-SN", "raw": "x"})
        Line = self.env["plcs.greenrx.ticket.line"].with_user(self.lager)
        Scan = self.env["plcs.greenrx.scan"].with_user(self.lager)
        self.assertFalse(Line.search_read([("ticket_number", "=", "FREMD")], ["id"]))
        self.assertFalse(Scan.search_read([("serial", "=", "FREMD-SN")], ["id"]))
        # Gegenprobe: eigene Firma lesbar
        own = self._ticket(name="RV-15B", lines=[{"pzn": "12345678", "qty": 1, "ticket_number": "EIGEN"}])
        self.assertTrue(Line.search_read([("ticket_number", "=", "EIGEN")], ["id"]))
        self.assertTrue(own)
