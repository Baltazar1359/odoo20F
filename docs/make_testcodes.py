"""Erzeugt einen Testbogen (PDF) für einen GreenRX-Retourenvorgang.

Inhalt: Retourenschein-QR, Box-Codes und securPharm-Codes (DataMatrix, GS1 mit
GS-Trennzeichen) für alle angemeldeten Packungen, dazu zwei Fehlerfälle.
Die Vorgänge und Artikel kommen aus import/greenrx_vorgaenge_letzte100.csv und
import/greenrx_artikel_pzn.csv.

Aufruf:
    python3 docs/make_testcodes.py                         # V202601693, GX001–GX003
    python3 docs/make_testcodes.py V202601607 --box 4      # anderer Vorgang, Boxen ab GX004

Seriennummern sind Testdaten (Präfix TST) und je Lauf neu, damit sie sich nicht
mit früheren Durchläufen überschneiden. Jede Seriennummer lässt sich in Odoo nur
einmal erfassen.
"""
import argparse
import csv
import os
import time

import zxingcpp  # pip install zxing-cpp
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Repo-Wurzel
GS = "\x1d"
EXPIRY_OK = "280331"      # 31.03.2028
EXPIRY_SHORT = "261130"   # 30.11.2026 – unter 3 Monaten Restlaufzeit


def load_vorgang(reference):
    path = os.path.join(ROOT, "import", "greenrx_vorgaenge_letzte100.csv")
    lines, head, current = [], None, None
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            current = row["name"] or current
            if current != reference:
                continue
            if row["name"]:
                head = row
            lines.append({
                "ticket": row["line_ids/ticket_number"] or "-",
                "pzn": row["line_ids/pzn"].zfill(8),
                "name": row["line_ids/product_name"],
                "batch": row["line_ids/batch_expected"] or "TEST1",
                "qty": int(row["line_ids/qty_expected"] or 1),
            })
    if not head:
        raise SystemExit(f"Vorgang {reference} nicht in der Importdatei gefunden.")
    return head, lines


def load_gtins():
    path = os.path.join(ROOT, "import", "greenrx_artikel_pzn.csv")
    with open(path, encoding="utf-8") as f:
        return {r["plcs_pzn"]: r["barcode"] for r in csv.DictReader(f)}


def gs1(gtin, expiry, batch, serial):
    return f"01{gtin}17{expiry}10{batch}{GS}21{serial}"


def draw_code(c, kind, value, x, y, size):
    fmt = zxingcpp.BarcodeFormat.QRCode if kind == "QR" else zxingcpp.BarcodeFormat.DataMatrix
    img = Image.fromarray(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(value, fmt), scale=1))
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)
    c.drawImage(ImageReader(img), x, y, size, size)


def short(name, n=30):
    return name if len(name) <= n else name[:n - 1] + "…"


