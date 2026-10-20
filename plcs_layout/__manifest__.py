{
    "name": "PLCS Briefpapier",
    "version": "20.0.1.0.0",
    "summary": "Briefpapier der PLCS GmbH für alle Odoo-Belege: Logo, Absenderzeile, Fußzeile mit Firmendaten",
    "description": """
PLCS-Briefpapier
================
Ein Dokumentlayout „PLCS“ nach dem Vorbild der PLCS-Gutschrift (Oktober 2026):

* Logo rechts oben, kleine Absenderzeile über der Empfängeradresse (Adresse links)
* Fußzeile: Firmenname in PLCS-Rot, Anschrift, Geschäftsführer, Telefon, E-Mail,
  Bankverbindung, Registergericht, Steuernummer, USt-IdNr.
* Optionaler Hinweis auf allen Belegen (z. B. Großhandelserlaubnis nach §52a AMG)

Alle Angaben kommen aus den Firmendaten in Odoo (Einstellungen › Firma) – nichts ist fest
im Code. Bei der Installation wird „PLCS“ das Dokumentlayout aller Firmen.
""",
    "author": "ETIV-System GmbH",
    "website": "https://etiv-system.de",
    "license": "OPL-1",
    "category": "Hidden/Tools",
    "depends": ["web", "base_setup"],
    "data": [
        "views/report_templates.xml",
        "data/report_layout.xml",
        "views/res_company_views.xml",
    ],
    "demo": [
        "demo/plcs_layout_demo.xml",
    ],
    "post_init_hook": "_plcs_layout_post_init",
    "installable": True,
    "application": False,
}
