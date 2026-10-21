"""Blistercenter 1.5.0 (Entscheidungen 21.10.2026): Proforma bei Bestätigung, Zahlungsziel ab Proforma,
Umsatzsteuer Inland 19 % / Export 0 %, Packungsfotos per PZN. Jede Regel mit Negativtest."""
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from .test_blistercenter import BlistercenterCommon

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (156, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


PNG_1PX = _png()


class FakeResponse:
    def __init__(self, status, content):
        self.status_code = status
        self.content = content


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterProforma(BlistercenterCommon):

    def _proforma_messages(self, order):
        template = self.env.ref("blistercenter.mail_template_bc_proforma")
        return order.message_ids.filtered(
            lambda m: m.message_type == "comment" and template.subject.split()[0] in (m.subject or ""))

    def test_confirm_sends_proforma_with_due_date(self):
        self.customer_invoice.email = "einkauf@blister.example"
        order = self._order(self.customer_invoice)
        today = fields.Date.context_today(order)
        self.assertEqual(order.bc_proforma_date, today)
        self.assertEqual(order.bc_proforma_due_date, today + timedelta(days=7), "Zahlungsziel 7 Tage ab Proforma")
        messages = self._proforma_messages(order)
        self.assertEqual(len(messages), 1, "Proforma geht bei Bestätigung per E-Mail an den Kunden")
        self.assertIn(self.customer_invoice.commercial_partner_id, messages.partner_ids | messages.notified_partner_ids)
        self.assertTrue(messages.attachment_ids, "Proforma als Anhang")
        self.assertIn("Proforma", messages.attachment_ids.name)
        self.assertIn("Zahlbar bis", str(messages.body))
        # Erneut senden über den Knopf; Datum und Fälligkeit bleiben
        order.with_user(self.user_bc).action_bc_send_proforma()
        self.assertEqual(len(self._proforma_messages(order)), 2)
        self.assertEqual(order.bc_proforma_date, today)

    def test_proforma_report_text(self):
        order = self._order(self.customer_debit)
        report = self.env.ref("blistercenter.action_report_bc_proforma")
        html = self.env["ir.actions.report"]._render_qweb_html(report, order.ids)[0].decode()
        self.assertIn("Proforma – keine Rechnung", html)
        self.assertIn("Bitte zahlen Sie den Betrag bis zum", html)
        self.assertIn("SEPA-Lastschrift", html)
        self.assertNotIn("Bitte nicht bezahlen", html, "Proforma ist jetzt die Zahlungsaufforderung")

    def test_no_email_no_mail_but_note(self):
        self.customer_debit.email = False
        order = self._order(self.customer_debit)
        self.assertTrue(order.bc_proforma_due_date)
        self.assertFalse(self._proforma_messages(order))
        self.assertTrue(order.message_ids.filtered(lambda m: "Proforma nicht per E-Mail versendet" in str(m.body)))

    def test_switch_off_and_non_bc_orders(self):
        self.customer_invoice.email = "einkauf@blister.example"
        self.config.rule_send_proforma = False
        order = self._order(self.customer_invoice)
        self.assertEqual(order.bc_proforma_due_date, order.bc_proforma_date + timedelta(days=7),
                         "Fälligkeit auch ohne E-Mail")
        self.assertFalse(self._proforma_messages(order))
        other = self._order(self.customer_other)
        self.assertFalse(other.bc_proforma_date, "Nur Blistercenter-Bestellungen")
        with self.assertRaises(UserError):
            other.action_bc_send_proforma()
        draft = self._order(self.customer_invoice, confirm=False)
        with self.assertRaises(UserError):
            draft.action_bc_send_proforma()

    def test_untaxed_goods_warning(self):
        order = self._order(self.customer_invoice, confirm=False)
        self.assertIn(self.product.display_name, order.bc_untaxed_products)
        tax = self.env["account.tax"].create({"name": "19% Test", "amount": 19.0, "type_tax_use": "sale"})
        order.order_line.tax_ids = tax
        self.assertFalse(order.bc_untaxed_products)
        self.assertFalse(self._order(self.customer_other, confirm=False).bc_untaxed_products, "Nur Blistercenter")


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterTaxes(BlistercenterCommon):

    def _fpos(self, country_code, vat=False):
        partner = self.env["res.partner"].create({
            "name": "Kunde %s" % country_code, "is_company": True,
            "country_id": self.env.ref("base.%s" % country_code.lower()).id, "vat": vat})
        return self.env["account.fiscal.position"]._get_fiscal_position(partner)

    def test_demo_tax_setup_domestic_19_export_0(self):
        Config = self.env["blistercenter.config"]
        if self.company.chart_template and self.company.chart_template.startswith("de_skr"):
            self.skipTest("deutscher Kontenrahmen: Steuern aus l10n_de")
        ust19 = Config._bc_demo_taxes()
        self.assertEqual(ust19.amount, 19.0)
        self.assertEqual(ust19.tax_group_id.name, "USt 19 %", "Summenzeile zeigt 19 %, nicht die Demogruppe")
        self.assertEqual(self.company.account_sale_tax_id, ust19)
        self.assertEqual(Config._bc_demo_taxes(), ust19, "zweiter Aufruf legt nichts doppelt an")
        self.assertEqual(self.env["account.tax"].search_count([("name", "=", "19% USt"),
                                                               ("company_id", "=", self.company.id)]), 1)
        inland = self._fpos("DE")
        self.assertEqual(inland.map_tax(ust19), ust19, "Inland 19 %")
        export = self._fpos("CH")
        self.assertTrue(export, "Drittland automatisch erkannt")
        self.assertEqual(export.map_tax(ust19).amount, 0.0, "Ausfuhr 0 %")
        eu_without = self._fpos("AT")
        self.assertEqual(eu_without.map_tax(ust19), ust19, "EU ohne USt-IdNr. bleibt 19 %")
        # Auftrag an einen Kunden in der Schweiz: 0 % in der Position
        swiss = self.env["res.partner"].create({
            "name": "Blistercenter Basel", "is_company": True, "bc_is_customer": True,
            "country_id": self.env.ref("base.ch").id, "property_payment_term_id": self.term_14.id})
        self.product.taxes_id = ust19
        order = self._order(swiss, confirm=False)
        self.assertEqual(order.fiscal_position_id, export)
        self.assertEqual(order.order_line.tax_ids.amount, 0.0)

    def test_skr_positions_auto_apply(self):
        """Mit deutschem Kontenrahmen nur die Schalter setzen (Odoo-Steuern bleiben)."""
        Config = self.env["blistercenter.config"]
        if not (self.company.chart_template or "").startswith("de_skr"):
            self.assertFalse(Config._bc_setup_tax_positions(self.company), "ohne SKR nichts ändern")
            return
        self.assertTrue(Config._bc_setup_tax_positions(self.company))


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterProductImages(BlistercenterCommon):

    def setUp(self):
        super().setUp()
        self.tmpl = self.product.product_tmpl_id
        self.tmpl.write({"default_code": "10330224", "image_1920": False})

    def test_fetch_by_pzn(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            return FakeResponse(200, PNG_1PX)
        with patch.object(requests, "get", side_effect=fake_get):
            done, missing = self.env["blistercenter.config"]._bc_fetch_product_images(self.tmpl)
        self.assertEqual(done, self.tmpl)
        self.assertFalse(missing)
        self.assertTrue(self.tmpl.image_1920)
        self.assertEqual(calls, ["https://cdn.shop-apotheke.com/images/600x0/D10/330/224/D10330224-p10.jpg"])
        # Vorhandenes Foto bleibt (kein zweiter Abruf)
        with patch.object(requests, "get", side_effect=fake_get):
            done, missing = self.env["blistercenter.config"]._bc_fetch_product_images(self.tmpl)
        self.assertFalse(done)
        self.assertEqual(len(calls), 1)

    def test_rejects_non_images_and_bad_pzn(self):
        Config = self.env["blistercenter.config"]
        for response in (FakeResponse(200, b"<html>kein Bild</html>"), FakeResponse(404, JPEG),
                         FakeResponse(200, JPEG + b"\x00" * (5 * 1024 * 1024))):
            with patch.object(requests, "get", return_value=response):
                done, missing = Config._bc_fetch_product_images(self.tmpl)
            self.assertFalse(done)
            self.assertEqual(missing, [self.tmpl.display_name])
            self.assertFalse(self.tmpl.image_1920)
        self.tmpl.default_code = "ABC"
        if "plcs_pzn" in self.tmpl._fields:
            self.tmpl.plcs_pzn = False
        with patch.object(requests, "get", side_effect=AssertionError("kein Abruf ohne PZN")):
            done, missing = Config._bc_fetch_product_images(self.tmpl)
        self.assertEqual(missing, [self.tmpl.display_name])

    def test_no_connection_stops(self):
        other = self.tmpl.copy({"default_code": "10330193", "image_1920": False})
        with patch.object(requests, "get", side_effect=requests.ConnectionError("offline")) as mocked:
            done, missing = self.env["blistercenter.config"]._bc_fetch_product_images(self.tmpl | other)
        self.assertEqual(mocked.call_count, 1, "nach dem ersten Verbindungsfehler abbrechen")
        self.assertEqual(len(missing), 2)

    def test_button_only_for_manager(self):
        with self.assertRaises(AccessError):
            self.config.with_user(self.user_bc).action_load_product_images()
        with patch.object(requests, "get", return_value=FakeResponse(200, PNG_1PX)):
            action = self.config.with_user(self.user_manager).action_load_product_images()
        self.assertEqual(action["params"]["type"], "success")
        self.assertTrue(self.tmpl.image_1920)


@tagged("post_install", "-at_install", "blistercenter")
class TestBlistercenterPortalProforma(HttpCase):

    def test_portal_pdf_is_proforma(self):
        env = self.env
        term = env.ref("blistercenter.payment_term_14days")
        customer = env["res.partner"].create({
            "name": "Blistercenter Portal Proforma", "is_company": True, "bc_is_customer": True,
            "bc_shop_enabled": True, "property_payment_term_id": term.id, "email": "portal@blister.example"})
        product = env["product.product"].create({
            "name": "Portal-Testartikel", "type": "consu", "is_storable": True, "tracking": "lot",
            "use_expiration_date": True, "invoice_policy": "delivery", "list_price": 10.0,
            "taxes_id": [Command.clear()], "bc_shop_product": True})
        env["blistercenter.config"]._get()._bc_check_setup()
        order = env["sale.order"].create({
            "partner_id": customer.id, "bc_channel": "phone", "bc_orderer_name": "Herr Test",
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": 1})]})
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        url = "/my/orders/%s?report_type=html&access_token=%s" % (order.id, order._portal_ensure_token())
        env.flush_all()
        body = self.url_open(url).content.decode()
        self.assertIn("Proforma – keine Rechnung", body, "Portal zeigt die Proforma statt des Auftrags")
        other = env["sale.order"].create({
            "partner_id": env["res.partner"].create({"name": "Normaler Kunde"}).id,
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": 1})]})
        other.action_confirm()
        env.flush_all()
        body = self.url_open("/my/orders/%s?report_type=html&access_token=%s" % (
            other.id, other._portal_ensure_token())).content.decode()
        self.assertNotIn("Proforma – keine Rechnung", body)
