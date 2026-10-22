"""Testprotokoll Blistercenter-Wareneingang (blistercenter 1.6.0): Testplan + Packungscodes als PDF.

Inhalt
  Seite 1–2  Testprotokoll: Schritte, erwartetes Ergebnis, Feld zum Abhaken und für Bemerkungen
  Seite 3    Packungscodes (GS1-DataMatrix wie auf echten Packungen) mit Klartext

Aufruf:
    python3 docs/make_bc_receipt_testpack.py
Ausgabe: docs/testprotokoll_blistercenter_wareneingang.pdf

Die Artikel sind die Testartikel, die jeder Dev-Build mit Demodaten anlegt (blistercenter/tools/testdaten.py).
Chargen, Verfall und Seriennummern sind Testdaten. Blistercenter speichert keine Seriennummern; das Paket
kann mehrfach verwendet werden.
"""
import os
import sys

import zxingcpp  # pip install zxing-cpp
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "blistercenter", "tools"))
import gs1  # noqa: E402  (ohne Odoo nutzbar)

GS = "\x1d"
OUT = os.path.join(ROOT, "docs", "testprotokoll_blistercenter_wareneingang.pdf")

FORXIGA = ("10330201", "FORXIGA 10 mg Filmtabletten 98 St (N3)")
XIGDUO = ("10126564", "XIGDUO 5 mg/850 mg Filmtabletten 196 St (N3)")
DAXAS = ("13475319", "DAXAS 250 Mikrogramm Tabletten 28 St")

# Code, Artikel, Charge, Verfall (JJMMTT), Seriennummer, mit Trennzeichen?, Zweck
PACKS = [
    ("P1", FORXIGA, "FX-T01", "280331", "TSTBC0001", True, "Forxiga, Charge FX-T01"),
    ("P2", FORXIGA, "FX-T01", "280331", "TSTBC0002", True, "Forxiga, gleiche Charge FX-T01"),
    ("P3", FORXIGA, "FX-T02", "271130", "TSTBC0003", True, "Forxiga, andere Charge FX-T02"),
    ("F1", DAXAS, "DX-T01", "280430", "TSTBC0004", True, "Fremder Artikel (nicht bestellt)"),
    ("K1", FORXIGA, "FX-T01", "280630", "TSTBC0005", True, "Charge FX-T01 mit anderem Verfall"),
    ("O1", XIGDUO, "XG21", "280831", "S10", False, "Xigduo, Code OHNE Trennzeichen"),
]
# Packung ohne lesbaren Code (nur Klartext): Handeingabe
MANUAL = ("H1", XIGDUO, "XG21", "08/2028", "Xigduo, Code beschädigt – von Hand eintragen")


def code_value(pzn, batch, expiry, serial, with_gs):
    gtin = gs1.pzn_to_gtin(pzn)
    if with_gs:
        # Reihenfolge wie auf vielen deutschen Packungen: GTIN, Seriennummer, Charge, Verfall
        return "01%s21%s%s10%s%s17%s" % (gtin, serial, GS, batch, GS, expiry)
    return "01%s21%s10%s17%s" % (gtin, serial, batch, expiry)


def check_codes():
    """Selbstprüfung: Codes mit Trennzeichen lesbar, Code ohne Trennzeichen nicht eindeutig."""
    for name, (pzn, _a), batch, expiry, serial, with_gs, _p in PACKS:
        value = code_value(pzn, batch, expiry, serial, with_gs)
        if with_gs:
            data = gs1.parse(value)
            assert data["pzn"] == pzn and data["batch"] == batch, name
        else:
            try:
                gs1.parse(value)
            except gs1.Gs1Error:
                pass
            else:
                raise AssertionError("%s soll ohne Trennzeichen nicht eindeutig sein" % name)


def draw_datamatrix(c, value, with_gs, x, y, size):
    if with_gs:
        # Echte GS1-DataMatrix (FNC1 am Anfang, GS zwischen den variablen Feldern)
        ais = gs1.parse(value)["ais"]
        hri = "".join("(%s)%s" % (ai, ais[ai]) for ai in ("01", "21", "10", "17"))
        barcode = zxingcpp.create_barcode(hri, zxingcpp.BarcodeFormat.DataMatrix, gs1=True)
    else:
        barcode = zxingcpp.create_barcode(value, zxingcpp.BarcodeFormat.DataMatrix)
    img = Image.fromarray(zxingcpp.write_barcode_to_image(barcode, scale=1))
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)
    # Gegenprobe: Bild wieder lesen
    read = zxingcpp.read_barcodes(img, text_mode=zxingcpp.TextMode.Plain)
    assert read and read[0].text.replace(GS, "") == value.replace(GS, ""), value
    c.drawImage(ImageReader(img), x, y, size, size)


