"""Blistercenter-Testdaten (1.3.0): eine Quelle für die Demodaten des Moduls und das Testdaten-Skript.

Demodaten werden nur in Datenbanken mit Demodaten geladen (z. B. Odoo.sh-Dev-Builds), nie in
Production. Das Skript ``docs/blistercenter_testdaten/testdaten.py`` nutzt dieselben Listen und
ergänzt Logo und Produktfotos.

Shop-Sortiment (Entscheidung Mehrdad 20.10.2026): nur Forxiga, Xigduo, Daxas, Brilique, Tagrisso.
"""

# PZN, Bezeichnung, Packung, Lagerung, Verkaufspreis (= HAP), Testpreis Einkauf, Herkunft des Preises
# Herkunft: "fiktiv" = Testpreis; "hap" = HAP aus dem AVP zurückgerechnet (Recherche 19.10.2026)
ARTICLES = [
    ("07560038", "BRILIQUE 90 mg Filmtabletten", "100 St", "ambient", 120.00, 96.00, "fiktiv"),
    ("11366951", "BRILIQUE 60 mg Filmtabletten", "60 St", "ambient", 80.00, 64.00, "fiktiv"),
    ("16015541", "BYDUREON 2 mg Depot-Injektionssuspension i.e. Fertigpen BCise", "", "cool", 150.00, 120.00, "fiktiv"),
    ("00839027", "BYETTA 10 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", 110.00, 88.00, "fiktiv"),
    ("00837270", "BYETTA 5 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", 100.00, 80.00, "fiktiv"),
    ("18063573", "CALQUENCE 100 mg Filmtabletten", "60 St", "ambient", 500.00, 400.00, "fiktiv"),
    ("13475319", "DAXAS 250 Mikrogramm Tabletten", "28 St", "ambient", 40.00, 32.00, "fiktiv"),
    ("13570710", "BEVESPI Aerosphere 7,2 µg/5 µg Druckgasinhalation 120 Hub", "1 St", "ambient", 60.00, 48.00, "fiktiv"),
    ("13592290", "AERODUR Turbohaler ohne Treibgas 120 ED", "", "ambient", 30.00, 24.00, "fiktiv"),
    ("04424530", "BAMBEC Tabletten", "", "ambient", 20.00, 16.00, "fiktiv"),
    # Häufigste Blistercenter-Artikel (alle ambient). Einkaufspreis fiktiv (HAP − 5 %).
    ("10330224", "FORXIGA 5 mg Filmtabletten", "28 St (N2)", "ambient", 50.87, 48.33, "hap"),
    ("16033349", "FORXIGA 5 mg Filmtabletten", "98 St (N3)", "ambient", 178.06, 169.16, "hap"),
    ("10330193", "FORXIGA 10 mg Filmtabletten", "28 St (N2)", "ambient", 50.87, 48.33, "hap"),
    ("10330201", "FORXIGA 10 mg Filmtabletten", "98 St (N3)", "ambient", 178.06, 169.16, "hap"),
    ("10126297", "XIGDUO 5 mg/850 mg Filmtabletten", "56 St (N2)", "ambient", 33.32, 31.65, "hap"),
    ("10126564", "XIGDUO 5 mg/850 mg Filmtabletten", "196 St (N3)", "ambient", 116.62, 110.79, "hap"),
    # 10126587: Preis nicht direkt gefunden, gleicher Preis wie 5/850 mg 56 St angenommen (vor Echtbetrieb prüfen)
    ("10126587", "XIGDUO 5 mg/1000 mg Filmtabletten", "56 St (N2)", "ambient", 33.32, 31.65, "hap"),
    ("10126593", "XIGDUO 5 mg/1000 mg Filmtabletten", "196 St (N3)", "ambient", 116.62, 110.79, "hap"),
    ("12743321", "TAGRISSO 40 mg Filmtabletten", "30 St", "ambient", 4715.75, 4479.96, "hap"),
    ("12743338", "TAGRISSO 80 mg Filmtabletten", "30 St", "ambient", 4715.75, 4479.96, "hap"),
]

# Shop-Sortiment: nur diese 5 Medikamente (erstes Wort der Bezeichnung), Reihenfolge = Shop-Kategorien
SHOP_FAMILIES = ["FORXIGA", "XIGDUO", "DAXAS", "BRILIQUE", "TAGRISSO"]

# Blistercenter-Testkunden: Name, Rabatt auf HAP, Zahlungsziel (XML-ID), Zahlungsart
CUSTOMERS = [
    ("Blistercenter Musterstadt (Test)", "0", "blistercenter.payment_term_14days", "direct_debit"),
    ("Blistercenter Rabatt 1 % (Test)", "1", "blistercenter.payment_term_7days", "direct_debit"),
    ("Blistercenter Rabatt 2 % (Test)", "2", "blistercenter.payment_term_14days", "transfer"),
]

