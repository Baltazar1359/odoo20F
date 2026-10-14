{
    "name": "PLCS GreenRX Wareneingang",
    "version": "20.0.1.7.3",
    "summary": "GreenRX-Retouren: Scan je Packung (Seriennummer, Charge, Verfall), Boxen, Vorprüfung mit Ampel, KabQ-Drucke, VP-Freigabe",
    "description": """
GreenRX-Wareneingang für PLCS GmbH
==================================
* Tickets aus dem GreenRX-ERP (Schnittstelle oder Import) mit Soll-Inhalt
* Geführter Scan-Ablauf für Handy und PC mit Handscanner:
  Paket wählen → Box scannen → Packungen scannen → Vorprüfung (Ampel) → Drucken → Abschluss
* Ein Scan speichert Seriennummer, Charge und Verfall aus dem securPharm-Code (GS1 und IFA)
* Regeln: eine Box = ein Artikel + eine Charge, jede Seriennummer nur einmal im System,
  Restlaufzeit, Doppelscan-Erkennung
* Drucke je Box: KabQ-Checkliste, Anlage Box-Inhalt, rotes Sperrblatt
* Freigabe durch die VP im 4-Augen-Prinzip, gesteuerte Bewegungen aus Quarantäne/Sperrlager
* ATIV-Schnittstelle: Gutschriften mit Tickets, Positionen, Verlauf und Dokumenten (alle 12 Stunden,
  auf Knopfdruck oder per Webhook angestoßen), Datenvertrag plcs.ativ.gutschrift/1
""",
    "author": "ETIV-System GmbH",
    "website": "https://etiv-system.de",
    "license": "OPL-1",
    "category": "Supply Chain/Inventory",
    "depends": ["stock", "product_expiry", "barcodes", "mail"],
    "data": [
        "security/plcs_greenrx_security.xml",
        "security/ir.access.csv",
        "data/plcs_greenrx_data.xml",
        "data/plcs_greenrx_cron.xml",
        "data/plcs_ativ_cron.xml",
        "report/plcs_greenrx_reports.xml",
        "report/report_box_documents.xml",
        "views/plcs_greenrx_ticket_views.xml",
        "views/plcs_greenrx_box_views.xml",
        "views/plcs_greenrx_connector_views.xml",
        "views/plcs_ativ_views.xml",
        "views/stock_views.xml",
        "views/res_config_settings_views.xml",
        "views/plcs_greenrx_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "plcs_greenrx/static/src/**/*",
        ],
        "web.assets_tests": [
            "plcs_greenrx/static/tests/tours/*.js",
        ],
    },
    "installable": True,
    "application": True,
}
