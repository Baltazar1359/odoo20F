{
    "name": "PLCS Geschäftsführungs-Dashboard",
    "version": "20.0.1.0.0",
    "summary": "Kennzahlen für die Geschäftsführung: Umsatz, Aufträge, Lager, Qualität/GDP – nur aus echten Odoo-Daten",
    "description": """
Geschäftsführungs-Dashboard der PLCS GmbH
=========================================
* KPI-Karten und Diagramme in vier Bereichen: Geschäft, operative Prozesse, Lager, Qualität/GDP
* Zeitfilter (heute, 7, 30, 90 Tage, frei) mit Vergleich zum vorherigen Zeitraum
* Klick auf eine Kennzahl öffnet die zugrunde liegenden Datensätze
* Nur Daten der aktiven Firma; Finanzkennzahlen nur für die Rolle „Finanzen“
* Kennzahlen ohne verlässliche Datengrundlage erscheinen als „nicht verfügbar“ mit Begründung
* GreenRX-Kennzahlen (Retouren, VP-Freigaben der Boxen), wenn das Modul installiert ist
""",
    "author": "ETIV-System GmbH",
    "website": "https://etiv-system.de",
    "license": "OPL-1",
    "category": "Productivity",
    "depends": ["blistercenter", "web"],
    "data": [
        "security/plcs_dashboard_security.xml",
        "views/dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "plcs_dashboard/static/src/dashboard/*",
        ],
        "web.assets_tests": [
            "plcs_dashboard/static/tests/tours/*",
        ],
    },
    "installable": True,
    "application": True,
}
