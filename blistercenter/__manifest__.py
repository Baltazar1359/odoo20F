{
    "name": "Blistercenter",
    "version": "20.0.1.5.1",
    "summary": "PLCS-Großhandel für Blistercenter: Kunden, Shop-Bestellungen, Sammelbestellungen beim Hersteller, Versand, Chargen und Rückruf",
    "description": """
Blistercenter – Großhandel der PLCS GmbH
========================================
Blistercenter bestellen bei PLCS (Shop oder Auftrag). PLCS bündelt die Kundenmengen
in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

Das Modul nutzt die Odoo-Standardmodelle für Shop, Verkauf, Einkauf, Rechnungen
und Lager und macht sie in der App „Blistercenter“ erreichbar. Es legt keine
parallele Buchhaltung und keine doppelten Bestell- oder Lagerdaten an.

* Kunden: Blistercenter-Kennzeichen, Shopfreischaltung, Zahlungsart (Lastschrift über DATEV),
  Zahlungsziel (7 oder 14 Tage), Rabatt 0/1/2 % auf den HAP, Zahlungssperre
* Kundenbestellungen aus Shop, Telefon, E-Mail oder Fax, mit Bestellweg und Besteller
* Proforma-Rechnung (nur Information), Rechnung je Lieferung über die gelieferte Menge
* Hinweis auf überfällige Rechnungen (keine Sperre)
* Sammelbestellungen: Liste „Zu bestellen“ je Hersteller, PLCS bündelt und bestellt manuell
* Wareneingang in Quarantäne, Freigabe durch die VP (Vier-Augen-Prinzip), Kurzläufer-Freigabe
* Ware fest je Kunde, Endkontrolle durch zweite Person, Teillieferung nur nach Rücksprache
* Chargen mit Sperre, Rückrufauswertung
* Einstellungen je Firma: Zuständigkeiten, Benachrichtigungen, Standardwerte, Prozessregeln
* Rollen: Benutzer, Freigabe, Administrator
""",
    "author": "ETIV-System GmbH",
    "website": "https://etiv-system.de",
    "license": "OPL-1",
    "category": "Sales/Sales",
    "depends": [
        "mail",
        "portal",
        "sale_management",
        "sale_stock",
        "purchase_stock",
        "account",
        "website_sale",
        "product_expiry",
        "plcs_layout",
    ],
    "data": [
        "security/blistercenter_security.xml",
        "security/ir.access.csv",
        "data/blistercenter_data.xml",
        "views/blistercenter_config_views.xml",
        "views/res_partner_views.xml",
        "views/product_views.xml",
        "views/sale_order_views.xml",
        "views/collective_order_views.xml",
        "views/stock_views.xml",
        "views/overview_views.xml",
        "wizard/collective_add_views.xml",
        "wizard/recall_views.xml",
        "wizard/bc_wizards_views.xml",
        "wizard/order_sheet_views.xml",
        "wizard/receipt_scan_views.xml",
        "views/blistercenter_menus.xml",
        "report/bc_proforma.xml",
        "data/mail_template_data.xml",
        "views/portal_templates.xml",
        "views/website_templates.xml",
        "data/portal_entry_data.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "blistercenter/static/src/js/order_sheet.js",
        ],
    },
    "demo": [
        "demo/blistercenter_demo.xml",
    ],
    "post_init_hook": "_bc_post_init",
    "installable": True,
    "application": True,
}