def steps():
    f, x, d = FORXIGA[1], XIGDUO[1], DAXAS[1]
    return [
        ("Vorbereitung", "Administrator", [
            "Odoo.sh › Branch fix/wareneingang-einfach › Connect (Dev-Build mit Testdaten).",
            "Benutzer Lager A, Lager B (Rolle Blistercenter „Benutzer“, alle Arbeitsbereiche) und VP "
            "(Rolle „Freigabe“) anlegen.",
            "Handscanner: Tastatursprache Deutsch. Ohne Handscanner: im Fenster den Knopf „Kamera“ nutzen "
            "(Browser fragt einmal nach der Kamera-Erlaubnis).",
        ], "Drei Benutzer vorhanden, Scanner angeschlossen."),
        ("1 Bestellung", "Lager A", [
            "Blistercenter › Telefonbestellung erfassen. Kunde „Blistercenter Musterstadt (Test)“, Besteller wählen.",
            "Positionen: %s × 3, %s × 2. Bestätigen." % (f, x),
        ], "Auftrag bestätigt, Proforma im Verlauf."),
        ("2 Einkauf", "Lager A", [
            "Blistercenter › Zu bestellen: beide Positionen markieren › „Zur Sammelbestellung“ › „Übernehmen“.",
            "In der Sammelbestellung „Anfrage beim Hersteller erstellen“, dann die Herstellerbestellung bestätigen.",
        ], "Wareneingang BCIN entsteht (Ziel Quarantäne)."),
        ("3 Fenster öffnen", "Lager A", [
            "Blistercenter › Wareneingänge › neuen BCIN öffnen.",
            "Oben „Packungen erfassen (Scan oder Hand)“ klicken. Der Cursor steht im Scan-Feld.",
        ], "Fenster mit 1. Scannen, 2. Von Hand, 3. Erfasste Packungen und Übersicht (Forxiga: fehlt 3, "
           "Xigduo: fehlt 2)."),
        ("4 Scannen", "Lager A", [
            "Codes P1, P2 und P3 nacheinander scannen (Handscanner) oder „Kamera“ und die Codes nacheinander vor "
            "die Kamera halten.",
            "Bei roter Meldung: die Zeile „Gelesen: …“ abschreiben oder Foto machen (zeigt, was der Scanner sendet).",
        ], "Grün. Zwei Zeilen: FX-T01 Menge 2 (Verfall 31.03.2028), FX-T02 Menge 1 (30.11.2027). "
           "Übersicht Forxiga: vollständig."),
        ("5 Fehlfälle", "Lager A", [
            "Code F1 scannen (%s, nicht bestellt)." % d,
            "Code K1 scannen (FX-T01 mit Verfall 30.06.2028).",
        ], "Beide rot, nichts gezählt. F1: „gehört nicht zu diesem Wareneingang“. K1: „Verfall weicht von "
           "der ersten Packung ab“. FX-T01 bleibt Menge 2."),
        ("6 Code ohne Trennzeichen", "Lager A", [
            "Code O1 scannen.",
            "Charge von der Packung (Klartext neben dem Code: XG21) im Feld „Charge“ eintragen, Anzahl 1, "
            "„Hinzufügen“.",
        ], "Rot: Charge nicht sicher lesbar. Artikel Xigduo und Verfall 08/2028 sind unten schon eingetragen. "
           "Nach „Hinzufügen“: Zeile XG21, Verfall 31.08.2028, Menge 1."),
        ("7 Von Hand", "Lager A", [
            "Packung H1 (ohne lesbaren Code): Artikel %s, Charge XG21, Verfall 08/2028, Anzahl 1, „Hinzufügen“." % x,
            "Probe: Charge leer lassen oder Verfall 13/2028 eintragen und „Hinzufügen“.",
        ], "XG21 jetzt Menge 2, Übersicht Xigduo: vollständig. Probe: Meldung „Charge fehlt“ bzw. "
           "„Monat 13 … ungültig“, nichts gezählt."),
        ("8 Übernehmen und buchen", "Lager A", [
            "„In den Wareneingang übernehmen“. Im Wareneingang „Validieren“.",
        ], "Status Erledigt. Chargen FX-T01 (2), FX-T02 (1), XG21 (2) mit Verfall. Ware in "
           "WH/Blistercenter/Quarantäne, Freigabe BCFRG für die VP."),
        ("9 Freigabe", "Lager A, dann VP", [
            "Lager A: Freigabe BCFRG öffnen, „Validieren“.",
            "VP: Blistercenter › Freigabe Wareneingang (VP) › BCFRG „Validieren“.",
        ], "Lager A: Fehlermeldung (nur VP). VP: erledigt, Ware im Bestand, Lieferung an den Kunden bereit."),
        ("10 Versand (optional)", "Lager A, dann Lager B", [
            "Lager A: Lieferung öffnen, „Kommissionierung abgeschlossen“.",
            "Lager B: Lieferung „Validieren“ (Endkontrolle).",
        ], "Lieferung mit den Chargen aus Schritt 8. Rechnung über die gelieferte Menge entsteht."),
    ]


