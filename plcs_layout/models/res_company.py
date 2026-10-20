from odoo import api, fields, models

PLCS_RED = "#9c0000"
PLCS_GREY = "#828282"


class ResCompany(models.Model):
    _inherit = "res.company"

    plcs_legal_name = fields.Char(
        "Vollständiger Firmenname",
        help="Steht in PLCS-Rot oben in der Fußzeile, z. B. „Pharma-Logistik-Consulting-Services GmbH“. "
             "Leer = Firmenname.")
    plcs_managing_director = fields.Char("Geschäftsführer", help="Name(n) für die Fußzeile.")
    plcs_register_court = fields.Char("Registergericht", help="z. B. „Amtsgericht Saarbrücken“")
    plcs_registry = fields.Char("Handelsregister", help="z. B. „HRB 103353“")
    plcs_tax_number = fields.Char("Steuernummer")
    plcs_legal_note = fields.Text(
        "Hinweis auf allen Belegen",
        help="Erscheint klein über der Fußzeile jedes Belegs, z. B. der Satz zur Großhandelserlaubnis "
             "nach §52a AMG. Wortlaut von PLCS; leer = kein Hinweis.")
    plcs_bank_id = fields.Many2one(
        "res.partner.bank", "Bankverbindung auf Belegen",
        domain="[('partner_id', '=', partner_id)]",
        help="Konto der Firma, das in der Fußzeile steht. Leer = erstes Konto der Firma.")

    def _plcs_footer_bank(self):
        self.ensure_one()
        return self.plcs_bank_id or self.partner_id.bank_ids[:1]

    def _plcs_apply_layout(self):
        layout = self.env.ref("plcs_layout.external_layout_plcs", raise_if_not_found=False)
        paper = self.env.ref("plcs_layout.paperformat_plcs_a4", raise_if_not_found=False)
        for company in self:
            vals = {"external_report_layout_id": layout.id, "font": "Lato"}
            if not company.primary_color:
                vals["primary_color"] = PLCS_RED
            if not company.secondary_color:
                vals["secondary_color"] = PLCS_GREY
            if paper:
                vals["paperformat_id"] = paper.id
            company.write(vals)

    @api.model
    def _plcs_load_demo(self):
        """Dev-Builds (Demodaten): Firmendaten wie auf dem PLCS-Briefpapier, ohne Bank- und Steuerdaten.

        IBAN, Steuernummer und USt-IdNr. stehen nicht im Code; sie werden in Odoo eingetragen.
        """
        from odoo.tools.binary import BinaryBytes
        from odoo.tools.misc import file_open
        company = self.env.company
        with file_open("plcs_layout/static/img/plcs_logo.png", "rb") as fh:
            logo = BinaryBytes(fh.read())
        company.write({
            "name": "PLCS GmbH",
            "plcs_legal_name": "Pharma-Logistik-Consulting-Services GmbH",
            "street": "Im Schachen 2/208",
            "zip": "66687",
            "city": "Wadern",
            "phone": "+49 (0)6424 801344",
            "email": "info@plcs-gmbh.de",
            "plcs_managing_director": "Tobias Krebs",
            "plcs_register_court": "Amtsgericht Saarbrücken",
            "plcs_registry": "HRB 103353",
            "logo": logo,
        })
        company._plcs_apply_layout()
        if "website" in self.env:
            self.env["website"].search([("company_id", "=", company.id)]).write({"logo": logo})
