"""Testdaten für den Blistercenter-Test (nur Test- und Dev-Datenbanken, nicht Production).

Legt an bzw. aktualisiert (beliebig oft ausführbar, keine Duplikate):
- Firmenlogo und Website-Logo: PLCS-Logo (plcs_logo.png neben diesem Skript),
- Hersteller „AstraZeneca GmbH (Test)“ mit Kennzeichen „Hersteller (Blistercenter)“,
- 10 Arzneimittel aus der GreenRX-Artikelliste (PZN, GTIN, Charge + Verfall, im Shop sichtbar,
  Einkaufskondition beim Hersteller, Produktfoto aus dem Internet),
- Blistercenter-Testkunde mit Ansprechpartner (Vorkasse, Shop freigeschaltet, ohne Portalzugang).

Preise sind fiktive Testpreise. Benutzer und Portalzugänge legt das Skript nicht an.

Aufruf im Odoo.sh-Shell des Dev-Builds:
    odoo-bin shell < ~/src/user/docs/blistercenter_testdaten/testdaten.py

Die Produktfotos lädt der Server beim Aufruf herunter (Apotheken-Bild-CDN). Ohne Internet
werden die Artikel ohne Foto angelegt. Die Fotos sind nur für Testsysteme gedacht; für den
Echtbetrieb lizenzierte Bilder verwenden (z. B. vom Hersteller).
"""
import base64
import os

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
except NameError:  # Aufruf über stdin: Pfad im Odoo.sh-Repository
    BASE_DIR = os.path.expanduser("~/src/user/docs/blistercenter_testdaten")
if not os.path.isdir(BASE_DIR):
    BASE_DIR = os.environ.get("BC_TESTDATEN_DIR", BASE_DIR)

IMAGE_URL = "https://cdn.shop-apotheke.com/images/256x0/D{a}/{b}/{c}/D{pzn}-p10.jpg"

# PZN, Bezeichnung, Packung, Lagerung, Testpreis Verkauf, Testpreis Einkauf
ARTICLES = [
    ("07560038", "BRILIQUE 90 mg Filmtabletten", "100 St", "ambient", 120.00, 96.00),
    ("11366951", "BRILIQUE 60 mg Filmtabletten", "60 St", "ambient", 80.00, 64.00),
    ("16015541", "BYDUREON 2 mg Depot-Injektionssuspension i.e. Fertigpen BCise", "", "cool", 150.00, 120.00),
    ("00839027", "BYETTA 10 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", 110.00, 88.00),
    ("00837270", "BYETTA 5 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", 100.00, 80.00),
    ("18063573", "CALQUENCE 100 mg Filmtabletten", "60 St", "ambient", 500.00, 400.00),
    ("13475319", "DAXAS 250 Mikrogramm Tabletten", "28 St", "ambient", 40.00, 32.00),
    ("13570710", "BEVESPI Aerosphere 7,2 µg/5 µg Druckgasinhalation 120 Hub", "1 St", "ambient", 60.00, 48.00),
    ("13592290", "AERODUR Turbohaler ohne Treibgas 120 ED", "", "ambient", 30.00, 24.00),
    ("04424530", "BAMBEC Tabletten", "", "ambient", 20.00, 16.00),
]


def gtin_from_pzn(pzn):
    body = "04150" + pzn
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


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

    # Logo
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

    # Blistercenter-Lager (Quarantäne, BCIN, BCFRG)
    env["blistercenter.config"]._get(company)._bc_setup()

    # Hersteller
    Partner = env["res.partner"]
    maker = Partner.search([("name", "=", "AstraZeneca GmbH (Test)")], limit=1)
    maker_vals = {"name": "AstraZeneca GmbH (Test)", "is_company": True, "bc_is_supplier": True,
                  "city": "Hamburg", "country_id": env.ref("base.de").id}
    maker = maker.write(maker_vals) and maker if maker else Partner.create(maker_vals)

    # Shop-Kategorie
    categ = False
    if "product.public.category" in env:
        Categ = env["product.public.category"]
        categ = Categ.search([("name", "=", "Arzneimittel")], limit=1) or Categ.create({"name": "Arzneimittel"})

    Product = env["product.product"]
    has_pzn = "plcs_pzn" in Product._fields
    has_temp = "plcs_storage_temp" in Product._fields
    images = 0
    for pzn, name, pack, storage, price, cost in ARTICLES:
        full_name = "%s %s" % (name, pack) if pack else name
        gtin = gtin_from_pzn(pzn)
        vals = {
            "name": full_name,
            "default_code": pzn,
            "barcode": gtin,
            "type": "consu",
            "is_storable": True,
            "tracking": "lot",
            "use_expiration_date": True,
            "sale_ok": True,
            "purchase_ok": True,
            "list_price": price,
            "standard_price": cost,
            "description_sale": "Testartikel (PZN %s). Preis fiktiv.%s" % (
                pzn, " Kühl lagern (2–8 °C)." if storage == "cool" else ""),
        }
        if has_pzn:
            vals["plcs_pzn"] = pzn
        if has_temp:
            vals["plcs_storage_temp"] = storage
        if "is_published" in Product._fields:
            vals["is_published"] = True
        if categ:
            vals["public_categ_ids"] = [(6, 0, categ.ids)]
        product = Product.search([("default_code", "=", pzn)], limit=1)
        if product:
            product.write(vals)
        else:
            product = Product.create(vals)
        if not product.seller_ids.filtered(lambda s: s.partner_id == maker):
            env["product.supplierinfo"].create({
                "partner_id": maker.id, "product_tmpl_id": product.product_tmpl_id.id,
                "price": cost, "min_qty": 1, "delay": 3,
            })
        image = download(IMAGE_URL.format(a=pzn[:2], b=pzn[2:5], c=pzn[5:], pzn=pzn))
        if image:
            product.image_1920 = image
            images += 1
        print("  %s  %s" % (pzn, full_name))
    report.append("%s Artikel, %s mit Foto" % (len(ARTICLES), images))

    # Blistercenter-Testkunde
    customer = Partner.search([("name", "=", "Blistercenter Musterstadt (Test)")], limit=1)
    cust_vals = {"name": "Blistercenter Musterstadt (Test)", "is_company": True, "bc_is_customer": True,
                 "street": "Musterstraße 1", "zip": "66687", "city": "Wadern",
                 "country_id": env.ref("base.de").id, "email": "bestellung@blistercenter.example"}
    if customer:
        customer.write(cust_vals)
    else:
        customer = Partner.create(cust_vals)
    customer.write({"bc_payment_model": "prepayment", "bc_shop_enabled": True})
    if not customer.child_ids.filtered(lambda c: c.name == "Erika Muster (Test)"):
        Partner.create({"name": "Erika Muster (Test)", "parent_id": customer.id, "type": "contact",
                        "email": "erika.muster@blistercenter.example", "function": "Einkauf"})
    report.append("Hersteller und Testkunde angelegt")

    env.cr.commit()
    print("Blistercenter-Testdaten: " + "; ".join(report))


run(env)  # noqa: F821
