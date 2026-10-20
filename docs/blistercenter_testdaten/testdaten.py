"""Testdaten für den Blistercenter-Test (nur Test- und Dev-Datenbanken, nicht Production).

Ab Blistercenter 1.3.0 legt das Modul die Testdaten in Datenbanken mit Demodaten (z. B. jedem
Odoo.sh-Dev-Build) **automatisch** an (`blistercenter/tools/testdaten.py`). Dieses Skript ist nur
noch nötig, um

- PLCS-Logo (plcs_logo.png neben diesem Skript) für Firma und Website zu setzen,
- Produktfotos aus dem Internet zu laden (Apotheken-Bild-CDN; nur für Testsysteme, für den
  Echtbetrieb lizenzierte Bilder verwenden),
- die Testdaten in einer Datenbank **ohne** Demodaten anzulegen bzw. zu aktualisieren.

Inhalt (beliebig oft ausführbar, keine Duplikate): Hersteller „AstraZeneca GmbH (Test)“,
20 Arzneimittel (PZN, GTIN, Charge + Verfall, Fakturierung nach Lieferung, Einkaufskondition),
davon im Shop nur Forxiga, Xigduo, Daxas, Brilique, Tagrisso (13 Artikel, je Medikament eine
Shop-Kategorie), drei Blistercenter-Testkunden mit Rabatt 0 / 1 / 2 % und Zahlungsziel 14 / 7 / 14 Tage.
Einkaufspreise sind fiktiv, HAP aus dem Apothekenverkaufspreis gerechnet (Testwert, nicht Lauer-Taxe).
Benutzer und Portalzugänge legt das Skript nicht an.

Aufruf im Odoo.sh-Shell des Dev-Builds:
    odoo-bin shell --no-http < ~/src/user/docs/blistercenter_testdaten/testdaten.py
"""
import base64
import os

from odoo.addons.blistercenter.tools import testdaten

try:  # Odoo 20: Binärfelder erwarten BinaryValue
    from odoo.tools.binary import BinaryBytes

    def to_binary(raw):
        return BinaryBytes(raw)
except ImportError:  # ältere Versionen: base64-Text
    def to_binary(raw):
        return base64.b64encode(raw).decode()

# env, odoo: vom Odoo-Shell bereitgestellt

try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:  # Aufruf über stdin (odoo-bin shell < testdaten.py)
    BASE_DIR = os.path.expanduser("~/src/user/docs/blistercenter_testdaten")
if not os.path.isdir(BASE_DIR):
    BASE_DIR = os.environ.get("BC_TESTDATEN_DIR", BASE_DIR)

IMAGE_URL = "https://cdn.shop-apotheke.com/images/256x0/D{a}/{b}/{c}/D{pzn}-p10.jpg"


def download(url):
    try:
        import requests
        res = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (PLCS Testdaten)"})
        if res.status_code == 200 and res.content[:3] == b"\xff\xd8\xff":
            return to_binary(res.content)
        print("  Foto nicht geladen (%s): %s" % (res.status_code, url))
    except Exception as exc:  # noqa: BLE001 – Testdaten: ohne Foto weitermachen
        print("  Foto nicht geladen (%s): %s" % (type(exc).__name__, url))
    return False


def run(env):
    company = env.company
    report = []

    logo_path = os.path.join(BASE_DIR, "plcs_logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as fh:
            logo = to_binary(fh.read())
        company.write({"logo": logo})
        if "website" in env:
            env["website"].search([]).write({"logo": logo})
        report.append("Logo gesetzt")
    else:
        report.append("Logo nicht gefunden: %s" % logo_path)

    products = testdaten.load(env, log=print)
    in_shop = sum(1 for p in products.values() if p.bc_shop_product)
    images = 0
    for pzn, product in products.items():
        image = download(IMAGE_URL.format(a=pzn[:2], b=pzn[2:5], c=pzn[5:], pzn=pzn))
        if image:
            product.image_1920 = image
            images += 1
    report.append("%s Artikel, davon %s im Shop, %s mit Foto" % (len(products), in_shop, images))
    report.append("Hersteller und %s Testkunden angelegt" % len(testdaten.CUSTOMERS))

    env.cr.commit()
    print("Blistercenter-Testdaten: " + "; ".join(report))


run(env)  # noqa: F821