MAKER_NAME = "AstraZeneca GmbH (Test)"


def gtin_from_pzn(pzn):
    body = "04150" + pzn
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return body + str((10 - total % 10) % 10)


def family_of(name):
    return name.split()[0].upper()


def load(env, log=None):
    """Hersteller, Artikel (Shop-Sortiment + Kategorien) und Testkunden anlegen oder aktualisieren.

    Beliebig oft ausführbar, keine Duplikate. Vorhandene Artikel (z. B. aus GreenRX/ATIV)
    werden über PZN, interne Referenz oder GTIN gefunden und ergänzt.
    Gibt {pzn: product.product} zurück.
    """
    log = log or (lambda msg: None)
    company = env.company
    config = env["blistercenter.config"]._get(company)
    config._bc_setup()
    config.rule_shop_only_bc_products = True

    Partner = env["res.partner"]
    maker = Partner.search([("name", "=", MAKER_NAME)], limit=1)
    maker_vals = {"name": MAKER_NAME, "is_company": True, "bc_is_supplier": True,
                  "city": "Hamburg", "country_id": env.ref("base.de").id}
    if maker:
        maker.write(maker_vals)
    else:
        maker = Partner.create(maker_vals)

    categs = {}
    if "product.public.category" in env:
        Categ = env["product.public.category"]
        for seq, family in enumerate(SHOP_FAMILIES, start=1):
            label = family.capitalize()
            categ = Categ.search([("name", "=", label)], limit=1)
            if categ:
                categ.sequence = seq
            else:
                categ = Categ.create({"name": label, "sequence": seq})
            categs[family] = categ

    Product = env["product.product"]
    has_pzn = "plcs_pzn" in Product._fields
    has_temp = "plcs_storage_temp" in Product._fields
    has_website = "is_published" in Product._fields
    products = {}
    for pzn, name, pack, storage, price, cost, origin in ARTICLES:
        full_name = "%s %s" % (name, pack) if pack else name
        gtin = gtin_from_pzn(pzn)
        family = family_of(name)
        in_shop = family in SHOP_FAMILIES
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
            "invoice_policy": "delivery",
            "bc_shop_product": in_shop,
            "description_sale": "Testartikel (PZN %s). %s%s" % (
                pzn,
                "HAP aus dem Apothekenverkaufspreis gerechnet (Testwert, nicht Lauer-Taxe)."
                if origin == "hap" else "Preis fiktiv.",
                " Kühl lagern (2–8 °C)." if storage == "cool" else ""),
        }
        if has_pzn:
            vals["plcs_pzn"] = pzn
        if has_temp:
            vals["plcs_storage_temp"] = storage
        if has_website:
            vals["is_published"] = in_shop
        if categs:
            vals["public_categ_ids"] = [(6, 0, categs[family].ids)] if in_shop else [(5, 0, 0)]
        domain = ["|", ("default_code", "=", pzn), ("barcode", "=", gtin)]
        if has_pzn:
            domain = ["|"] + domain + [("plcs_pzn", "=", pzn)]
        product = Product.search(domain, limit=1)
        if product:
            product.write(vals)
        else:
            product = Product.create(vals)
        if not product.seller_ids.filtered(lambda s: s.partner_id == maker):
            env["product.supplierinfo"].create({
                "partner_id": maker.id, "product_tmpl_id": product.product_tmpl_id.id,
                "price": cost, "min_qty": 1, "delay": 3,
            })
        products[pzn] = product
        log("  %s  %s%s" % (pzn, full_name, "  [Shop]" if in_shop else ""))

    for index, (cust_name, discount, term_xmlid, payment_model) in enumerate(CUSTOMERS, start=1):
        customer = Partner.search([("name", "=", cust_name)], limit=1)
        cust_vals = {"name": cust_name, "is_company": True, "bc_is_customer": True,
                     "street": "Musterstraße %s" % index, "zip": "66687", "city": "Wadern",
                     "country_id": env.ref("base.de").id,
                     "email": "bestellung%s@blistercenter.example" % index}
        if customer:
            customer.write(cust_vals)
        else:
            customer = Partner.create(cust_vals)
        customer.write({
            "bc_payment_model": payment_model, "bc_shop_enabled": True, "bc_discount": discount,
            "property_payment_term_id": env.ref(term_xmlid).id,
        })
        contact = "Erika Muster (Test)" if index == 1 else "Kontakt %s (Test)" % index
        if not customer.child_ids.filtered(lambda c, n=contact: c.name == n):
            Partner.create({"name": contact, "parent_id": customer.id, "type": "contact",
                            "email": "kontakt%s@blistercenter.example" % index, "function": "Einkauf"})
        log("  Kunde: %s – Rabatt %s %%, %s" % (cust_name, discount, env.ref(term_xmlid).name))
    return products
