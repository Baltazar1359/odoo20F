{
    "name": "Blistercenter",
    "version": "20.0.1.0.0",
    "summary": "PLCS-Großhandel für Blistercenter: Kunden, Shop-Bestellungen, Sammelbestellungen beim Hersteller, Versand, Chargen und Rückruf",
    "description": """
Blistercenter – Großhandel der PLCS GmbH
========================================
Blistercenter bestellen bei PLCS (Shop oder Auftrag). PLCS bündelt die Kundenmengen
in Sammelbestellungen und bestellt manuell beim Hersteller (z. B. AstraZeneca).

Das Modul nutzt die Odoo-Standardmodelle für Shop, Verkauf, Einkauf, Rechnungen
und Lager und macht sie in der App „Blistercenter“ erreichbar. Es legt keine
parallele Buchhaltung und keine doppelten Bestell- oder Lagerdaten an.

* Kunden: Blistercenter-Kennzeichen, Shopfreischaltung, Zahlungsmodell, Zahlungssperre
* Kundenbestellungen mit Bearbeitungsstatus
* Sammelbestellungen: Kundenmengen auswählen, bündeln, Anfrage beim Hersteller erzeugen
* Lager und Versand, Chargen mit Sperre, Rückrufauswertung
* Einstellungen je Firma: Zuständigkeiten, Benachrichtigungen, Standardwerte, Prozessregeln
* Rollen: Benutzer, Freigabe, Administrator
""",
    "author": "ETIV-System GmbH",
    "website": "https://etiv-system.de",
    "license": "OPL-1",
    "category": "Sales/Sales",
    "depends": [
        "mail",
        "sale_management",
        "sale_stock",
        "purchase_stock",
        "account",
        "website_sale",
        "product_expiry",
    ],
    "data": [
        "security/blistercenter_security.xml",
        "security/ir.access.csv",
        "data/blistercenter_data.xml",
        "views/blistercenter_config_views.xml",
        "views/res_partner_views.xml",
        "views/sale_order_views.xml",
        "views/collective_order_views.xml",
        "views/stock_views.xml",
        "views/overview_views.xml",
        "wizard/collective_add_views.xml",
        "wizard/recall_views.xml",
        "views/blistercenter_menus.xml",
    ],
    "installable": True,
    "application": True,
}