def build():
    check_codes()
    c = canvas.Canvas(OUT, pagesize=A4)
    c.setTitle("Blistercenter 1.6.2 – Testprotokoll Wareneingang")
    w, h = A4
    left, width = 16 * mm, w - 32 * mm

    def header(title, sub=None):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(left, h - 17 * mm, title)
        c.setFont("Helvetica", 8.5)
        if sub:
            c.drawString(left, h - 22 * mm, sub)
        return h - 30 * mm

    y = header("Blistercenter 1.6.2 – Testprotokoll Wareneingang",
               "Dev-Build, nicht Production. Codes auf Seite 3 sind Testdaten. Jeden Schritt abhaken, Abweichung "
               "unter „Bemerkung“ notieren.")
    c.setFont("Helvetica", 9)
    c.drawString(left, y, "Datum: ____________    Tester: ______________________    Build/Commit: ______________")
    y -= 8 * mm
    for title, who, items, expected in steps():
        lines = []
        for i, step in enumerate(items, 1):
            lines += simpleSplit("%s. %s" % (i, step), "Helvetica", 8.5, width - 14 * mm)
        exp = simpleSplit("Erwartet: " + expected, "Helvetica-Bold", 8.5, width - 14 * mm)
        need = (len(lines) + len(exp)) * 4.1 * mm + 17 * mm
        if y - need < 14 * mm:
            c.showPage()
            y = header("Testprotokoll Wareneingang (Fortsetzung)")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, title)
        c.setFont("Helvetica", 8)
        c.drawRightString(left + width, y, "Rolle: " + who)
        y -= 5 * mm
        c.setFont("Helvetica", 8.5)
        for line in lines:
            c.drawString(left + 4 * mm, y, line)
            y -= 4.1 * mm
        c.setFont("Helvetica-Bold", 8.5)
        for line in exp:
            c.drawString(left + 4 * mm, y, line)
            y -= 4.1 * mm
        c.setFont("Helvetica", 8.5)
        c.rect(left + 4 * mm, y - 1.2 * mm, 3.5 * mm, 3.5 * mm)
        c.drawString(left + 9.5 * mm, y, "OK")
        c.rect(left + 20 * mm, y - 1.2 * mm, 3.5 * mm, 3.5 * mm)
        c.drawString(left + 25.5 * mm, y, "Fehler    Bemerkung: ______________________________________________")
        y -= 8 * mm

    # Codes
    c.showPage()
    y = header("Packungscodes (GS1-DataMatrix)",
               "Mit dem Handscanner im Fenster „Packungen erfassen“ scannen. Klartext = Aufdruck der Packung.")
    size = 30 * mm
    col_w = width / 2
    entries = PACKS + [MANUAL]
    for i, entry in enumerate(entries):
        col, row = i % 2, i // 2
        x0 = left + col * col_w
        y0 = y - (row + 1) * (size + 14 * mm)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(x0, y0 + size + 3 * mm, entry[0])
        if entry is MANUAL:
            name, (pzn, article), batch, expiry, purpose = entry
            c.setDash(3, 2)
            c.rect(x0, y0, size, size)
            c.setDash()
            c.setFont("Helvetica", 7)
            c.drawCentredString(x0 + size / 2, y0 + size / 2, "Code beschädigt")
            exp_text = expiry
        else:
            name, (pzn, article), batch, expiry, serial, with_gs, purpose = entry
            draw_datamatrix(c, code_value(pzn, batch, expiry, serial, with_gs), with_gs, x0, y0, size)
            exp_text = "%s/20%s" % (expiry[2:4], expiry[0:2])
        tx = x0 + size + 4 * mm
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(tx, y0 + size - 3 * mm, purpose)
        c.setFont("Helvetica", 8)
        for j, line in enumerate(simpleSplit(article, "Helvetica", 8, col_w - size - 8 * mm)[:2]):
            c.drawString(tx, y0 + size - 8 * mm - j * 3.6 * mm, line)
        c.drawString(tx, y0 + size - 16 * mm, "PZN %s" % pzn)
        c.drawString(tx, y0 + size - 20 * mm, "Ch.-B. %s" % batch)
        c.drawString(tx, y0 + size - 24 * mm, "Verw. bis %s" % exp_text)
    c.setFont("Helvetica", 7.5)
    c.drawString(left, 12 * mm, "Erzeugt mit docs/make_bc_receipt_testpack.py. O1 enthält absichtlich keine Trennzeichen.")
    c.save()
    return OUT


if __name__ == "__main__":
    print(build())
