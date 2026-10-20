from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

# Seit 1.2.0: Rechnung je Lieferung mit Zahlungsziel; Vorkasse entfällt.
PAYMENT_MODELS = [
    ("direct_debit", "Lastschrift (DATEV)"),
    ("transfer", "Überweisung"),
]
# Kundenrabatt auf den HAP (Verkaufspreis des Artikels), für alle Artikel gleich
DISCOUNTS = [
    ("0", "0 %"),
    ("1", "1 %"),
    ("2", "2 %"),
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
    accounting_user_id = fields.Many2one(
        "res.users", string="Zuständig Buchhaltung", tracking=True,
        help="Erhält die Aufgabe, die Rechnung zu einer Lieferung zu prüfen, zu buchen und an den Kunden zu senden.",
    )
    purchase_user_id = fields.Many2one(
        "res.users", string="Zuständig Sammelbestellungen", tracking=True,
        help="Standard-Verantwortlicher neuer Sammelbestellungen; erhält die Aufgabe, die Anfrage beim Hersteller zu bestellen.",
    )
    vp_user_id = fields.Many2one(
        "res.users", string="Zuständig Freigabe (VP)", tracking=True,
        help="Erhält die Aufgabe, gebuchte Wareneingänge aus der Quarantäne freizugeben.",
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
    notify_release = fields.Boolean(
        "Aufgabe bei Wareneingang in Quarantäne", default=True, tracking=True,
        help="Nach dem Wareneingang bekommt die zuständige VP eine Aufgabe zur Freigabe.",
    )

    # Standardwerte für neue Blistercenter
    default_payment_model = fields.Selection(
        PAYMENT_MODELS, string="Standard-Zahlungsart", default="direct_debit",
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

    # Prozessregeln: Bestellung und Zahlung
    rule_shop_requires_enable = fields.Boolean(
        "Shop nur für freigeschaltete Blistercenter", default=True, tracking=True,
        help="Warenkorb und Bestellbestätigung aus dem Shop nur für Kunden mit Shopfreischaltung.",
    )
    rule_shop_only_bc_products = fields.Boolean(
        "Shop zeigt nur Blistercenter-Artikel", default=True, tracking=True,
        help="Im Shop erscheinen und bestellbar sind nur Artikel mit „Im Blistercenter-Shop“ "
             "(z. B. Forxiga, Xigduo, Daxas, Brilique, Tagrisso). Alle anderen Artikel, auch "
             "Odoo-Demoprodukte, sind dort unsichtbar. Telefonbestellungen sind nicht betroffen.",
    )
    shop_product_count = fields.Integer(
        "Artikel im Shop-Sortiment", compute="_compute_shop_product_count",
        help="Artikel mit „Im Blistercenter-Shop“ (Regel R40).")
    rule_require_channel = fields.Boolean(
        "Bestellweg und Besteller Pflicht", default=True, tracking=True,
        help="Vor der Bestätigung muss der Bestellweg feststehen. Bei Telefon, E-Mail und Fax ist der Besteller Pflicht.",
    )
    rule_block_confirm = fields.Boolean(
        "Zahlungssperre verhindert Auftragsbestätigung", default=True, tracking=True,
    )
    rule_block_delivery = fields.Boolean(
        "Zahlungssperre verhindert Versand", default=True, tracking=True,
    )
    rule_require_payment_term = fields.Boolean(
        "Zahlungsziel am Kunden Pflicht", default=True, tracking=True,
        help="Ohne Zahlungsbedingung (z. B. 7 oder 14 Tage) lässt sich eine Blistercenter-Bestellung nicht bestätigen.",
    )
    rule_customer_discount = fields.Boolean(
        "Kundenrabatt auf den HAP fest", default=True, tracking=True,
        help="Jede Warenposition bekommt den Rabatt des Kunden (0, 1 oder 2 %) auf den Verkaufspreis (HAP). "
             "Ein abweichender Rabatt in der Position verhindert die Bestätigung.",
    )
    rule_invoice_per_delivery = fields.Boolean(
        "Rechnung je Lieferung", default=True, tracking=True,
        help="Nach jeder Lieferung legt Odoo die Rechnung über die gelieferte Menge als Entwurf an "
             "(kein Buchen, kein Versand). Artikel müssen „Fakturierung nach gelieferter Menge“ haben.",
    )
    rule_forbid_mto_route = fields.Boolean(
        "Route „Auf Auftrag beschaffen“ bei Blistercenter-Bestellungen verbieten", default=True, tracking=True,
        help="Sonst legt Odoo bei der Bestätigung selbst eine Bestellung beim Lieferanten an "
             "und die Sammelbestellung würde doppelt bestellen.",
    )
    rule_require_lot_tracking = fields.Boolean(
        "Nur chargengeführte Artikel mit Verfallsdatum bündeln", default=True, tracking=True,
    )

    # Prozessregeln: Lager
    rule_fixed_allocation = fields.Boolean(
        "Ware fest je Kunde zuordnen", default=True, tracking=True,
        help="Die Lieferung an ein Blistercenter wartet auf die Ware aus seiner Sammelbestellung "
             "und reserviert nur diese. Ausnahme je Position: „Aus Lagerbestand liefern“.",
    )
    rule_release_four_eyes = fields.Boolean(
        "Freigabe Wareneingang nach Vier-Augen-Prinzip", default=True, tracking=True,
        help="Wer den Wareneingang gebucht hat, darf ihn nicht selbst aus der Quarantäne freigeben.",
    )
    short_shelf_months = fields.Integer(
        "Kurzläufer: Restlaufzeit unter (Monate)", default=3, tracking=True,
        help="Chargen mit kürzerer Restlaufzeit brauchen eine zusätzliche Freigabe der VP.",
    )
    rule_short_release = fields.Boolean(
        "Kurzläufer: zusätzliche VP-Freigabe beim Einlagern", default=True, tracking=True,
    )
    rule_short_delivery = fields.Boolean(
        "Kurzläufer: zusätzliche VP-Freigabe beim Versand", default=True, tracking=True,
    )
    rule_check_four_eyes = fields.Boolean(
        "Endkontrolle beim Versand durch zweite Person", default=True, tracking=True,
        help="Wer kommissioniert hat, darf die Lieferung nicht selbst bestätigen.",
    )
    rule_partial_needs_approval = fields.Boolean(
        "Teillieferung nur nach Rücksprache", default=True, tracking=True,
        help="Standard ist Komplettlieferung. Eine Teillieferung muss im Auftrag mit Vermerk erlaubt sein.",
    )

    # Lager-Einrichtung (vom Modul angelegt)
    warehouse_id = fields.Many2one("stock.warehouse", string="Lager", readonly=True, check_company=True)
    quarantine_location_id = fields.Many2one(
        "stock.location", string="Quarantäne", readonly=True, check_company=True,
        help="Hier landet jeder Wareneingang aus Sammelbestellungen bis zur Freigabe durch die VP.",
    )
    receipt_type_id = fields.Many2one("stock.picking.type", string="Vorgangstyp Eingang", readonly=True, check_company=True)
    release_type_id = fields.Many2one("stock.picking.type", string="Vorgangstyp Freigabe (VP)", readonly=True, check_company=True)

    _company_uniq = models.Constraint(
        "unique(company_id)", "Je Firma gibt es nur einen Satz Blistercenter-Einstellungen.",
    )

    @api.constrains("short_shelf_months")
    def _check_short_shelf_months(self):
        for config in self:
            if config.short_shelf_months < 0:
                raise UserError(self.env._("Die Restlaufzeit für Kurzläufer darf nicht negativ sein."))

    @api.model
    def _get(self, company=None):
        """Einstellungen der Firma (sudo), werden bei Bedarf mit Standardwerten angelegt."""
        company = company or self.env.company
        config = self.sudo().search([("company_id", "=", company.id)], limit=1)
        if not config:
            config = self.sudo().create({"company_id": company.id})
        return config

    @api.model
    def _setup_all(self):
        for company in self.env["res.company"].sudo().search([]):
            self._get(company)._bc_setup()

    def _bc_setup(self):
        """Quarantäne und Vorgangstypen für Blistercenter-Ware anlegen (idempotent)."""
        self.ensure_one()
        config = self.sudo()
        company = config.company_id
        warehouse = config.warehouse_id or self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", company.id)], limit=1)
        if not warehouse:
            return False
        Location = self.env["stock.location"].sudo().with_company(company)

        def location(name, parent, usage="internal"):
            loc = Location.search([("name", "=", name), ("location_id", "=", parent.id)], limit=1)
            if not loc:
                loc = Location.create({"name": name, "location_id": parent.id, "usage": usage, "company_id": company.id})
            return loc

        root = location("Blistercenter", warehouse.view_location_id, usage="view")
        quarantine = config.quarantine_location_id or location("Quarantäne", root)
        PickingType = self.env["stock.picking.type"].sudo().with_company(company)

        def picking_type(code, name, kind, src, dest, **extra):
            ptype = PickingType.search([("sequence_code", "=", code), ("company_id", "=", company.id)], limit=1)
            if not ptype:
                vals = {
                    "name": name, "sequence_code": code, "code": kind, "warehouse_id": warehouse.id,
                    "company_id": company.id, "default_location_src_id": src.id,
                    "default_location_dest_id": dest.id,
                }
                vals.update(extra)
                ptype = PickingType.create(vals)
            return ptype

        supplier = self.env.ref("stock.stock_location_suppliers")
        receipt = config.receipt_type_id or picking_type(
            "BCIN", "Blistercenter: Wareneingang", "incoming", supplier, quarantine,
            use_create_lots=True, use_existing_lots=True)
        release = config.release_type_id or picking_type(
            "BCFRG", "Blistercenter: Freigabe (VP)", "internal", quarantine, warehouse.lot_stock_id,
            create_backorder="always")
        config.write({
            "warehouse_id": warehouse.id,
            "quarantine_location_id": quarantine.id,
            "receipt_type_id": receipt.id,
            "release_type_id": release.id,
        })
        return True

    def _compute_shop_product_count(self):
        count = self.env["product.template"].sudo().search_count(
            [("bc_shop_product", "=", True), ("sale_ok", "=", True)])
        for config in self:
            config.shop_product_count = count

    def action_open_shop_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Shop-Sortiment"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("bc_shop_product", "=", True)],
            "context": {"default_bc_shop_product": True},
        }

    @api.model
    def _bc_apply_website_branding(self):
        """PLCS-Farben für Website und Kundenportal (1.4.0). Nur einmal (Installation, Update auf 1.4.0),
        damit spätere Änderungen im Website-Editor erhalten bleiben."""
        if "website" not in self.env:
            return
        palette = {
            "o-color-1": "#9c0000",  # PLCS-Rot (Briefpapier)
            "o-color-2": "#828282",  # PLCS-Grau (Logo)
            "o-color-3": "#f4f4f4",
            "o-color-4": "#ffffff",
            "o-color-5": "#222222",
        }
        Assets = self.env["website.assets"]
        for website in self.env["website"].search([]):
            Assets.with_context(website_id=website.id).make_scss_customization(
                "/website/static/src/scss/options/colors/user_color_palette.scss", palette)
            company = website.company_id
            if company.logo and not website.logo:
                from odoo.tools.binary import BinaryBytes
                website.logo = BinaryBytes(company.logo.content)

    @api.model
    def _bc_load_demo_data(self):
        """Demodaten (1.3.0): Shop-Sortiment, Hersteller und Testkunden – nur in Datenbanken mit Demodaten."""
        from ..tools import testdaten
        testdaten.load(self.env)

    def _bc_check_setup(self):
        """Lager-Einrichtung prüfen (legt sie bei Bedarf an) und die Einstellungen zurückgeben."""
        self.ensure_one()
        if not (self.quarantine_location_id and self.receipt_type_id and self.release_type_id):
            self._bc_setup()
        if not (self.quarantine_location_id and self.receipt_type_id and self.release_type_id):
            raise UserError(self.env._(
                "Das Blistercenter-Lager ist für %(company)s nicht eingerichtet (kein Lager gefunden).",
                company=self.company_id.name))
        return self

    def action_setup_stock(self):
        if not self.env.user.has_group("blistercenter.group_bc_manager"):
            raise AccessError(self.env._("Die Einstellungen darf nur die Rolle „Blistercenter Administrator“ ändern."))
        for config in self:
            config._bc_check_setup()
        return True

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
