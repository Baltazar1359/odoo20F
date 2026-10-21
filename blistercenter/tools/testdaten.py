"""Blistercenter-Testdaten (1.3.0): eine Quelle für die Demodaten des Moduls und das Testdaten-Skript.

Demodaten werden nur in Datenbanken mit Demodaten geladen (z. B. Odoo.sh-Dev-Builds), nie in
Production. Das Skript ``docs/blistercenter_testdaten/testdaten.py`` nutzt dieselben Listen und
ergänzt Logo und Produktfotos.

Shop-Sortiment (Entscheidung Mehrdad 20.10.2026, Tagrisso entfernt 21.10.2026): Forxiga, Xigduo, Daxas, Brilique.

Preise (Entscheidung 21.10.2026): Verkaufspreis bleibt der HAP (Rabatt 0/1/2 % darauf, R34), gerechnet aus dem
aktuellen AVP; der AVP steht zusätzlich am Artikel (Feld „AVP“) und in der Shop-Beschreibung.
"""


def hap_from_avp(avp):
    """HAP (Abgabepreis des Herstellers) aus dem AVP eines verschreibungspflichtigen Fertigarzneimittels.

    Preisbildung nach AMPreisV (ABDA, Zahlen Daten Fakten 2026, Stand Mai 2026):
    AEP = HAP + 3,15 % (höchstens 37,80 €) + 0,73 €;
    AVP = (AEP + 3 % + 8,35 € + 0,21 € Notdienst + 0,20 € pDL) + 19 % USt.
    Rückrechnung, auf Cent gerundet (Abweichung ±1 Cent möglich). Für den Echtbetrieb HAP von PLCS.
    """
    net = avp / 1.19
    aep = (net - 8.35 - 0.21 - 0.20) / 1.03
    hap = (aep - 0.73) / 1.0315
    if hap * 0.0315 > 37.80:
        hap = aep - 0.73 - 37.80
    return round(hap, 2)


# PZN, Bezeichnung, Packung, Lagerung, AVP (Shop Apotheke, abgerufen 21.10.2026; None = außer Handel /
# kein Preis gefunden), Testpreis HAP falls kein AVP. Einkaufspreis fiktiv: HAP − 5 %.
ARTICLES_RAW = [
    ("07560038", "BRILIQUE 90 mg Filmtabletten", "100 St", "ambient", 168.87, None),
    ("11366951", "BRILIQUE 60 mg Filmtabletten", "60 St", "ambient", 106.15, None),
    ("16015541", "BYDUREON 2 mg Depot-Injektionssuspension i.e. Fertigpen BCise", "", "cool", None, 150.00),
    ("00839027", "BYETTA 10 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", None, 110.00),
    ("00837270", "BYETTA 5 µg Injektionslösung i.e. Fertigpen", "1 St", "cool", None, 100.00),
    ("18063573", "CALQUENCE 100 mg Filmtabletten", "60 St", "ambient", 6181.90, None),
    ("13475319", "DAXAS 250 Mikrogramm Tabletten", "28 St", "ambient", 72.45, None),
    ("13570710", "BEVESPI Aerosphere 7,2 µg/5 µg Druckgasinhalation 120 Hub", "1 St", "ambient", 99.70, None),
    ("13592290", "AERODUR Turbohaler ohne Treibgas 120 ED", "", "ambient", 24.69, None),
    ("04424530", "BAMBEC Tabletten", "", "ambient", None, 20.00),
    # Häufigste Blistercenter-Artikel (alle ambient)
    ("10330224", "FORXIGA 5 mg Filmtabletten", "28 St (N2)", "ambient", 76.41, None),
    ("16033349", "FORXIGA 5 mg Filmtabletten", "98 St (N3)", "ambient", 237.21, None),
    ("10330193", "FORXIGA 10 mg Filmtabletten", "28 St (N2)", "ambient", 76.41, None),
    ("10330201", "FORXIGA 10 mg Filmtabletten", "98 St (N3)", "ambient", 237.21, None),
    ("10126297", "XIGDUO 5 mg/850 mg Filmtabletten", "56 St (N2)", "ambient", 54.22, None),
    ("10126564", "XIGDUO 5 mg/850 mg Filmtabletten", "196 St (N3)", "ambient", 159.53, None),
    ("10126587", "XIGDUO 5 mg/1000 mg Filmtabletten", "56 St (N2)", "ambient", 54.22, None),
    ("10126593", "XIGDUO 5 mg/1000 mg Filmtabletten", "196 St (N3)", "ambient", 159.53, None),
    ("12743321", "TAGRISSO 40 mg Filmtabletten", "30 St", "ambient", 5838.52, None),
    ("12743338", "TAGRISSO 80 mg Filmtabletten", "30 St", "ambient", 5838.52, None),
]

