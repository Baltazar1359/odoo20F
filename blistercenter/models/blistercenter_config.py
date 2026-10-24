import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import bc_auth

_logger = logging.getLogger(__name__)

# Packungsfotos per PZN (1.5.0, Entscheidung 21.10.2026). Platzhalter: {pzn} und Teile {a}{b}{c} (2/3/3 Ziffern)
DEFAULT_PRODUCT_IMAGE_URL = "https://cdn.shop-apotheke.com/images/600x0/D{a}/{b}/{c}/D{pzn}-p10.jpg"
IMAGE_MAX_BYTES = 5 * 1024 * 1024

# Freigabe des Verfahrens „automatische Freigabe“ setzt nur die zuständige VP (Regel R70)
CONFIG_PROTECTED_FIELDS = (
    "auto_release_enabled", "auto_release_sop", "auto_release_approved_by", "auto_release_approved_date",
)

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
             "(z. B. Forxiga, Xigduo, Daxas, Brilique). Alle anderen Artikel, auch "
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
    rule_send_proforma = fields.Boolean(
        "Proforma-Rechnung bei Bestätigung senden", default=True, tracking=True,
        help="Bei der Bestätigung bekommt das Blistercenter statt einer Auftragsbestätigung die Proforma-Rechnung "
             "als PDF per E-Mail. Zahlbar bis: Bestätigung plus Zahlungsziel. Der Versand wartet nicht auf die Zahlung.",
    )
    product_image_url = fields.Char(
        "Bildquelle Packungsfotos (PZN)", default=DEFAULT_PRODUCT_IMAGE_URL, tracking=True,
        help="Adresse eines Packungsfotos je PZN. Platzhalter: {pzn} (8 Ziffern) sowie {a}, {b}, {c} "
             "(PZN geteilt in 2, 3 und 3 Ziffern). Bildrechte: Die Quelle wählt PLCS.",
    )
    rule_customer_discount = fields.Boolean(
        "Kundenrabatt auf den HAP fest", default=True, tracking=True,
        help="Jede Warenposition bekommt den Rabatt des Kunden (0, 1 oder 2 %) auf den Verkaufspreis (HAP). "
             "Ein abweichender Rabatt oder ein Preis ungleich HAP in der Position verhindert die Bestätigung.",
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

    # Automatische Freigabe regulärer Herstellerlieferungen (Regel BC-AUTO-1, 1.3.0)
    auto_release_enabled = fields.Boolean(
        "Automatische Freigabe aktiv", readonly=True, tracking=True, copy=False,
        help="Reguläre, einwandfreie Herstellerlieferungen gehen nach bestandener Wareneingangsprüfung ohne "
             "VP-Einzelentscheidung in den Bestand. Jede Abweichung bleibt in der Quarantäne bei der VP. "
             "Aktivieren kann nur die zuständige VP nach Freigabe des Verfahrens (SOP).",
    )
    auto_release_sop = fields.Char(
        "Freigegebenes Verfahren (SOP)", readonly=True, tracking=True, copy=False,
        help="Verfahrensanweisung, die die automatische Freigabe beschreibt und von der VP freigegeben ist.",
    )
    auto_release_approved_by = fields.Many2one(
        "res.users", "Verfahren freigegeben von (VP)", readonly=True, tracking=True, copy=False)
    auto_release_approved_date = fields.Datetime("Verfahren freigegeben am", readonly=True, copy=False)

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
    def _get_readonly(self, company=None):
        """Einstellungen der Firma (sudo) ohne Anlegen – für lesende Website-Anfragen.

        Ohne gespeicherten Datensatz gelten die Standardwerte (nicht gespeicherter neuer Datensatz).
        """
        company = company or self.env.company
        config = self.sudo().search([("company_id", "=", company.id)], limit=1)
        return config or self.sudo().new({"company_id": company.id})

    def write(self, vals):
        bc_auth.check_protected(self, vals, CONFIG_PROTECTED_FIELDS)
        vp_changed = self.browse()
        if "vp_user_id" in vals:
            vp_changed = self.filtered(lambda c: c.auto_release_enabled and c.vp_user_id.id != vals["vp_user_id"])
        res = super().write(vals)
        for config in vp_changed:
            # Das Verfahren hat die bisherige VP freigegeben; die neue VP muss es selbst freigeben
            config._bc_set_auto_release_off(self.env._("Zuständige VP geändert – Verfahren neu freigeben."))
        if {"rule_shop_requires_enable"} & set(vals):
            # Zugriffsregel der Artikel hängt von dieser Einstellung ab (ir.access-Cache)
            self.env["res.users"]._bc_invalidate_shop_access()
        return res

    # ------------------------------------------------------------------
    # Automatische Freigabe (Regel BC-AUTO-1)
    # ------------------------------------------------------------------
    def _bc_enable_auto_release(self, sop):
        """Verfahren freigeben und Automatik einschalten – nur durch die zuständige VP."""
        self.ensure_one()
        user = self.env.user
        if not self.env.su and not (user.has_group("blistercenter.group_bc_approver") and user == self.vp_user_id):
            raise AccessError(self.env._(
                "Die automatische Freigabe schaltet nur die zuständige VP ein (Einstellungen › Zuständig "
                "Freigabe (VP): %(vp)s).", vp=self.vp_user_id.display_name or "-"))
        if not (sop or "").strip():
            raise UserError(self.env._("Bitte das freigegebene Verfahren (SOP, Version) angeben."))
        bc_auth.grant(self.sudo(), "auto_release_config").write({
            "auto_release_enabled": True,
            "auto_release_sop": sop.strip(),
            "auto_release_approved_by": user.id,
            "auto_release_approved_date": fields.Datetime.now(),
        })
        self.sudo().message_post(body=self.env._(
            "Automatische Freigabe regulärer Herstellerlieferungen eingeschaltet (Regel BC-AUTO-1, Verfahren: %(sop)s).",
            sop=sop.strip()))
        return True

    def _bc_disable_auto_release(self, reason=False):
        """Automatik ausschalten – VP oder Administrator der App (Ausschalten ist die sichere Richtung)."""
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")):
            raise AccessError(self.env._("Die automatische Freigabe schaltet nur die Rolle „Freigabe“ (VP) aus."))
        self._bc_set_auto_release_off(reason or self.env._("Von %(user)s ausgeschaltet.", user=self.env.user.name))
        return True

    def _bc_set_auto_release_off(self, reason):
        for config in self.filtered("auto_release_enabled"):
            bc_auth.grant(config.sudo(), "auto_release_config").write({
                "auto_release_enabled": False,
                "auto_release_approved_by": False,
                "auto_release_approved_date": False,
            })
            config.sudo().message_post(body=self.env._(
                "Automatische Freigabe ausgeschaltet: %(reason)s", reason=reason))

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

    # ------------------------------------------------------------------
    # Umsatzsteuer: Inland 19 %, Export 0 % (1.5.0, Entscheidung 21.10.2026)
    # ------------------------------------------------------------------
    @api.model
    def _bc_setup_tax_positions(self, company=None):
        """Deutscher Kontenrahmen (SKR03/SKR04): Steuerzuordnung für Kunden im Ausland automatisch.

        Odoo bringt mit l10n_de 19 % für Inland und 0 % für Ausfuhr (Nicht-EU) bzw. innergemeinschaftliche
        Lieferung (EU mit USt-IdNr.). Automatisch erkannt werden aber nur Inland und EU mit USt-IdNr.
        Hier zusätzlich: EU ohne USt-IdNr. (bleibt 19 %) und Nicht-EU (Ausfuhr 0 %) nach Lieferland.
        Nur Schalter „Automatisch erkennen“ und Ländergruppe; Steuern und Konten bleiben wie von Odoo.
        """
        company = company or self.env.company
        template = company.chart_template or ""
        if not template.startswith("de_skr"):
            return False
        Chart = self.env["account.chart.template"].with_company(company)
        skr = template[len("de_"):]
        eu_vat = Chart.ref("fiscal_position_eu_vat_id_partner_%s" % skr, raise_if_not_found=False)
        eu_no_id = Chart.ref("fiscal_position_eu_no_id_partner_%s" % skr, raise_if_not_found=False)
        non_eu = Chart.ref("fiscal_position_non_eu_partner_%s" % skr, raise_if_not_found=False)
        eu_group = (eu_vat and eu_vat.country_group_id) or self.env.ref("base.europe")
        if eu_no_id and not eu_no_id.auto_apply:
            eu_no_id.write({"auto_apply": True, "country_group_id": eu_no_id.country_group_id.id or eu_group.id})
        if non_eu and not non_eu.auto_apply:
            non_eu.auto_apply = True
        return True

    @api.model
    def _bc_demo_taxes(self):
        """Nur Dev-Builds: Der Odoo-Demokontenrahmen (generisch, 15 %) bekommt deutsche Sätze –
        19 % USt, 0 % Ausfuhr (Nicht-EU), 0 % EU mit USt-IdNr. – mit automatischer Zuordnung wie im SKR03.
        Production erhält das aus dem deutschen Kontenrahmen (siehe _bc_setup_tax_positions)."""
        company = self.env.company
        if self._bc_setup_tax_positions(company):
            return company.account_sale_tax_id
        Tax = self.env["account.tax"].with_company(company)
        Fpos = self.env["account.fiscal.position"].with_company(company)
        europe = self.env.ref("base.europe")

        def fpos(name, sequence, **vals):
            rec = Fpos.search([("company_id", "=", company.id), ("name", "=", name)], limit=1)
            return rec or Fpos.create({"name": name, "sequence": sequence, "auto_apply": True,
                                       "company_id": company.id, **vals})

        Group = self.env["account.tax.group"].with_company(company)

        def group(name):
            rec = Group.search([("company_id", "=", company.id), ("name", "=", name)], limit=1)
            return rec or Group.create({"name": name, "company_id": company.id})

        def tax(name, amount, **vals):
            # Eigene Steuergruppe, sonst steht in der Summenzeile „Steuer 15 %“ des Demokontenrahmens
            vals["tax_group_id"] = group("USt %s %%" % int(amount)).id
            rec = Tax.search([("company_id", "=", company.id), ("name", "=", name),
                              ("type_tax_use", "=", "sale")], limit=1)
            if rec:
                if rec.tax_group_id.id != vals["tax_group_id"]:
                    rec.tax_group_id = vals["tax_group_id"]
                return rec
            return Tax.create({"name": name, "amount": amount, "amount_type": "percent",
                               "type_tax_use": "sale", "company_id": company.id, **vals})

        inland = fpos("Geschäftspartner Inland", 10, country_id=self.env.ref("base.de").id)
        eu_vat = fpos("Geschäftspartner EU (mit USt-ID)", 20, country_group_id=europe.id, vat_required=True)
        eu_no_id = fpos("Geschäftspartner EU (ohne USt-ID)", 40, country_group_id=europe.id)
        non_eu = fpos("Geschäftspartner Ausland (Nicht-EU)", 50)
        # Zuordnungen des Demokontenrahmens (z. B. „Foreign Trade“) nicht mehr automatisch anwenden
        Fpos.search([("company_id", "=", company.id), ("auto_apply", "=", True),
                     ("id", "not in", (inland | eu_vat | eu_no_id | non_eu).ids)]).auto_apply = False
        ust19 = tax("19% USt", 19.0, invoice_label="19 % USt")
        tax("0% Ausfuhr", 0.0, invoice_label="0 % steuerfreie Ausfuhr",
            fiscal_position_ids=[(6, 0, non_eu.ids)], original_tax_ids=[(6, 0, ust19.ids)])
        tax("0% EU", 0.0, invoice_label="0 % steuerfreie innergemeinschaftliche Lieferung",
            fiscal_position_ids=[(6, 0, eu_vat.ids)], original_tax_ids=[(6, 0, ust19.ids)])
        company.account_sale_tax_id = ust19
        return ust19

    @api.model
    def _bc_demo_euro(self):
        """Nur Dev-Builds (Demodaten): Firma und Preislisten in Euro (Hinweis 21.10.2026).

        Die Odoo-Demodaten von „account“ laden für die Hauptfirma fest den US-Kontenrahmen und buchen
        in USD; danach sperrt Odoo den Währungswechsel. In der Wegwerf-Datenbank wird die Währung
        deshalb direkt umgestellt (die Demobuchungen behalten ihre Zahlen). Production ist bereits EUR;
        dort legt Odoo beim Installieren der Buchhaltung den deutschen Kontenrahmen in Euro an.
        """
        euro = self.env.ref("base.EUR")
        company = self.env.company
        if company.currency_id != euro:
            self.env.flush_all()
            self.env.cr.execute("UPDATE res_company SET currency_id = %s WHERE id = %s", (euro.id, company.id))
            self.env.invalidate_all()
        Pricelist = self.env["product.pricelist"].with_context(active_test=False)
        pricelists = Pricelist.search([("company_id", "in", [company.id, False]), ("currency_id", "!=", euro.id)])
        pricelists.write({"currency_id": euro.id})
        # Demo-Preisliste „USD“ rechnet bereits in Euro: Namen angleichen
        Pricelist.search([("company_id", "in", [company.id, False]), ("name", "=", "USD"),
                          ("currency_id", "=", euro.id)]).name = "Standard (EUR)"
        # Eine Preisliste im Shop, kein Preislisten-/Währungswähler (Demo-Preislisten „Benelux“ usw. archivieren)
        if "selectable" in Pricelist._fields:
            own = Pricelist.search([("company_id", "in", [company.id, False]), ("active", "=", True)])
            keep = self._get(company).default_pricelist_id or own.sorted(lambda p: (p.sequence, p.id))[:1]
            own.write({"selectable": False})
            (own - keep).filtered("website_id").active = False

    @api.model
    def _bc_load_demo_data(self):
        """Demodaten (1.3.0): Shop-Sortiment, Hersteller und Testkunden – nur in Datenbanken mit Demodaten."""
        from ..tools import testdaten
        # Beim Laden der Demodaten die Firmendaten erneut setzen (Demodaten anderer Module setzen den
        # Firmennamen zurück). Nicht bei direkten Aufrufen, z. B. aus Tests.
        if self.env.context.get("install_demo"):
            self.env["res.company"]._plcs_load_demo()
            # PLCS sitzt in Deutschland: Website und Kundenportal auf Deutsch (Hinweis 21.10.2026)
            if "website" in self.env and "default_lang_id" in self.env["website"]._fields:
                german = self.env["res.lang"]._lang_get("de_DE")
                for website in self.env["website"].search([]):
                    website.write({"language_ids": [(4, german.id)], "default_lang_id": german.id})
        demo = self.env.context.get("install_demo")
        ust19 = False
        if demo:
            self._bc_demo_euro()
            ust19 = self._bc_demo_taxes()
        products = testdaten.load(self.env)
        if demo:
            # Belege an die Testkunden auf Deutsch drucken
            self.env["res.partner"].search([("bc_is_customer", "=", True)]).lang = "de_DE"
            if ust19:
                for product in products.values():
                    product.taxes_id = [(6, 0, ust19.ids)]
            # Packungsfotos per PZN (Entscheidung 21.10.2026); ohne Internet bleiben die Artikel ohne Foto
            templates = self.env["product.template"].browse([p.product_tmpl_id.id for p in products.values()])
            self._bc_fetch_product_images(templates.filtered("bc_shop_product"))
        # Nur PLCS-Arzneimittel als Waren: Odoo-Demomöbel usw. archivieren (Entscheidung 21.10.2026)
        testdaten.archive_foreign_demo_products(self.env)

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

    # ------------------------------------------------------------------
    # Packungsfotos
    # ------------------------------------------------------------------
    @api.model
    def _bc_product_pzn(self, template):
        pzn = ("plcs_pzn" in template._fields and template.plcs_pzn) or template.default_code or ""
        return pzn if re.fullmatch(r"\d{8}", pzn) else False

    @api.model
    def _bc_fetch_product_images(self, templates, force=False, timeout=10):
        """Packungsfoto je PZN aus der eingestellten Bildquelle laden (nur JPEG/PNG bis 5 MB).

        Artikel mit Foto bleiben unverändert (außer force). Ohne Internetverbindung bricht der Lauf nach dem
        ersten Verbindungsfehler ab. Gibt (Artikel mit neuem Foto, Liste der Artikel ohne Foto) zurück.
        """
        import requests
        from odoo.tools.binary import BinaryBytes

        done = self.env["product.template"]
        missing = []
        pending = templates.filtered(lambda t: force or not t.image_1920)
        for index, template in enumerate(pending):
            pattern = self._get(template.company_id or self.env.company).product_image_url or DEFAULT_PRODUCT_IMAGE_URL
            pzn = self._bc_product_pzn(template)
            if not pzn:
                missing.append(template.display_name)
                continue
            url = pattern.format(pzn=pzn, a=pzn[:2], b=pzn[2:5], c=pzn[5:])
            try:
                res = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (PLCS Odoo)"})
            except requests.ConnectionError as exc:
                _logger.info("Packungsfotos: keine Verbindung zu %s (%s)", url, exc)
                missing.extend(pending[index:].mapped("display_name"))
                break
            except requests.RequestException as exc:
                _logger.info("Packungsfoto %s nicht geladen: %s", pzn, exc)
                missing.append(template.display_name)
                continue
            data = res.content or b""
            is_image = data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            if res.status_code != 200 or not is_image or len(data) > IMAGE_MAX_BYTES:
                _logger.info("Packungsfoto %s nicht geladen (HTTP %s, %s Bytes)", pzn, res.status_code, len(data))
                missing.append(template.display_name)
                continue
            template.image_1920 = BinaryBytes(data)
            done |= template
        return done, missing

    def action_load_product_images(self):
        """Knopf „Packungsfotos laden“: fehlende Fotos des Shop-Sortiments per PZN laden."""
        if not self.env.user.has_group("blistercenter.group_bc_manager"):
            raise AccessError(self.env._("Die Einstellungen darf nur die Rolle „Blistercenter Administrator“ ändern."))
        self.ensure_one()
        templates = self.env["product.template"].sudo().search([
            ("bc_shop_product", "=", True), ("company_id", "in", [self.company_id.id, False])])
        done, missing = self.sudo()._bc_fetch_product_images(templates)
        message = self.env._("%(count)s Packungsfotos geladen.", count=len(done))
        if missing:
            message += " " + self.env._("Ohne Foto: %(names)s", names=", ".join(missing))
        self.message_post(body=message)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("Packungsfotos"), "message": message,
                       "type": "warning" if missing else "success", "sticky": bool(missing)},
        }

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
