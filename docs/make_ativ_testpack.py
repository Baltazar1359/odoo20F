"""Testunterlagen für ausgewählte ATIV-Gutschriften (Wareneingang GreenRX).

Erzeugt aus einer JSON-Auswahl (aus der ATIV-Testkopie gelesen) zwei PDFs:

1. Retourenscheine – je Gutschrift eine Seite mit Retourenschein-QR (REF/REKL/Positionen, wie
   tools/slip.py ihn liest) und einem Code-128-Barcode der Referenznummer. Damit findet der
   Wareneingang den Vorgang mit einem Scan.
2. Packungscodes – je Gutschrift die Box (QR) und je Packung ein securPharm-DataMatrix (GS1,
   echte GTIN, Charge und Verfall laut Ticket, Test-Seriennummer TST…).

Aufruf:
    python3 docs/make_ativ_testpack.py auswahl.json --out docs/ativ_testpack --box 101

Die JSON-Datei enthält Kundendaten und gehört nicht ins Repository.
Jede Seriennummer lässt sich in Odoo nur einmal erfassen; je Lauf entstehen neue Nummern.
"""
import argparse
import csv
import json
import os
import sys
import time

import zxingcpp  # pip install zxing-cpp
from PIL import Image
from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plcs_greenrx", "tools"))
import slip as slip_parser  # noqa: E402  (tools/slip.py, ohne Odoo nutzbar)

GS = "\x1d"


def load_gtins():
    with open(os.path.join(ROOT, "import", "greenrx_artikel_pzn.csv"), encoding="utf-8") as f:
        return {r["plcs_pzn"].zfill(8): r["barcode"] for r in csv.DictReader(f)}


def gs1(gtin, expiry, batch, serial):
    return f"01{gtin}17{expiry}10{batch}{GS}21{serial}"


def yymmdd(iso):
    y, m, d = iso.split("-")
    return y[2:] + m + d


def de_date(iso):
    y, m, d = iso[:10].split("-")
    return f"{d}.{m}.{y}"


def gs1_hri(value):
    """Rohwert 01…17…10…<GS>21… → Klartext (01)…(17)…(10)…(21)… für den GS1-Encoder."""
    head, serial = value.split(GS + "21")
    gtin, exp, batch = head[2:16], head[18:24], head[26:]
    return f"(01){gtin}(17){exp}(10){batch}(21){serial}"


def code_image(kind, value):
    if kind == "QR":
        barcode = zxingcpp.create_barcode(value, zxingcpp.BarcodeFormat.QRCode)
    else:
        # echtes GS1-DataMatrix (FNC1) wie auf securPharm-Packungen
        barcode = zxingcpp.create_barcode(gs1_hri(value), zxingcpp.BarcodeFormat.DataMatrix, gs1=True)
    img = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=1))
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)
    # Gegenprobe: der gedruckte Code muss genau den Inhalt liefern
    found = zxingcpp.read_barcodes(img.convert("L"))
    raw = found[0].bytes.decode("latin-1") if found else None
    if raw != value or (kind != "QR" and found[0].content_type != zxingcpp.ContentType.GS1):
        raise SystemExit(f"Code lässt sich nicht zurücklesen: {value!r} -> {raw!r}")
    return img


def draw_code(c, kind, value, x, y, size):
    c.drawImage(ImageReader(code_image(kind, value)), x, y, size, size)