# PZN, Bezeichnung, Packung, Lagerung, Verkaufspreis (= HAP), Einkaufspreis, AVP (oder None)
ARTICLES = [
    (pzn, name, pack, storage, hap_from_avp(avp) if avp else price,
     round((hap_from_avp(avp) if avp else price) * 0.95, 2) if avp else round(price * 0.8, 2), avp)
    for pzn, name, pack, storage, avp, price in ARTICLES_RAW
]

# Shop-Sortiment: nur diese 5 Medikamente (erstes Wort der Bezeichnung), Reihenfolge = Shop-Kategorien
SHOP_FAMILIES = ["FORXIGA", "XIGDUO", "DAXAS", "BRILIQUE"]

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
    for pzn, name, pack, storage, price, cost, avp in ARTICLES:
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
            "bc_avp": avp or 0.0,
            "description_sale": "PZN %s. %s%s" % (
                pzn,
                ("AVP %s € (Apothekenverkaufspreis inkl. 19 %% USt)." % ("%.2f" % avp).replace(".", ","))
                if avp else "Außer Handel bzw. ohne AVP – Preis fiktiv.",
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

    if categs:
        # Andere Shop-Kategorien (z. B. Odoo-Demo „Desks“, „Furniture“) ohne Sortimentsartikel aus der
        # Kategorieleiste nehmen. Kunden sehen leere Kategorien ohnehin nicht, interne Benutzer schon.
        Categ = env["product.public.category"]
        ours = Categ.browse([c.id for c in categs.values()])
        for categ in Categ.search([("id", "not in", ours.ids), ("parent_id", "=", False)]):
            if not env["product.template"].search_count([
                    ("public_categ_ids", "child_of", categ.id), ("bc_shop_product", "=", True)]):
                categ.not_in_shop = True
        ours.write({"not_in_shop": False})

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


OWN_MODULES = ("blistercenter", "plcs_greenrx", "plcs_layout")


def archive_foreign_demo_products(env):
    """Odoo-Demoprodukte ohne Bezug zu PLCS (Tische, Stühle, Möbel, Hotel …) archivieren (1.4.0).

    Nur Artikel aus den Demodaten anderer Module (erkennbar an ihrer XML-ID). Aktiv bleiben die
    Artikel, die Odoo intern braucht: Versandarten und das Anzahlungsprodukt. Archivieren statt
    löschen: Demo-Aufträge verweisen auf die Artikel. Gibt die archivierten Vorlagen zurück.
    """
    Template = env["product.template"].with_context(active_test=False)
    data = env["ir.model.data"].sudo().search([
        ("model", "in", ("product.template", "product.product")),
        ("module", "not in", OWN_MODULES),
    ])
    template_ids = set(data.filtered(lambda d: d.model == "product.template").mapped("res_id"))
    variants = env["product.product"].with_context(active_test=False).browse(
        data.filtered(lambda d: d.model == "product.product").mapped("res_id")).exists()
    template_ids |= set(variants.product_tmpl_id.ids)
    protected = Template.browse()
    if "delivery.carrier" in env:
        protected |= env["delivery.carrier"].with_context(active_test=False).search([]).product_id.product_tmpl_id
    deposit_id = env["ir.config_parameter"].sudo().get_str("sale.default_deposit_product_id")
    if deposit_id and deposit_id.isdigit():
        protected |= env["product.product"].with_context(active_test=False).browse(int(deposit_id)).exists().product_tmpl_id
    templates = Template.browse(list(template_ids)).exists().filtered(
        lambda t: t.active and t not in protected and not t.bc_shop_product
        and not ("plcs_pzn" in t._fields and t.plcs_pzn))
    templates.write({"active": False, "is_published": False} if "is_published" in templates._fields
                    else {"active": False})
    return templates
