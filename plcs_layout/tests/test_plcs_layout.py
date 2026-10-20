"""PLCS-Briefpapier: Layout ist Standard, Fußzeile aus den Firmendaten, kein Fixtext im Code."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "plcs_layout")
class TestPlcsLayout(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({
            "name": "PLCS Test GmbH", "plcs_legal_name": "Pharma-Logistik Testname GmbH",
            "street": "Teststraße 1", "zip": "12345", "city": "Testheim",
            "phone": "+49 1 2", "email": "info@test.example", "plcs_managing_director": "Erika Test",
            "plcs_register_court": "Amtsgericht Testheim", "plcs_registry": "HRB 1",
            "plcs_tax_number": "111/222/333", "plcs_legal_note": "Großhandelserlaubnis Testtext",
        })
        cls.company._plcs_apply_layout()
        cls.partner = cls.env["res.partner"].create({
            "name": "Empfänger Test", "street": "Empfängerweg 9", "zip": "50667", "city": "Köln"})

    def _render(self):
        """Ein Beleg mit Adresse (Partner-Bericht über das externe Layout)."""
        html = self.env["ir.qweb"]._render("web.external_layout", {
            "o": self.partner, "doc": self.partner, "company": self.company, "res_company": self.company,
            "report_type": "html", "address": self.partner.name,
            "layout_document_title": "Testbeleg", "is_html_empty": lambda v: not v,
        })
        import re
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html)))

    def _raw(self):
        return str(self.env["ir.qweb"]._render("web.external_layout", {
            "o": self.partner, "doc": self.partner, "company": self.company, "res_company": self.company,
            "report_type": "html", "address": self.partner.name,
            "layout_document_title": "Testbeleg", "is_html_empty": lambda v: not v,
        }))

    def test_layout_is_company_default(self):
        layout = self.env.ref("plcs_layout.external_layout_plcs")
        self.assertEqual(self.company.external_report_layout_id, layout)
        self.assertEqual(self.company.paperformat_id, self.env.ref("plcs_layout.paperformat_plcs_a4"))
        self.assertTrue(self.env["report.layout"].search([("view_id", "=", layout.id)]))

    def test_footer_from_company_data(self):
        html = self._render()
        for text in ("Pharma-Logistik Testname GmbH", "Teststraße 1", "12345 Testheim",
                     "Geschäftsführer: Erika Test", "Fon: +49 1 2", "E-Mail: info@test.example",
                     "Amtsgericht Testheim HRB 1", "Steuernummer: 111/222/333",
                     "Großhandelserlaubnis Testtext"):
            self.assertIn(text, html)
        # Absenderzeile über der Adresse
        raw = self._raw()
        self.assertIn("o_plcs_sender", raw)
        self.assertLess(raw.index("o_plcs_sender"), raw.index("Empfänger Test"))

    def test_bank_only_when_present(self):
        self.assertNotIn("Bankverbindung", self._render())
        bank = self.env["res.partner.bank"].create({
            "partner_id": self.company.partner_id.id, "account_number": "DE89370400440532013000"})
        self.company.plcs_bank_id = bank
        html = self._render()
        self.assertIn("Bankverbindung", html)
        self.assertIn("IBAN: DE89 3704 0044 0532 0130 00", html)

    def test_empty_fields_print_nothing(self):
        """Leere Angaben erzeugen keine leeren Beschriftungen."""
        self.company.write({"plcs_tax_number": False, "plcs_legal_note": False, "plcs_managing_director": False})
        html = self._render()
        self.assertNotIn("Steuernummer", html)
        self.assertNotIn("Geschäftsführer", html)
        self.assertNotIn("o_plcs_note", self._raw())