def short(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


def slip_text(g):
    lines = [f"REF:{g['ref']}", f"REKL:{g['rekl'] or ''}", "TICKET|PZN|CHARGE|ANZAHL"]
    lines += [f"{l['ticket']}|{str(l['pzn']).zfill(8)}|{l['batch']}|{int(l['qty'])}" for l in g["lines"]]
    return "\n".join(lines)


def check_slip(g, text):
    data = slip_parser.parse(text)
    assert data["reference"] == g["ref"], data
    assert len(data["lines"]) == len(g["lines"]), data
    for parsed, line in zip(data["lines"], g["lines"]):
        assert parsed["ticket"] == line["ticket"] and parsed["qty"] == int(line["qty"]), (parsed, line)


def build_slips(selection, path):
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle("GreenRX – Retourenscheine (Test)")
    w, h = A4
    for g in selection:
        text = slip_text(g)
        check_slip(g, text)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(20 * mm, h - 25 * mm, "Retourenschein GreenRX")
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.75, 0, 0)
        c.drawString(20 * mm, h - 31 * mm, "TESTAUSDRUCK – nur für die Prüfung des Wareneingangs bei PLCS")
        c.setFillColorRGB(0, 0, 0)
        draw_code(c, "QR", text, w - 75 * mm, h - 85 * mm, 55 * mm)
        c.setFont("Helvetica", 8)
        c.drawCentredString(w - 47.5 * mm, h - 89 * mm, "Im Wareneingang diesen QR-Code scannen")

        rows = [
            ("Referenz (Vorgang)", g["ref"]),
            ("Gutschrift", g["gs"]),
            ("Reklamation", g["rekl"] or "–"),
            ("Absender", g["kunde"]),
            ("Kundennummer", str(g["kdnr"])),
            ("Ort", g["ort"] or "–"),
            ("Datum Gutschrift", de_date(g["datum"])),
        ]
        y = h - 45 * mm
        for label, value in rows:
            c.setFont("Helvetica", 9)
            c.drawString(20 * mm, y, label)
            c.setFont("Helvetica-Bold", 10 if len(str(value)) <= 34 else 8)
            c.drawString(58 * mm, y, short(value, 46))
            y -= 7 * mm

        bc = code128.Code128(g["ref"], barHeight=14 * mm, barWidth=0.38 * mm)
        bc.drawOn(c, 20 * mm, h - 115 * mm)
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, h - 120 * mm, f"Referenz {g['ref']} (Barcode, falls der QR-Code nicht lesbar ist)")

        y = h - 138 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "Angemeldete Produkte")
        y -= 8 * mm
        cols = [(20, "Ticket"), (42, "PZN"), (62, "Artikel"), (140, "Charge"), (160, "Verfall"), (182, "Pkg.")]
        c.setFont("Helvetica-Bold", 9)
        for x, label in cols:
            c.drawString(x * mm, y, label)
        c.line(20 * mm, y - 2 * mm, 195 * mm, y - 2 * mm)
        c.setFont("Helvetica", 9)
        total = 0
        for l in g["lines"]:
            y -= 7 * mm
            values = [l["ticket"], str(l["pzn"]).zfill(8), short(l["name"], 42), l["batch"],
                      de_date(l["exp"]) if l.get("exp") else "–", str(int(l["qty"]))]
            for (x, _), v in zip(cols, values):
                c.drawString(x * mm, y, v)
            total += int(l["qty"])
        y -= 4 * mm
        c.line(20 * mm, y, 195 * mm, y)
        y -= 6 * mm
        c.setFont("Helvetica-Bold", 9)
        c.drawString(140 * mm, y, "Gesamt")
        c.drawString(182 * mm, y, str(total))

        c.setFont("Helvetica", 8)
        c.drawString(20 * mm, 18 * mm, "Daten aus der ATIV-Testkopie (Gutschrift, Vorgang, Ticket). "
                                        "Kein echter Retourenschein.")
        c.showPage()
    c.save()


