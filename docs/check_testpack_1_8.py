"""Prüft das Testpaket 1.8.0 automatisch: Codes im PDF lesbar und Testplan T1–T9 verhält sich wie beschrieben.

Nur für eine lokale Test-Datenbank, nicht für Production (legt Benutzer, Artikel und Buchungen an).

Aufruf (Datenbank mit installiertem plcs_greenrx):
    TESTPACK=docs/testpaket_VTEST1801 odoo-bin shell -c odoo.conf -d <db> --no-http < docs/check_testpack_1_8.py

Ablauf wie in der Oberfläche: dieselben Serverfunktionen, die die Scan-App aufruft, mit den Benutzern
lager.test, vp.test und Administrator. Bei der ersten Abweichung bricht das Skript mit einer Meldung ab.
Die Oberflächen-Teile von T1 (schnelle Scans + F9, Paketübersicht) prüft der Browser-Test
TestGreenrxScanQueue.test_scans_before_next_step_all_arrive.
"""
import csv
import json
import os
import subprocess
import tempfile

import zxingcpp
from PIL import Image

from odoo.exceptions import AccessError, UserError

base = os.environ.get("TESTPACK", "docs/testpaket_VTEST1801")
with open(base + ".json", encoding="utf-8") as f:
    d = json.load(f)
results = []


def ok(case, text):
    results.append((case, text))
    print("  OK  %s: %s" % (case, text))


def expect(cond, case, text):
    if not cond:
        raise SystemExit("FEHLER %s: %s" % (case, text))
    ok(case, text)


def expect_error(case, text, fn, contains, exc=UserError):
    try:
        fn()
    except exc as e:
        if contains not in str(e):
            raise SystemExit("FEHLER %s: %s – andere Meldung: %s" % (case, text, e))
        ok(case, "%s (Meldung: %s…)" % (text, str(e)[:70]))
        return
    raise SystemExit("FEHLER %s: %s – keine Fehlermeldung" % (case, text))


def guarded(fn):
    def run():
        with env.cr.savepoint():
            fn()
    return run


# ----------------------------------------------------------------------
# 0. Codes im PDF lesbar und identisch mit den Daten
# ----------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["pdftoppm", "-r", "200", "-png", base + ".pdf", os.path.join(tmp, "p")], check=True)
    decoded = set()
    for name in sorted(os.listdir(tmp)):
        for code in zxingcpp.read_barcodes(Image.open(os.path.join(tmp, name))):
            decoded.add(code.bytes.decode("utf-8"))  # Rohdaten: GS-Trennzeichen als ASCII 29
wanted = {d["slip"], *d["boxes"].values(), *(v for packs in d["packs"].values() for _t, v in packs)}
missing = wanted - decoded
expect(not missing, "Codes", "alle %s Codes im PDF lesbar (fehlend: %s)" % (len(wanted), missing or "-"))

