import base64
import hashlib

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..tools import gs1
from . import test_greenrx_flow as flow

DENIED = (AccessError, UserError)


@tagged("post_install", "-at_install", "plcs_greenrx")
class TestFormblattPdf(TransactionCase):
    """1.9.0: Formblatt-PDF je Druck an der Belegung, Archivierung bei leerer Box.

    Jede Regel mit Negativtest: Fehlversuche müssen scheitern.
    """

    _ticket = flow.TestGreenrxFlow._ticket
    _all_yes = flow.TestGreenrxFlow._all_yes
    _fill_box = flow.TestGreenrxFlow._fill_box
    _close = flow.TestGreenrxFlow._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company._plcs_grx_setup()
        cls.lager = new_test_user(cls.env, login="fb_lager", name="Lena Lager",
                                  groups="plcs_greenrx.group_grx_user")
        cls.vp1 = new_test_user(cls.env, login="fb_vp1", name="Vera VP", groups="plcs_greenrx.group_grx_vp")
        cls.vp2 = new_test_user(cls.env, login="fb_vp2", name="Viktor VP", groups="plcs_greenrx.group_grx_vp")
        cls.stock_only = new_test_user(cls.env, login="fb_stock", groups="stock.group_stock_user")
        cls.manager = new_test_user(cls.env, login="fb_mgr", groups="stock.group_stock_manager,base.group_system,"
                                                                     "plcs_greenrx.group_grx_vp")
        cls.gtin_a = gs1.pzn_to_gtin("12345678")
        cls.prod_a = cls.env["product.product"].create({
            "name": "BYETTA", "is_storable": True, "tracking": "lot", "use_expiration_date": True,
            "barcode": cls.gtin_a, "plcs_pzn": "12345678",
        })
        cls.env["res.partner"].create({"name": "Linden-Apotheke", "ref": "K-10234"})
        cls.customer = cls.env["res.partner"].create({"name": "Blistercenter Köln"})
        cls.exp_ok = (fields.Date.today() + relativedelta(years=2)).strftime("%y%m00")

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------
    def _closed_box(self, name, box_code, serials):
        ticket = self._ticket(name=name, lines=[{"pzn": "12345678", "qty": len(serials)}])
        box = self._fill_box(ticket, box_code, self.gtin_a, serials)
        box.grx_to_check()
        self._close(box)
        return ticket, box

    def _released_box(self, name, box_code, serials):
        ticket, box = self._closed_box(name, box_code, serials)
        ticket.with_user(self.lager).grx_finish()
        box.with_user(self.vp1).grx_vp_release()
        self.assertEqual(box.state, "released")
        return ticket, box

    def _ship(self, box, serials):
        """Packungen aus GreenRX/Bestand an einen Kunden liefern (Verkauf)."""
        stock_loc = self.company.plcs_grx_stock_location_id
        customer_loc = self.env.ref("stock.stock_location_customers")
        out_type = self.env["stock.warehouse"].search([("company_id", "=", self.company.id)], limit=1).out_type_id
        lots = box.lot_ids.filtered(lambda l: l.name in serials)
        self.assertEqual(len(lots), len(serials))
        picking = self.env["stock.picking"].create({
            "picking_type_id": out_type.id, "partner_id": self.customer.id,
            "location_id": stock_loc.id, "location_dest_id": customer_loc.id,
        })
        self.env["stock.move"].create({
            "product_id": self.prod_a.id, "product_uom_qty": len(lots), "uom_id": self.prod_a.uom_id.id,
            "picking_id": picking.id, "location_id": stock_loc.id, "location_dest_id": customer_loc.id,
            "move_line_ids": [(0, 0, {
                "product_id": self.prod_a.id, "lot_id": lot.id, "quantity": 1, "uom_id": self.prod_a.uom_id.id,
                "location_id": stock_loc.id, "location_dest_id": customer_loc.id,
                "package_id": box.package_id.id, "picking_id": picking.id,
            }) for lot in lots],
        })
        picking.action_confirm()
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True, cancel_backorder=True).button_validate()
        self.assertEqual(picking.state, "done")
        return picking

    def _content(self, doc):
        return doc.sudo().pdf_file.content.decode()

    # ------------------------------------------------------------------
    # Druck → PDF
    # ------------------------------------------------------------------
    def test_print_stores_pdf_with_stamp(self):
        ticket = self._ticket(name="VTEST1801", lines=[{"pzn": "12345678", "qty": 2}])
        box = self._fill_box(ticket, "GX181", self.gtin_a, ["F1", "F2"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        action = box.grx_print()
        docs = box.sudo().document_ids
        self.assertEqual(len(docs), 1)
        doc = docs
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["url"], doc._grx_url())
        self.assertEqual(doc.print_no, 1)
        self.assertFalse(doc.reprint)
        self.assertEqual(doc.state, "open")
        self.assertEqual(doc.printed_by, self.lager)
        self.assertEqual(doc.printed_by_name, "Lena Lager")
        self.assertEqual(doc.printed_at, box.printed_at)
        # Name: Box_Vorgang_Datum_Uhrzeit_DruckN (im Test HTML statt PDF, da ohne wkhtmltopdf-Server)
        local = fields.Datetime.context_timestamp(box, box.printed_at).strftime("%Y-%m-%d_%H%M")
        self.assertTrue(doc.file_name.startswith("GX181_VTEST1801_%s_Druck1." % local), doc.file_name)
        html = self._content(doc)
        self.assertIn("Checkliste Warenannahme", html)
        self.assertIn("Druck Nr. 1 ·", html)
        self.assertIn("gedruckt von Lena Lager", html)
        self.assertNotIn("Nachdruck", html)
        self.assertNotIn("Ansicht – kein Druck", html)
        self.assertEqual(doc.checksum, hashlib.sha256(html.encode()).hexdigest())
        self.assertIn(doc.file_name, box.message_ids[0].body)

    def test_preview_has_no_valid_stamp(self):
        """/report/pdf ohne Druck zeigt „Ansicht – kein Druck“; ein Kontextwert per RPC täuscht keinen Druck vor."""
        _ticket, box = self._closed_box("VTEST1802", "GX182", ["P1"])
        html = self.env["ir.actions.report"].with_context(plcs_grx_print_stamp={"print_no": 9})._render_qweb_html(
            "plcs_greenrx.action_report_grx_box_documents", box.ids)[0].decode()
        self.assertIn("Ansicht – kein Druck", html)
        self.assertNotIn("Druck Nr. 9", html)

    def test_reprint_is_saved_and_marked(self):
        ticket = self._ticket(name="VTEST1803", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX183", self.gtin_a, ["R1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        first = box.sudo().document_ids
        first_sum = first.checksum
        box.grx_print()
        docs = box.sudo().document_ids
        self.assertEqual(len(docs), 2)
        second = docs - first
        self.assertTrue(second.reprint)
        self.assertEqual(second.print_no, 2)
        self.assertIn("Druck Nr. 2 (Nachdruck)", self._content(second))
        self.assertEqual(first.checksum, first_sum, "Der erste Druck bleibt unverändert")
        # Nachdruck nach Abschluss: weitere Kopie, gültige Fassung bleibt
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(second.state, "valid")
        self.assertEqual(first.state, "replaced")
        box.grx_print()
        third = box.sudo().document_ids - first - second
        self.assertEqual(third.state, "copy")
        self.assertTrue(third.after_close)
        self.assertEqual(box.valid_document_id, second)
        self.env.invalidate_all()
        self.assertEqual(box.sudo().document_ids[:1], second, "Gültiges Formblatt steht oben")

    def test_change_after_print_only_last_is_valid(self):
        ticket = self._ticket(name="VTEST1804", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX184", self.gtin_a, ["C1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        old = box.sudo().document_ids
        box.grx_back()
        self._all_yes(box, q_damage="nein")
        box.grx_save_check(ampel="gelb")
        box.grx_to_print()
        box.grx_print()
        new = box.sudo().document_ids - old
        self.assertGreater(new.content_rev, old.content_rev)
        box.grx_confirm_signed(True)
        box.grx_close()
        self.assertEqual(new.state, "valid")
        self.assertEqual(old.state, "replaced")
        self.assertEqual(box.valid_document_id, new)
        self.assertIn("GELB", self._content(new))

    def test_cancel_voids_open_documents(self):
        ticket = self._ticket(name="VTEST1805", lines=[{"pzn": "12345678", "qty": 1}])
        box = self._fill_box(ticket, "GX185", self.gtin_a, ["X1"])
        box.grx_to_check()
        self._all_yes(box)
        box.grx_save_check(ampel="gruen")
        box.grx_to_print()
        box.grx_print()
        box.grx_cancel()
        self.assertEqual(box.sudo().document_ids.state, "void")
        self.assertFalse(box.valid_document_id)

    # ------------------------------------------------------------------
    # Nicht löschbar, nicht änderbar, Zugriff
    # ------------------------------------------------------------------
    def test_documents_cannot_be_deleted_or_changed(self):
        _ticket, box = self._closed_box("VTEST1806", "GX186", ["D1"])
        doc = box.sudo().document_ids
        att = self.env["ir.attachment"].sudo().search(
            [("res_model", "=", doc._name), ("res_id", "=", doc.id), ("res_field", "=", "pdf_file")])
        self.assertEqual(len(att), 1)
        for user in (self.lager, self.vp1, self.vp2, self.manager):
            with self.subTest(user=user.login):
                with self.assertRaises(DENIED):
                    doc.with_user(user).unlink()
                with self.assertRaises(DENIED):
                    doc.with_user(user).write({"file_name": "x.pdf"})
                with self.assertRaises(DENIED):
                    doc.with_user(user).write({"pdf_file": base64.b64encode(b"%PDF-1.4 fake").decode()})
                with self.assertRaises(DENIED):
                    att.with_user(user).unlink()
                with self.assertRaises(DENIED):
                    att.with_user(user).write({"raw": b"fake"})
                with self.assertRaises(DENIED):
                    self.env["plcs.greenrx.box.document"].with_user(user).create({
                        "box_id": box.id, "print_no": 9, "printed_at": fields.Datetime.now(),
                        "printed_by": user.id, "file_name": "fake.pdf"})
                with self.assertRaises(DENIED):
                    self.env["ir.attachment"].with_user(user).create({
                        "name": "fake.pdf", "res_model": doc._name, "res_id": doc.id, "raw": b"fake"})
        # Auch mit Superuser-Rechten: Inhalt fest, nur der Status darf weiterschalten
        with self.assertRaises(UserError):
            doc.sudo().write({"pdf_file": base64.b64encode(b"fake").decode()})
        with self.assertRaises(UserError):
            doc.sudo().unlink()
        with self.assertRaises(UserError):
            att.sudo().unlink()
        with self.assertRaises(UserError):
            doc.sudo().write({"state": "open"})  # gültig → zurück geht nicht
        with self.assertRaises(UserError):
            box.sudo().unlink()
        self.assertTrue(doc.exists())
        self.assertEqual(doc.state, "valid")

    def test_access_only_own_company_and_greenrx_role(self):
        _ticket, box = self._closed_box("VTEST1807", "GX187", ["A1"])
        doc = box.sudo().document_ids
        self.assertEqual(doc.with_user(self.lager).file_name, doc.file_name)
        self.assertEqual(doc.with_user(self.vp1).file_name, doc.file_name)
        with self.assertRaises(AccessError):
            doc.with_user(self.stock_only).check_access("read")
        with self.assertRaises(AccessError):
            box.with_user(self.stock_only).grx_document_url()
        other = self.env["res.company"].create({"name": "Fremdfirma GmbH"})
        foreign = new_test_user(self.env, login="fb_foreign", groups="plcs_greenrx.group_grx_vp",
                                company_id=other.id, company_ids=[(6, 0, [other.id])])
        with self.assertRaises(AccessError):
            doc.with_user(foreign).check_access("read")
        with self.assertRaises(AccessError):
            doc.with_user(foreign).read(["pdf_file"])
        att = self.env["ir.attachment"].sudo().search(
            [("res_model", "=", doc._name), ("res_id", "=", doc.id), ("res_field", "=", "pdf_file")])
        self.assertEqual(len(att), 1)
        with self.assertRaises(AccessError):
            att.with_user(foreign).read(["raw"])
        with self.assertRaises(AccessError):
            att.with_user(self.stock_only).read(["raw"])

    # ------------------------------------------------------------------
    # Archivierung
    # ------------------------------------------------------------------
    def test_partial_sale_keeps_active_last_pack_archives(self):
        _ticket, box = self._released_box("VTEST1808", "GX188", ["S1", "S2"])
        self._ship(box, ["S1"])
        self.assertFalse(box.archived, "Teilverkauf: Belegung bleibt aktiv")
        picking = self._ship(box, ["S2"])
        self.assertTrue(box.archived)
        self.assertTrue(box.archived_at)
        self.assertIn("Ausgeliefert", box.archive_reason)
        self.assertIn(picking.name, box.archive_reason)
        self.assertIn("Blistercenter Köln", box.archive_reason)
        # Formblatt, Verlauf, Prüfung und VP-Entscheidung bleiben lesbar
        self.assertTrue(box.valid_document_id)
        self.assertEqual(box.vp_user_id, self.vp1)
        self.assertEqual(box.ampel, "gruen")
        self.assertEqual(box.q_seal, "ja")
        # Je Packung sichtbar, wohin sie gegangen ist
        for lot in box.lot_ids:
            self.assertIn("Ausgeliefert", lot.plcs_grx_whereabouts)
            self.assertIn("Blistercenter Köln", lot.plcs_grx_whereabouts)

    def test_archived_box_is_read_only(self):
        _ticket, box = self._released_box("VTEST1809", "GX189", ["W1"])
        self._ship(box, ["W1"])
        self.assertTrue(box.archived)
        doc = box.valid_document_id
        with self.assertRaises(UserError):
            box.sudo().write({"note": "geändert"})
        with self.assertRaises(UserError):
            box.sudo().write({"archived": False})
        with self.assertRaises(UserError):
            box.sudo().unlink()
        with self.assertRaises(UserError):
            box.with_user(self.vp2).grx_print()
        with self.assertRaises(UserError):
            box.with_user(self.vp2).grx_vp_block("Test")
        with self.assertRaises(UserError):
            doc.sudo().write({"state": "replaced"})
        with self.assertRaises(UserError):
            doc.sudo().unlink()
        # Ein Kontextwert per RPC hebt den Schutz nicht auf
        with self.assertRaises(UserError):
            box.sudo().with_context(plcs_grx_archive={"box_ids": box.ids}).write({"note": "x"})
        self.assertTrue(box.archived)
        self.assertFalse(box.note)

    def test_search_by_serial_finds_archived_and_valid_pdf(self):
        _ticket, box = self._released_box("VTEST1810", "GX190", ["Q1"])
        self._ship(box, ["Q1"])
        lot = self.env["stock.lot"].with_user(self.lager).search(
            [("name", "=", "Q1"), ("plcs_is_grx_serial", "=", True)])
        self.assertEqual(len(lot), 1)
        self.assertEqual(lot.plcs_grx_box_id, box)
        self.assertTrue(lot.plcs_grx_archived)
        self.assertEqual(lot.plcs_grx_document_id, box.valid_document_id)
        # Von der Seriennummer zum gültigen PDF: ein Klick (Knopf „Formblatt“)
        action = lot.action_plcs_grx_open_document()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["url"], box.valid_document_id._grx_url())
        self.assertEqual(lot.action_plcs_grx_open_box()["res_id"], box.id)
        # Box-Liste: Standardfilter „Aktiv“ blendet aus, Filter „Archiv“ und Suche nach SN finden sie
        Box = self.env["plcs.greenrx.box"].with_user(self.lager)
        self.assertFalse(Box.search([("archived", "=", False), ("lot_ids", "=", "Q1")]))
        self.assertEqual(Box.search([("archived", "=", True), ("lot_ids", "=", "Q1")]), box)
        self.assertEqual(Box.search([("batch", "=", "BYE-T01"), ("archived", "=", True)]) & box, box)

    def test_stock_to_pdf_from_quant(self):
        """Bestand GreenRX: Knopf „Formblatt“ je Packung öffnet das gültige PDF."""
        _ticket, box = self._released_box("VTEST1811", "GX191", ["B1"])
        quant = self.env["stock.quant"].search([("lot_id", "in", box.lot_ids.ids), ("quantity", ">", 0)])
        self.assertEqual(len(quant), 1)
        action = quant.with_user(self.lager).action_plcs_grx_open_document()
        self.assertEqual(action["url"], box.valid_document_id._grx_url())
        self.assertIn("Im Lager", box.lot_ids.plcs_grx_whereabouts)

    def test_box_reuse_new_assignment_has_own_documents(self):
        _ticket, old = self._released_box("VTEST1812", "GX192", ["U1"])
        self._ship(old, ["U1"])
        self.assertTrue(old.archived)
        old_docs = old.sudo().document_ids
        ticket2 = self._ticket(name="VTEST1813", lines=[{"pzn": "12345678", "qty": 1}])
        new = self._fill_box(ticket2, "GX192", self.gtin_a, ["U2"])
        self.assertNotEqual(new, old)
        self.assertEqual(new.package_id, old.package_id)
        self.assertFalse(new.sudo().document_ids, "Alte Formblätter erscheinen nicht in der neuen Belegung")
        new.grx_to_check()
        self._close(new)
        self.assertEqual(len(new.sudo().document_ids), 1)
        self.assertEqual(old.sudo().document_ids, old_docs)
        self.assertNotEqual(new.valid_document_id, old.valid_document_id)

    def test_reuse_without_archive_fails(self):
        """Box physisch leer (Packungen ins Regal umgepackt), Belegung aber nicht archiviert → keine Neubelegung."""
        _ticket, box = self._released_box("VTEST1814", "GX193", ["N1"])
        stock_loc = self.company.plcs_grx_stock_location_id
        shelf = self.env["stock.location"].create({"name": "Regal 1", "location_id": stock_loc.id})
        quant = self.env["stock.quant"].search([("lot_id", "in", box.lot_ids.ids), ("quantity", ">", 0)])
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.company.plcs_grx_release_type_id.id,
            "location_id": stock_loc.id, "location_dest_id": shelf.id,
        })
        self.env["stock.move"].create({
            "product_id": self.prod_a.id, "product_uom_qty": 1, "uom_id": self.prod_a.uom_id.id,
            "picking_id": picking.id, "location_id": stock_loc.id, "location_dest_id": shelf.id,
            "move_line_ids": [(0, 0, {
                "product_id": self.prod_a.id, "lot_id": quant.lot_id.id, "quantity": 1,
                "uom_id": self.prod_a.uom_id.id, "location_id": stock_loc.id, "location_dest_id": shelf.id,
                "package_id": box.package_id.id, "result_package_id": False, "picking_id": picking.id})],
        })
        picking.action_confirm()
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        self.assertFalse(box.archived, "Packung liegt noch im Lager")
        ticket2 = self._ticket(name="VTEST1815", lines=[{"pzn": "12345678", "qty": 1}])
        with self.assertRaisesRegex(UserError, "nicht archiviert"):
            self.env["plcs.greenrx.box"].with_user(self.lager).grx_open_box(ticket2.id, "GX193")

    def test_legacy_box_without_stock_archived_on_reuse(self):
        """Belegung von vor 1.9.0 ohne Formblatt und ohne Bestand: wird bei Neubelegung archiviert, kein PDF erfunden."""
        _ticket, box = self._released_box("VTEST1816", "GX194", ["L1"])
        box.sudo().document_ids.with_context(force_delete=True).unlink()  # Stand vor 1.9.0 nachbilden
        self.env.cr.execute("UPDATE plcs_greenrx_box SET archived = false WHERE id = %s", [box.id])
        stock_loc = self.company.plcs_grx_stock_location_id
        # Bestand direkt ausbuchen (wie vor 1.9.0, ohne dass archiviert wurde)
        self.env.cr.execute("DELETE FROM stock_quant WHERE lot_id IN %s", [tuple(box.lot_ids.ids)])
        self.env.invalidate_all()
        self.assertFalse(box.archived)
        self.assertTrue(stock_loc)
        ticket2 = self._ticket(name="VTEST1817", lines=[{"pzn": "12345678", "qty": 1}])
        self._fill_box(ticket2, "GX194", self.gtin_a, ["L2"])
        self.assertTrue(box.archived)
        self.assertFalse(box.sudo().document_ids)
        with self.assertRaises(UserError):
            box.action_grx_open_valid_document()