def build(reference, first_box, out):
    head, lines = load_vorgang(reference)
    gtins = load_gtins()
    run = time.strftime("%m%d%H%M")
    slip = "\n".join(
        [f"REF:{reference}", f"REKL:{head['complaint_number']}", "TICKET|PZN|CHARGE|ANZAHL"]
        + [f"{l['ticket']}|{l['pzn']}|{l['batch']}|{l['qty']}" for l in lines])
    boxes = [f"GX{first_box + i:03d}" for i in range(len(lines) + 2)]

    packs, n = [], 0
    for li, l in enumerate(lines):
        gtin = gtins[l["pzn"]]
        for k in range(l["qty"]):
            n += 1
            packs.append((f"{short(l['name'], 24)} {k + 1}/{l['qty']}",
                          gs1(gtin, EXPIRY_OK, l["batch"], f"TST{run}{n:03d}"), f"Box {boxes[li]}"))
    l0 = lines[0]
    slip_pzns = {l["pzn"] for l in lines}
    other_pzn = next(p for p in gtins if p not in slip_pzns)
    errors = [
        ("Fehlerfall: andere Charge (X999)", gs1(gtins[l0["pzn"]], EXPIRY_OK, "X999", f"TST{run}901"),
         f"In {boxes[0]} scannen: wird abgelehnt (eine Box = eine Charge)."),
        ("Fehlerfall: Verfall in 7 Wochen", gs1(gtins[l0["pzn"]], EXPIRY_SHORT, l0["batch"], f"TST{run}902"),
         f"In {boxes[-2]}: Restlaufzeit zu kurz, Vorschlag Rot (mit Sperrblatt)."),
        ("Fehlerfall: Artikel nicht auf dem Schein", gs1(gtins[other_pzn], EXPIRY_OK, "Z123", f"TST{run}903"),
         f"In {boxes[-1]}: nicht angemeldet, Vorschlag Gelb."),
    ]

    os.makedirs(os.path.dirname(out), exist_ok=True)
    c = canvas.Canvas(out, pagesize=A4)
    c.setTitle(f"GreenRX – Testcodes {reference}")
    w, h = A4

    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, h - 22 * mm, f"GreenRX – Testcodes {reference}")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 28 * mm, "Nur für die Prüfumgebung. Packungscodes sind Testdaten, keine echten "
                                        "securPharm-Seriennummern.")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 42 * mm, f"1. Retourenschein {reference} (Reklamation {head['complaint_number']})")
    draw_code(c, "QR", slip, 20 * mm, h - 100 * mm, 52 * mm)
    c.setFont("Helvetica", 8.5)
    ty = h - 52 * mm
    for line in slip.split("\n"):
        c.drawString(80 * mm, ty, line)
        ty -= 4.2 * mm
    ty -= 3 * mm
    for text in (f"Absender: {head['partner_name']} (Kd.-Nr. {head['customer_number']})",
                 *[f"Ticket {l['ticket']}: {short(l['name'], 45)} – {l['qty']} Pck., Charge {l['batch']}"
                   for l in lines]):
        c.drawString(80 * mm, ty, text)
        ty -= 4.2 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 115 * mm, "2. Boxen")
    for i, box in enumerate(boxes[:4]):
        x = 20 * mm + i * 44 * mm
        draw_code(c, "QR", box, x, h - 155 * mm, 32 * mm)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 8 * mm, h - 161 * mm, box)
    c.setFont("Helvetica", 8.5)
    plan = ", ".join(f"{boxes[i]} für Ticket {l['ticket']}" for i, l in enumerate(lines))
    c.drawString(20 * mm, h - 180 * mm, f"Empfehlung: {plan}, {boxes[-2]} und {boxes[-1]} für die Fehlerfälle.")
    c.drawString(20 * mm, h - 185 * mm, "Die Boxen müssen leer sein. Belegte Boxen lehnt Odoo ab.")
    c.showPage()

    items = packs + errors
    per_page, cols, size = 9, 3, 34 * mm
    for start in range(0, len(items), per_page):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, h - 20 * mm, "3. Packungen (securPharm-DataMatrix, GS1)")
        for idx, (title, value, note) in enumerate(items[start:start + per_page]):
            col, row = idx % cols, idx // cols
            x = 20 * mm + col * 60 * mm
            y = h - 70 * mm - row * 75 * mm
            draw_code(c, "DM", value, x, y, size)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(x, y - 5 * mm, title)
            c.setFont("Helvetica", 6.5)
            first, _, second = value.replace(GS, "<GS>").partition("<GS>")
            c.drawString(x, y - 9 * mm, first + "<GS>")
            c.drawString(x, y - 12.5 * mm, second)
            words, line, ty = note.split(), "", y - 17 * mm
            for word in words:
                if len(line) + len(word) > 42:
                    c.drawString(x, ty, line)
                    line, ty = "", ty - 3.5 * mm
                line = (line + " " + word).strip()
            if line:
                c.drawString(x, ty, line)
        c.showPage()
    c.save()
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("vorgang", nargs="?", default="V202601693")
    parser.add_argument("--box", type=int, default=1, help="erste Box-Nummer (Standard 1 = GX001)")
    parser.add_argument("--out", help="Ausgabedatei (Standard docs/testcodes_<Vorgang>.pdf)")
    args = parser.parse_args()
    out = args.out or os.path.join(ROOT, "docs", f"testcodes_{args.vorgang}.pdf")
    print(build(args.vorgang, args.box, out))


if __name__ == "__main__":
    main()