# ----------------------------------------------------------------------
# Vorbereitung: Firma, Artikel wie in Production, Benutzer
# ----------------------------------------------------------------------
company = env.company
company._plcs_grx_setup()
Product = env["product.product"]
with open(os.path.join(os.path.dirname(base), "..", "import", "greenrx_artikel_pzn.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if not Product.search([("plcs_pzn", "=", row["plcs_pzn"])], limit=1):
            Product.create({"name": row["name"], "plcs_pzn": row["plcs_pzn"], "default_code": row["default_code"],
                            "barcode": row["barcode"], "is_storable": True, "tracking": "lot",
                            "use_expiration_date": True})
Users = env["res.users"]


def user(login, name, group):
    u = Users.search([("login", "=", login)], limit=1)
    if not u:
        u = Users.create({"login": login, "name": name, "group_ids": [(4, env.ref(group).id),
                                                                      (4, env.ref("stock.group_stock_user").id)]})
    return u


lager = user("lager.test", "Lager Test", "plcs_greenrx.group_grx_user")
vp = user("vp.test", "VP Test", "plcs_greenrx.group_grx_vp")
admin = env.ref("base.user_admin")
expect(admin.has_group("plcs_greenrx.group_grx_vp"), "Vorbereitung", "Administrator hat die VP-Rolle")
B = d["boxes"]
P = {case: [v for _t, v in packs] for case, packs in d["packs"].items()}
yes = {f: "ja" for f in ("q_damage", "q_temp_controlled", "q_temp_log", "q_manufacturer", "q_storage_proof", "q_seal")}
for name in B.values():
    pkg = env["stock.package"].search([("name", "=", name)])
    expect(pkg.plcs_grx_is_empty, "Vorbereitung", "Box %s leer" % name)
BoxL = env["plcs.greenrx.box"].with_user(lager)
BoxV = env["plcs.greenrx.box"].with_user(vp)
BoxA = env["plcs.greenrx.box"].with_user(admin)


def row(values, text):
    return next(r for r in values["rows_eingang"] if r["text"] == text)


def fill(Box, ticket, box_code, codes):
    box = Box.browse(Box.grx_open_box(ticket.id, box_code)["id"])
    out = [box.grx_scan(c) for c in codes]
    return box, out


def check_and_print(box, ampel, answers=None, note=None):
    box.grx_save_check(answers=answers or yes)
    box.grx_save_check(ampel=ampel, note=note)
    box.grx_to_print()
    box.grx_print()


# ----------------------------------------------------------------------
# T1
# ----------------------------------------------------------------------
data = env["plcs.greenrx.ticket"].with_user(lager).grx_from_slip(d["slip"])
expect(data["created_from_slip"], "T1", "Vorgang aus dem Retourenschein angelegt")
ticket = env["plcs.greenrx.ticket"].browse(data["id"])
expect(ticket.name == d["ref"] and len(ticket.line_ids) == 4, "T1", "Referenz %s, 4 Tickets" % d["ref"])
box1, res = fill(BoxL, ticket, B["T1"], P["T1"])
expect([r["status"] for r in res] == ["ok"] * 3 and box1.qty == 3, "T1", "3 BRILIQUE-Packungen in %s" % B["T1"])
last = box1.scan_ids.filtered("accepted").sorted("id")[-1]
ui = box1.grx_remove_scan(last.id)
expect(ui["qty"] == 2, "T1", "Packung 3/3 entfernt: 2 in der Box")
expect(box1.grx_scan(P["T1"][2])["status"] == "ok" and box1.qty == 3, "T1", "wieder gescannt: 3 in der Box")
box1.grx_to_check()
check_and_print(box1, "gruen")
v = box1._grx_report_values()
expect([row(v, t)["value"] for t in ("– PZN", "– Bezeichnung", "– Charge")] == ["ja"] * 3, "T1",
       "Formblatt: PZN, Bezeichnung, Charge = ja")
expect(v["checked_by"].startswith("Lager Test") and v["captured_by"] == "Lager Test", "T1",
       "Formblatt: geprüft von Lager Test, erfasst von Lager Test")
box1.grx_confirm_signed(True)
box1.grx_close()
expect(box1.state == "to_release", "T1", "Box abgeschlossen, wartet auf VP")

# ----------------------------------------------------------------------
# T2
# ----------------------------------------------------------------------
box2, res = fill(BoxL, ticket, B["T2"], P["T2"])
box2.grx_to_check()
box2.grx_save_check(answers=dict(yes, q_damage="nein"))
expect(box2.ampel_suggest == "gelb", "T2", "Vorschlag Gelb")
box2.grx_save_check(ampel="rot", note="")
ui = box2._grx_ui_data()
expect(ui["suggest"] == "gelb" and box2.ampel == "rot", "T2",
       "Oberfläche: Rot gewählt bei Vorschlag Gelb (Feld Grund wird Pflicht)")
expect_error("T2", "Rot ohne Grund: Weiter geht nicht", guarded(box2.grx_to_print), "strenger als der Vorschlag")
box2.grx_save_check(note="Umkarton nass")
box2.grx_to_print()
box2.grx_print()
expect(box2._grx_report_values()["pages"] == 3, "T2", "Druck mit rotem Sperrblatt (3 Seiten)")
box2.grx_confirm_signed(True)
box2.grx_close()
expect(box2.state == "blocked", "T2", "Box rot, gesperrt")

# ----------------------------------------------------------------------
# T3
# ----------------------------------------------------------------------
box3, res = fill(BoxL, ticket, B["T3"], P["T3"])
expect(res[0]["status"] == "warning" and "nicht auf dem Retourenschein" in res[0]["message"], "T3",
       "Warnung: Artikel steht nicht auf dem Retourenschein")
box3.grx_to_check()
box3.grx_save_check(answers=yes)
expect(box3.ampel_suggest == "gelb", "T3", "Vorschlag Gelb")
check_and_print(box3, "gelb")
v = box3._grx_report_values()
expect(v["rows_annahme"][0]["value"] == "nein", "T3", "Formblatt: Ware für uns = nein")
expect(all(row(v, t)["value"] == "nein" and "keine Position im Ticket" in row(v, t)["remark"]
           for t in ("– PZN", "– Bezeichnung", "– Charge")), "T3",
       "Formblatt: PZN, Bezeichnung, Charge = nein, „keine Position im Ticket“")
box3.grx_confirm_signed(True)
box3.grx_close()

# ----------------------------------------------------------------------
# T4
# ----------------------------------------------------------------------
box4, res = fill(BoxL, ticket, B["T4"], P["T4"])
box4.grx_to_check()
again = BoxV.grx_open_box(ticket.id, B["T4"])
expect(again["id"] == box4.id and again["state"] == "check", "T4", "vp.test öffnet die Box von lager.test (Vorprüfung)")
box4v = BoxV.browse(box4.id)
check_and_print(box4v, "gruen")
v = box4._grx_report_values()
expect(v["checked_by"].startswith("VP Test") and v["captured_by"] == "Lager Test", "T4",
       "Formblatt: geprüft von VP Test, erfasst von Lager Test")
box4v.grx_confirm_signed(True)
box4v.grx_close()

# ----------------------------------------------------------------------
# T5
# ----------------------------------------------------------------------
box5, res = fill(BoxL, ticket, B["T5"], P["T5"])
box5.grx_to_check()
check_and_print(box5, "gruen")
box5.grx_confirm_signed(True)
lokelma = Product.search([("plcs_pzn", "=", "16020967")], limit=1).with_user(admin)
old_temp = lokelma.plcs_storage_temp
lokelma.plcs_storage_temp = "cool" if old_temp != "cool" else "ambient"
expect_error("T5", "Abschluss nach Stammdatenänderung abgelehnt", guarded(box5.grx_close),
             "nach dem letzten Druck geändert")
box5.grx_print()
expect(not box5.signed_confirmed and box5.print_count == 2, "T5", "Neuausdruck Nr. 1, Bestätigung zurückgesetzt")
box5.grx_confirm_signed(True)
box5.grx_close()
expect(box5.state == "to_release", "T5", "nach Neudruck abgeschlossen")
lokelma.plcs_storage_temp = old_temp

# ----------------------------------------------------------------------
# T6
# ----------------------------------------------------------------------
box6, res = fill(BoxL, ticket, B["T6"], [P["T1"][0]])
expect(res[0]["status"] == "error" and res[0]["title"] == "Diese Packung ist schon im System"
       and B["T1"] in res[0]["message"], "T6", "doppelte Packung abgelehnt mit Hinweis auf %s" % B["T1"])
box6.grx_cancel()
expect(box6.state == "cancel", "T6", "Box %s abgebrochen" % B["T6"])

# ----------------------------------------------------------------------
# T7
# ----------------------------------------------------------------------
T = ticket.with_user(lager)
expect_error("T7", "Paket fertig ohne Begründung abgelehnt", guarded(T.grx_finish), "Begründung")
T.grx_finish(deviation_note="KOMBOGLYZE nicht angemeldet, Klärung mit ATIV")
expect(ticket.state == "done" and ticket.picking_id.state == "done", "T7", "Eingang gebucht, Vorgang erledigt")

# ----------------------------------------------------------------------
# T8
# ----------------------------------------------------------------------
pkg1 = box1.package_id.with_user(admin)
expect_error("T8", "Auspacken von %s abgelehnt" % B["T1"], guarded(pkg1.unpack), "ausgepackt oder umgepackt")
lot = box1.lot_ids[:1].with_user(admin)
expect_error("T8", "Seriennummer umbenennen abgelehnt", guarded(lambda: lot.write({"name": "GEAENDERT"})),
             "kann nicht geändert werden")

# ----------------------------------------------------------------------
# T9
# ----------------------------------------------------------------------
lst = BoxV.grx_vp_list()
by_box = {b["box"]: b for b in lst["boxes"]}
expect(by_box[B["T4"]]["can_release"] is False and by_box[B["T1"]]["can_release"], "T9",
       "%s „nur sperren“ für vp.test, %s freigebbar" % (B["T4"], B["T1"]))
BoxV.browse(box1.id).grx_vp_release()
expect_error("T9", "Gelbe Box ohne Begründung abgelehnt", guarded(lambda: BoxV.browse(box3.id).grx_vp_release()),
             "Begründung")
BoxV.browse(box3.id).grx_vp_release(note="Rücksprache ATIV: Ware gehört zum Vorgang")
expect_error("T9", "vp.test darf %s nicht freigeben" % B["T4"],
             guarded(lambda: BoxV.browse(box4.id).grx_vp_release()), "4-Augen", exc=AccessError)
BoxV.browse(box5.id).grx_vp_release()
BoxA.browse(box4.id).grx_vp_release()
stock = company.plcs_grx_stock_location_id
blocked = company.plcs_grx_blocked_location_id
for box in (box1, box3, box4, box5):
    box.invalidate_recordset()
expect(all(b.state == "released" and b.location_id == stock for b in (box1, box3, box4, box5)), "T9",
       "%s, %s, %s, %s in %s" % (B["T1"], B["T3"], B["T4"], B["T5"], stock.display_name))
box2.invalidate_recordset()
expect(box2.state == "blocked" and box2.location_id == blocked, "T9", "%s im %s" % (B["T2"], blocked.display_name))

print("\nTestpaket %s: %s Prüfungen bestanden." % (d["ref"], len(results)))
env.cr.rollback()
