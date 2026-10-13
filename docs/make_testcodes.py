"""Erzeugt den Testbogen docs/greenrx_testcodes.pdf für die Prüfung auf Odoo.sh.

Inhalt: Retourenschein-QR (V202601693), Box-Codes GX001/GX002 und securPharm-Codes
(DataMatrix, GS1 mit GS-Trennzeichen) für die angemeldeten Packungen plus Fehlerfälle.

Aufruf:  python3 docs/make_testcodes.py
"""
import os
import sys

import zxingcpp  # pip install zxing-cpp
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Repo-Wurzel
OUT = os.path.join(ROOT, "docs", "greenrx_testcodes.pdf")
GS = "\x1d"

SLIP = ("REF:V202601693\nREKL:S23124\nTICKET|PZN|CHARGE|ANZAHL\n"
        "1001982|13570727|214124|4\n1001983|07560038|33t32|3")

BRILIQUE = "04150075600387"   # PZN 07560038
BEVESPI = "04150135707278"    # PZN 13570727


def gs1(gtin, expiry, batch, serial):
    return f"01{gtin}17{expiry}10{batch}{GS}21{serial}"


PACKS = [
    ("BRILIQUE 1/3", gs1(BRILIQUE, "280630", "33t32", "TSTBRI0000001")),
    ("BRILIQUE 2/3", gs1(BRILIQUE, "280630", "33t32", "TSTBRI0000002")),
    ("BRILIQUE 3/3", gs1(BRILIQUE, "280630", "33t32", "TSTBRI0000003")),
    ("BEVESPI 1/4", gs1(BEVESPI, "280930", "214124", "TSTBEV0000001")),
    ("BEVESPI 2/4", gs1(BEVESPI, "280930", "214124", "TSTBEV0000002")),
    ("BEVESPI 3/4", gs1(BEVESPI, "280930", "214124", "TSTBEV0000003")),
    ("BEVESPI 4/4", gs1(BEVESPI, "280930", "214124", "TSTBEV0000004")),
]
ERRORS = [
    ("Fehlerfall: andere Charge (X999)", gs1(BRILIQUE, "280630", "X999", "TSTBRI0000099"),
     "In der BRILIQUE-Box: abgelehnt. In einer neuen Box: Vorschlag Gelb."),
    ("Fehlerfall: Verfall in 6 Wochen", gs1(BEVESPI, "261130", "214124", "TSTBEV0000099"),
     "In einer neuen Box: Restlaufzeit zu kurz, Vorschlag Rot."),
]


def draw_code(c, kind, value, x, y, size):
    fmt = zxingcpp.BarcodeFormat.QRCode if kind == "QR" else zxingcpp.BarcodeFormat.DataMatrix
    img = Image.fromarray(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(value, fmt), scale=1))
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)
    c.drawImage(ImageReader(img), x, y, size, size)


def readable(value):
    return value.replace(GS, "<GS>")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    c = canvas.Canvas(OUT, pagesize=A4)
    c.setTitle("GreenRX – Testcodes für die Prüfung")
    w, h = A4

    # Seite 1: Retourenschein + Boxen
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, h - 22 * mm, "GreenRX – Testcodes (nur für die Prüfumgebung)")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 28 * mm, "Packungscodes sind Testdaten, keine echten securPharm-Seriennummern.")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 42 * mm, "1. Retourenschein V202601693 (Reklamation S23124)")
    draw_code(c, "QR", SLIP, 20 * mm, h - 100 * mm, 52 * mm)
    c.setFont("Helvetica", 8.5)
    ty = h - 52 * mm
    for line in SLIP.split("\n"):
        c.drawString(80 * mm, ty, line)
        ty -= 4.2 * mm
    c.drawString(80 * mm, ty - 3 * mm, "Erwartet: Ticket 1001983 BRILIQUE 3 Pck., Ticket 1001982 BEVESPI 4 Pck.")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 115 * mm, "2. Boxen")
    for i, box in enumerate(["GX001", "GX002", "GX003"]):
        x = 20 * mm + i * 55 * mm
        draw_code(c, "QR", box, x, h - 160 * mm, 38 * mm)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 10 * mm, h - 166 * mm, box)

    c.setFont("Helvetica", 8.5)
    c.drawString(20 * mm, h - 180 * mm,
                 "Empfehlung: GX001 für BRILIQUE, GX002 für BEVESPI, GX003 für einen Fehlerfall.")
    c.showPage()

    # Seite 2: Packungen
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, h - 20 * mm, "3. Packungen (securPharm-DataMatrix, GS1)")
    cols, size = 3, 34 * mm
    items = [(t, v, "") for t, v in PACKS] + list(ERRORS)
    for idx, (title, value, note) in enumerate(items):
        col, row = idx % cols, idx // cols
        x = 20 * mm + col * 60 * mm
        y = h - 70 * mm - row * 72 * mm
        draw_code(c, "DM", value, x, y, size)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x, y - 5 * mm, title)
        c.setFont("Helvetica", 6.5)
        first, _, second = readable(value).partition("<GS>")
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
    print(OUT)


if __name__ == "__main__":
    sys.exit(main())
