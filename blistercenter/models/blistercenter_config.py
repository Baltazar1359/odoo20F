from odoo import api, fields, models
from odoo.exceptions import AccessError

PAYMENT_MODELS = [
    ("prepayment", "Vorkasse"),
    ("invoice", "Rechnung mit Zahlungsziel"),
]


class BlistercenterConfig(models.Model):
    """Einstellungen und Prozessregeln der App Blistercenter, ein Datensatz je Firma.

    Bewusst ein eigenes Modell statt res.config.settings: Lesen dürfen alle
    Blistercenter-Benutzer (die Regeln wirken in ihren Abläufen), ändern nur
    die Rolle „Administrator“ der App – ohne Odoo-Administratorrechte.
    """

    _name = "blistercenter.config"
    _description = "Blistercenter-Einstellungen"
    _inherit = ["mail.thread"]
    _rec_name = "company_id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", string="Firma", required=True, readonly=True,
        default=lambda self: self.env.company, ondelete="cascade",
    )

    # Zuständigkeiten
    sales_user_id = fields.Many2one(
        "res.users", string="Zuständig Kundenbestellungen", tracking=True,
        help="Erhält die Aufgabe zu neuen Bestellungen von Blistercentern.",
    )
    purchase_user_id = fields.Many2one(
        "res.users", string="Zuständig Sammelbestellungen", tracking=True,
        help="Standard-Verantwortlicher neuer Sammelbestellungen; erhält die Aufgabe, die Anfrage beim Hersteller zu bestellen.",
    )
    shipping_user_id = fields.Many2one(
        "res.users", string="Zuständig Versand", tracking=True,
    )

    # Benachrichtigungen (als Aufgaben in Odoo, kein E-Mail-Versand)
    notify_new_order = fields.Boolean(
        "Aufgabe bei neuer Kundenbestellung", default=True, tracking=True,
        help="Bei Bestätigung einer Blistercenter-Bestellung bekommt der Zuständige eine Aufgabe.",
    )
    notify_purchase_created = fields.Boolean(
        "Aufgabe bei neuer Herstelleranfrage", default=True, tracking=True,
        help="Wenn aus einer Sammelbestellung eine Anfrage beim Hersteller entsteht, bekommt der Zuständige eine Aufgabe.",
    )

    # Standardwerte für neue Blistercenter
    default_payment_model = fields.Selection(
        PAYMENT_MODELS, string="Standard-Zahlungsmodell", default="prepayment",
        required=True, tracking=True,
    )
    default_payment_term_id = fields.Many2one(
        "account.payment.term", string="Standard-Zahlungsbedingung", tracking=True,
        check_company=True,
    )
    default_pricelist_id = fields.Many2one(
        "product.pricelist", string="Standard-Preisliste", tracking=True,
        check_company=True,
    )

    # Prozessregeln
    rule_shop_requires_enable = fields.Boolean(
        "Shop nur für freigeschaltete Blistercenter", default=True, tracking=True,
        help="Warenkorb und Bestellbestätigung aus dem Shop nur für Kunden mit Shopfreischaltung.",
    )
    rule_block_confirm = fields.Boolean(
        "Zahlungssperre verhindert Auftragsbestätigung", default=True, tracking=True,
    )
    rule_block_delivery = fields.Boolean(
        "Zahlungssperre verhindert Versand", default=True, tracking=True,
    )
    rule_prepayment_before_collective = fields.Boolean(
        "Vorkasse: erst nach Zahlung in Sammelbestellung", default=True, tracking=True,
        help="Positionen von Vorkasse-Kunden kommen erst in eine Sammelbestellung, wenn der Auftrag bezahlt ist.",
    )
    rule_prepayment_before_delivery = fields.Boolean(
        "Vorkasse: Versand erst nach Zahlung", default=True, tracking=True,
    )

    _company_uniq = models.Constraint(
        "unique(company_id)", "Je Firma gibt es nur einen Satz Blistercenter-Einstellungen.",
    )

    @api.model
    def _get(self, company=None):
        """Einstellungen der Firma (sudo), werden bei Bedarf mit Standardwerten angelegt."""
        company = company or self.env.company
        config = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if not config:
            config = self.sudo().create({"company_id": company.id})
        return config

    @api.model
    def action_open(self):
        if not self.env.user.has_group("blistercenter.group_bc_manager"):
            raise AccessError(self.env._("Die Einstellungen darf nur die Rolle „Blistercenter Administrator“ öffnen."))
        config = self._get()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Einstellungen Blistercenter"),
            "res_model": self._name,
            "res_id": config.id,
            "view_mode": "form",
            "target": "current",
        }
