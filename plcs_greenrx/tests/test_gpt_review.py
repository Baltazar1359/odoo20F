"""Regressionstests zum externen Prüfbericht vom 15.10.2026 (GPT, Befunde F01–F08).

Jeder Test prüft den Fehlerfall (muss scheitern bzw. erkannt werden) und den Gutfall.
"""
from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import BaseCase, HttpCase, TransactionCase, new_test_user, tagged
from odoo.tools import mute_logger

from ..tools import gs1
from ..tools.ativ_client import AtivError
from . import test_greenrx_flow as flow
from .test_ativ_sync import AtivCase
from .test_greenrx_flow import code

ANSWERS = ("q_damage", "q_temp_controlled", "q_temp_log", "q_manufacturer", "q_storage_proof", "q_seal")


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGptReviewBox(TransactionCase):

    _ticket = flow.TestGreenrxFlow._ticket
    _all_yes = flow.TestGreenrxFlow._all_yes
    _fill_box = flow.TestGreenrxFlow._fill_box
    _close = flow.TestGreenrxFlow._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="gr_lager", groups="plcs_greenrx.group_grx_user")
        cls.lager2 = new_test_user(cls.env, login="gr_lager2", groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="gr_vp1", groups="plcs_greenrx.group_grx_vp")
        cls.vp2 = new_test_user(cls.env, login="gr_vp2", groups="plcs_greenrx.group_grx_vp")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")

    def _row(self, values, text):
        return next(r for r in values["rows_eingang"] if r["text"] == text)

    def _to_print(self, box, ampel="gruen", note=False):
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel=ampel, note=note)
        box.grx_to_print()

    # ------------------------------------------------------------------
    # F01: Von einer VP gesperrte Box bleibt für eine andere VP erreichbar
    # ------------------------------------------------------------------
    def test_f01_blocked_box_listed_for_other_vp(self):
        ticket = self._ticket(name="GR-01", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX121", self.gtin_a, ["F01A"], user=self.vp1).with_user(self.vp1)
        box.grx_to_check()
        self._close(box)
        ticket.with_user(self.lager).grx_finish()
        box.grx_vp_block("Umkarton nass")
        self.assertEqual(box.state, "blocked")

        # Andere VP: Box steht unter „Gesperrte Boxen erneut prüfen“, freigebbar
        listing = self.env["plcs.greenrx.box"].with_user(self.vp2).grx_vp_list()
        self.assertNotIn(box.id, [b["id"] for b in listing["boxes"]], "keine offene Aufgabe mehr")
        row = next(b for b in listing["blocked"] if b["id"] == box.id)
        self.assertTrue(row["vp_blocked"])
        self.assertTrue(row["can_release"])
        self.assertEqual(row["vp_reason"], "Umkarton nass")
        self.assertEqual(row["box"], "GX121", "Scan der Box findet sie über die Liste")
        # Zählt nicht als offene Aufgabe auf der Startseite
        home = self.env["plcs.greenrx.ticket"].with_user(self.vp2).grx_home()
        self.assertEqual(home["vp_count"], 0)
        # Beteiligte VP sieht sie, darf aber nicht freigeben
        own = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        self.assertFalse(next(b for b in own["blocked"] if b["id"] == box.id)["can_release"])
        with self.assertRaises(AccessError):
            box.grx_vp_release(note="doch ok")
        # Andere VP: ohne Begründung nicht, mit Begründung frei
        with self.assertRaisesRegex(UserError, "Begründung"):
            box.with_user(self.vp2).grx_vp_release()
        box.with_user(self.vp2).grx_vp_release(note="Packungen geprüft, trocken")
        self.assertEqual(box.state, "released")
        after = self.env["plcs.greenrx.box"].with_user(self.vp2).grx_vp_list()
        self.assertNotIn(box.id, [b["id"] for b in after["blocked"]])

    def test_f01_red_box_still_waits_in_main_list(self):
        """Gegenprobe: rot abgeschlossene Box (noch keine VP-Entscheidung) bleibt offene Aufgabe."""
        ticket = self._ticket(name="GR-01B", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX122", self.gtin_a, ["F01B"])
        box.grx_to_check()
        self._close(box, ampel="rot", q_seal="nein")
        ticket.with_user(self.lager).grx_finish()
        self.assertEqual(box.state, "blocked")
        listing = self.env["plcs.greenrx.box"].with_user(self.vp1).grx_vp_list()
        self.assertIn(box.id, [b["id"] for b in listing["boxes"]])
        self.assertNotIn(box.id, [b["id"] for b in listing["blocked"]])
        self.assertFalse(next(b for b in listing["boxes"] if b["id"] == box.id)["vp_blocked"])

    # ------------------------------------------------------------------
    # F02: Bewertung aus den aktuellen Soll-Daten
    # ------------------------------------------------------------------
    def test_f02_batch_changed_after_first_scan(self):
        ticket = self._ticket(name="GR-02", lines=[{"pzn": "12345678", "qty": 1, "batch": "BYE-T01",
                                                    "ticket_number": "T1"}])
        box = self._fill_box(ticket, "GX123", self.gtin_a, ["F02A"])
        self.assertEqual(box.slip_match, "ok")
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "gruen")
        # ATIV korrigiert die Soll-Charge (wie _ativ_sync_lines während der Erfassung)
        box.line_id.sudo().batch_expected = "BYE-T99"
        self.assertEqual(box.slip_match, "ok", "erster Scan bleibt im Verlauf unverändert")
        self.assertEqual(box.slip_match_current, "batch_differs")
        self.assertEqual(box.ampel_suggest, "gelb")
        self.assertIn("Charge weicht vom Retourenschein ab", box.reasons)
        self.assertEqual(self._row(box._grx_report_values(), "– Charge")["value"], "nein")
        # und zurück: alter Fehlvergleich wird aufgehoben
        box.line_id.sudo().batch_expected = "BYE-T01"
        self.assertEqual(box.ampel_suggest, "gruen")
        self.assertEqual(self._row(box._grx_report_values(), "– Charge")["value"], "ja")

    def test_f02_batch_corrected_to_scan(self):
        """B → A: Erster Scan wich ab, ATIV korrigiert auf die gescannte Charge → passt."""
        ticket = self._ticket(name="GR-02B", lines=[{"pzn": "12345678", "qty": 1, "batch": "FALSCH"}])
        box = self._fill_box(ticket, "GX124", self.gtin_a, ["F02B"])
        self.assertEqual(box.slip_match, "batch_differs")
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "gelb")
        box.line_id.sudo().batch_expected = "BYE-T01"
        self.assertEqual(box.slip_match_current, "ok")
        self.assertEqual(box.ampel_suggest, "gruen")

    def test_f02_removed_line_and_zero_quantity(self):
        ticket = self._ticket(name="GR-02C", lines=[{"pzn": "12345678", "qty": 1, "ticket_number": "T7"}])
        box = self._fill_box(ticket, "GX125", self.gtin_a, ["F02C"])
        box.grx_to_check()
        self._all_yes(box)
        self.assertEqual(box.ampel_suggest, "gruen")
        # Menge bei ATIV auf 0 (Position bleibt): Soll 0, Ist 1
        box.line_id.sudo().qty_expected = 0
        self.assertEqual(box.ampel_suggest, "gelb")
        self.assertIn("Mehr Packungen als angemeldet", box.reasons)
        # Position bei ATIV entfernt
        box.line_id.sudo().write({"qty_expected": 1, "ativ_removed": True})
        self.assertEqual(box.slip_match_current, "not_on_slip")
        self.assertEqual(box.ampel_suggest, "gelb")
        values = box._grx_report_values()
        self.assertEqual(values["rows_annahme"][0]["value"], "nein")
        self.assertIn("bei ATIV entfernt", self._row(values, "– Charge")["remark"])
        self.assertTrue(any("bei ATIV entfernt" in c["label"] for c in box._grx_system_checks()))

    # ------------------------------------------------------------------
    # F08: Neudruck/Abschluss prüfen die Ampel gegen die aktuelle Bewertung
    # ------------------------------------------------------------------
    def test_f08_reprint_does_not_keep_milder_ampel(self):
        self.company.plcs_grx_min_shelf_months = 3
        exp6 = (fields.Date.today() + relativedelta(months=6)).strftime("%y%m00")
        ticket = self._ticket(name="GR-08", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX126", self.gtin_a, ["F08A"], exp=exp6)
        self._to_print(box)
        box.grx_print()
        box.grx_confirm_signed(True)
        # Mindestlaufzeit verschärft: Vorschlag jetzt rot
        self.company.plcs_grx_min_shelf_months = 12
        self.assertEqual(box._grx_suggestion()[0], "rot")
        with self.assertRaisesRegex(UserError, "Bewertung hat sich seit der Vorprüfung geändert"):
            box.grx_close()
        with self.assertRaisesRegex(UserError, "Bewertung hat sich seit der Vorprüfung geändert"):
            box.grx_print()
        self.assertEqual(box.state, "print")
        self.assertEqual(box.ampel, "gruen")
        # Richtiger Weg: zurück, Ampel neu wählen, neu drucken, abschließen
        box.grx_back()
        box.grx_save_check(ampel="rot")
        box.grx_to_print()
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.ampel, "rot")
        self.assertNotEqual(box.state, "print")

    def test_f08_other_box_quantity_does_not_block(self):
        """Gegenprobe: Mehr Packungen durch eine andere Box ändern nur den Paketabgleich (beim Paketabschluss)."""
        ticket = self._ticket(name="GR-08B", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX127", self.gtin_a, ["F08B"])
        self._to_print(box)
        box.grx_print()
        box.grx_confirm_signed(True)
        other = self._fill_box(ticket, "GX128", self.gtin_a, ["F08C"], user=self.lager2)
        self.assertEqual(box._grx_suggestion()[0], "gelb", "Paket hat jetzt mehr als angemeldet")
        # Der Text der Gründe auf dem Formblatt ändert sich (wie bisher): neu drucken.
        # Der Neudruck selbst wird durch den Mengenabgleich nicht gesperrt.
        if box._grx_print_outdated():
            box.grx_print()
            box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "to_release")
        self.assertTrue(other)

    def test_f08_storage_change_still_handled_by_reprint(self):
        """Gegenprobe: Stammdatenänderung ohne Einfluss auf die Ampel bleibt über den Neudruck lösbar."""
        ticket = self._ticket(name="GR-08C", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX129", self.gtin_a, ["F08D"])
        self._to_print(box)
        box.grx_print()
        box.grx_confirm_signed(True)
        self.prod_a.plcs_storage_temp = "cool" if self.prod_a.plcs_storage_temp != "cool" else "ambient"
        with self.assertRaisesRegex(UserError, "nach dem letzten Druck geändert"):
            box.grx_close()
        box.grx_print()
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(box.state, "to_release")

    def test_f08_closed_box_reprint_unchanged(self):
        """Nachdruck einer abgeschlossenen Box prüft die Ampel nicht erneut (historischer Stand)."""
        exp6 = (fields.Date.today() + relativedelta(months=6)).strftime("%y%m00")
        self.company.plcs_grx_min_shelf_months = 3
        ticket = self._ticket(name="GR-08D", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX130", self.gtin_a, ["F08E"], exp=exp6)
        box.grx_to_check()
        self._close(box)
        self.company.plcs_grx_min_shelf_months = 12
        box.grx_print()  # kein Fehler
        self.assertEqual(box.ampel, "gruen")


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGptReviewParser(BaseCase):
    """F06: IFA-Codes so streng wie GS1."""

    RS, GS = "\x1e", "\x1d"

    def _ifa(self, *parts):
        return "[)>" + self.RS + "06" + self.GS + self.GS.join(parts) + self.RS + "\x04"

    def test_f06_contradicting_serials_rejected(self):
        with self.assertRaisesRegex(gs1.Gs1Error, "widersprüchlich"):
            gs1.parse(self._ifa("9N111234567842", "1TLOT", "D280300", "SFIRST", "SSECOND"))
        with self.assertRaisesRegex(gs1.Gs1Error, "widersprüchlich"):
            gs1.parse(self._ifa("9N111234567842", "1TLOT1", "1TLOT2", "D280300", "SX"))
        # gleicher Wert doppelt ist kein Widerspruch
        r = gs1.parse(self._ifa("9N111234567842", "1TLOT", "D280300", "SX1", "SX1"))
        self.assertEqual(r["serial"], "X1")

    def test_f06_gtin_check_digit(self):
        with self.assertRaisesRegex(gs1.Gs1Error, "GTIN .* ungültig"):
            gs1.parse(self._ifa("8P04150123456789", "1TLOT", "D280300", "SX1"))
        with self.assertRaisesRegex(gs1.Gs1Error, "GTIN .* ungültig"):
            gs1.parse(self._ifa("8P0415012345678A", "1TLOT", "D280300", "SX1"))
        with self.assertRaisesRegex(gs1.Gs1Error, "GTIN .* ungültig"):
            gs1.parse(self._ifa("8P104150123456782", "1TLOT", "D280300", "SX1"))
        r = gs1.parse(self._ifa("8P04150123456782", "1TLOT", "D280300", "SX1"))
        self.assertEqual(r["gtin"], "04150123456782")

    def test_f06_ppn_check(self):
        self.assertTrue(gs1.ppn_check_ok("111234567842"))
        self.assertFalse(gs1.ppn_check_ok("111234567843"))
        self.assertFalse(gs1.ppn_check_ok("11123456784"))
        with self.assertRaisesRegex(gs1.Gs1Error, "PPN .* ungültig"):
            gs1.parse(self._ifa("9N111234567843", "1TLOT", "D280300", "SX1"))
        r = gs1.parse(self._ifa("9N111234567842", "1TLOT", "D280300", "SX1"))
        self.assertEqual(r["pzn"], "12345678")


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGptReviewAtiv(AtivCase):

    def _open_box(self, grx, box_code):
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        return Box.browse(Box.grx_open_box(grx.id, box_code)["id"])

    # F02 über die echte Synchronisierung
    def test_f02_sync_changes_batch_during_capture(self):
        self.sync()
        grx = self.grx("V900")
        grx.with_user(self.lager).grx_start()
        box = self._open_box(grx, "GX131")
        res = box.grx_scan(code(gs1.pzn_to_gtin("12345678"), "SYNC1", "L-77", "280229"))
        self.assertIn(res["status"], ("ok", "warning"), res)
        self.assertEqual(box.slip_match_current, "ok")
        self.later()
        self.fake.touch("helpdesk.ticket", 501, x_studio_batch_number="L-99", name="L-99")
        self.sync()
        self.assertEqual(box.line_id.batch_expected, "L-99")
        self.assertEqual(box.slip_match, "ok")
        self.assertEqual(box.slip_match_current, "batch_differs")
        box.grx_to_check()
        box.grx_save_check(answers={f: "ja" for f in ANSWERS})
        self.assertEqual(box.ampel_suggest, "gelb")

    # F04: bearbeiteter Nachrichtentext
    def test_f04_edited_message_body_updated(self):
        self.sync()
        Message = self.env["plcs.ativ.message"]
        msg = Message.search([("source_key", "like", "%901")])
        self.assertEqual(len(msg), 1)
        self.assertIn("Charge korrigiert", msg.body)
        self.later()
        self.fake.db["mail.message"][901]["body"] = "<p>Charge korrigiert auf L-78</p>"
        self.fake.touch("helpdesk.ticket", 501, description="<p>Retoure 4711 (bearbeitet)</p>")
        self.sync()
        msg.invalidate_recordset()
        self.assertEqual(len(Message.search([("source_key", "like", "%901")])), 1, "kein Duplikat")
        self.assertIn("auf L-78", msg.body)
        # Folgelauf ohne Änderung schreibt nichts
        stamp = msg.write_date
        self.later(2)
        self.fake.touch("helpdesk.ticket", 501, description="<p>Retoure 4711 (2)</p>")
        self.sync()
        msg.invalidate_recordset()
        self.assertEqual(msg.write_date, stamp)

    # F05: Option „Fehlende Artikel anlegen“ gilt auch beim Scan
    def test_f05_option_off_also_on_scan(self):
        self.connector.ativ_create_products = False
        self.prod_brilique.plcs_pzn = "99999997"
        self.sync()
        grx = self.grx("V900")
        self.assertFalse(grx.line_ids.filtered(lambda l: l.pzn == "07560038").product_id)
        grx.with_user(self.lager).grx_start()
        box = self._open_box(grx, "GX132")
        res = box.grx_scan(code(gs1.pzn_to_gtin("07560038"), "OFF1", "L-78", "280229"))
        self.assertEqual(res["status"], "error", res)
        self.assertIn("unbekannt", res["title"])
        self.assertFalse(self.env["product.product"].search([("plcs_pzn", "=", "07560038")]))

    def test_f05_option_on_scan_creates(self):
        """Gegenprobe: Option an (Standard) – Artikel wird beim Scan angelegt, wie in 1.7.4."""
        self.prod_brilique.plcs_pzn = "99999997"
        self.connector.ativ_create_products = False
        self.sync()  # Synchronisierung legt nichts an
        self.connector.ativ_create_products = True
        grx = self.grx("V900")
        self.assertFalse(self.env["product.product"].search([("plcs_pzn", "=", "07560038")]))
        grx.with_user(self.lager).grx_start()
        box = self._open_box(grx, "GX133")
        res = box.grx_scan(code(gs1.pzn_to_gtin("07560038"), "ON1", "L-78", "280229"))
        self.assertIn(res["status"], ("ok", "warning"), res)
        self.assertTrue(self.env["product.product"].search([("plcs_pzn", "=", "07560038")]))

    # F07: Zeitüberschreitung beim Lesen der Statushistorie
    @mute_logger("odoo.addons.plcs_greenrx.models.plcs_ativ_sync")
    def test_f07_timeout_on_tracking_is_retried(self):
        self.fake.fail[("mail.tracking.value", "read")] = AtivError("Zeitüberschreitung", transient=True)
        run = self.sync()
        self.assertTrue(self.connector.tracking_readable, "Timeout schaltet die Historie nicht ab")
        self.assertEqual(run.state, "running")
        del self.fake.fail[("mail.tracking.value", "read")]
        run.queue_ids.filtered(lambda q: q.state == "pending").write(
            {"next_try": fields.Datetime.now() - timedelta(minutes=1)})
        self.connector._ativ_work()
        run.invalidate_recordset()
        self.assertEqual(run.state, "done", run.message)
        msg = self.env["plcs.ativ.message"].search([("source_key", "like", "%902")])
        self.assertIn("Genehmigt", msg.tracking or "")

    @mute_logger("odoo.addons.plcs_greenrx.models.plcs_ativ_sync")
    def test_f07_access_denied_disables_tracking(self):
        """Gegenprobe: fehlende Leserechte (403) schalten die Historie wie vorgesehen ab."""
        self.fake.fail[("mail.tracking.value", "read")] = AtivError("Keine Leserechte", status=403)
        run = self.sync()
        self.assertEqual(run.state, "done", run.message)
        self.assertFalse(self.connector.tracking_readable)


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestGptReviewManualUi(HttpCase):
    """F03: Handeingabe mit verzögerter Serverantwort im Browser."""

    def test_f03_manual_pack_delayed_response(self):
        self.env.company._plcs_grx_setup()
        admin = self.env.ref("base.user_admin")
        admin.group_ids |= self.env.ref("plcs_greenrx.group_grx_user")
        product = self.env["product.product"].create({
            "name": "Verzögerungs-Testartikel", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": gs1.pzn_to_gtin("12345678"), "plcs_pzn": "12345678",
        })
        res = self.env["plcs.greenrx.ticket"].api_upsert([{
            "name": "V-DELAY-1", "partner_name": "Hand-Apotheke",
            "lines": [{"ticket_number": "9700001", "pzn": "12345678", "batch": "MLOT", "qty": 2}],
        }])
        self.assertFalse(res["errors"], res)
        ticket = self.env["plcs.greenrx.ticket"].search([("name", "=", "V-DELAY-1")])
        Box = self.env["plcs.greenrx.box"].with_user(admin)
        box = Box.browse(Box.grx_open_box(ticket.id, "GX134")["id"])

        BoxClass = type(self.env["plcs.greenrx.box"])
        original = BoxClass.grx_scan_manual

        def slow(this, code_, serial, batch, expiry):
            if serial == "SLOW1":
                import time
                time.sleep(2)
            return original(this, code_, serial, batch, expiry)

        with patch.object(BoxClass, "grx_scan_manual", slow):
            self.start_tour("/odoo/action-plcs_greenrx.action_grx_app", "plcs_greenrx_manual_pack_delay",
                            login="admin")

        box.invalidate_recordset()
        self.assertEqual(box.product_id, product)
        self.assertEqual(sorted(box.scan_ids.filtered("accepted").mapped("serial")), ["NEXT2", "SLOW1"])