def build_packs(selection, path, first_box, gtins):
    run = time.strftime("%m%d%H%M")
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle("GreenRX – Packungscodes (Test)")
    w, h = A4
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, h - 22 * mm, "GreenRX – Packungscodes zu den 10 Test-Retourenscheinen")
    c.setFont("Helvetica", 9)
    intro = [
        "Packungscodes sind Testdaten (securPharm-Format GS1, echte GTIN, Charge/Verfall laut ATIV-Ticket,",
        "Seriennummer TST…). Jede Seriennummer lässt sich in Odoo nur einmal erfassen.",
        "",
        "Ablauf je Paket:  1. Retourenschein-QR scannen   2. Box-QR scannen   3. Packungen scannen",
        "                  4. Vorprüfung   5. Drucken und Box abschließen   6. „Paket fertig“",
        "",
        "Voraussetzung: Die Artikel sind im Artikelstamm angelegt (GTIN/PZN), die Boxen sind leer.",
    ]
    y = h - 32 * mm
    for line in intro:
        c.drawString(20 * mm, y, line)
        y -= 5 * mm
    c.setFont("Helvetica-Bold", 10)
    y -= 4 * mm
    c.drawString(20 * mm, y, "Übersicht")
    c.setFont("Helvetica", 9)
    box = first_box
    plan = []
    for g in selection:
        boxes = []
        for _l in g["lines"]:
            boxes.append(f"GX{box:03d}")
            box += 1
        plan.append(boxes)
        y -= 5.5 * mm
        c.drawString(20 * mm, y, f"{g['ref']}  ·  Gutschrift {g['gs']}  ·  {short(g['kunde'], 40)}  ·  "
                                 f"Box {', '.join(boxes)}  ·  {sum(int(l['qty']) for l in g['lines'])} Pkg.")
    c.showPage()

    n = 0
    slots = []
    for g, boxes in zip(selection, plan):
        for l, b in zip(g["lines"], boxes):
            gtin = gtins.get(str(l["pzn"]).zfill(8))
            if not gtin:
                raise SystemExit(f"PZN {l['pzn']} fehlt in import/greenrx_artikel_pzn.csv")
            packs = []
            for k in range(int(l["qty"])):
                n += 1
                packs.append((f"Packung {k + 1}/{int(l['qty'])}", gs1(gtin, yymmdd(l["exp"]), l["batch"],
                                                                     f"TST{run}{n:03d}")))
            slots.append((g, l, b, packs))

    per_page = 2
    for start in range(0, len(slots), per_page):
        top = h - 15 * mm
        for g, l, b, packs in slots[start:start + per_page]:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(20 * mm, top - 6 * mm, f"{g['ref']}  ·  Gutschrift {g['gs']}")
            c.setFont("Helvetica", 9)
            c.drawString(20 * mm, top - 12 * mm, f"{short(g['kunde'], 60)}  ·  Ticket {l['ticket']}  ·  "
                                                  f"{short(l['name'], 40)}  ·  Charge {l['batch']}")
            draw_code(c, "QR", b, 20 * mm, top - 52 * mm, 34 * mm)
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(37 * mm, top - 57 * mm, f"Box {b}")
            for i, (title, value) in enumerate(packs[:4]):
                x = 65 * mm + i * 33 * mm
                draw_code(c, "DM", value, x, top - 48 * mm, 28 * mm)
                c.setFont("Helvetica", 7.5)
                c.drawCentredString(x + 14 * mm, top - 52 * mm, title)
                c.drawCentredString(x + 14 * mm, top - 56 * mm, value.split(GS + "21")[-1])
            if len(packs) > 4:
                raise SystemExit("Mehr als 4 Packungen je Position: Layout erweitern.")
            c.line(20 * mm, top - 63 * mm, 195 * mm, top - 63 * mm)
            top -= 72 * mm
        c.showPage()
    c.save()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("selection")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "ativ_testpack"))
    ap.add_argument("--box", type=int, default=101, help="erste Box-Nummer (GX…)")
    args = ap.parse_args()
    with open(args.selection, encoding="utf-8") as f:
        selection = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    slips = os.path.join(args.out, "GreenRX_Retourenscheine_Test.pdf")
    packs = os.path.join(args.out, "GreenRX_Packungscodes_Test.pdf")
    build_slips(selection, slips)
    count = build_packs(selection, packs, args.box, load_gtins())
    print(f"{len(selection)} Retourenscheine: {slips}")
    print(f"{count} Packungscodes: {packs}")


if __name__ == "__main__":
    main()
