"""Testpaket für plcs_greenrx 1.8.0: Testplan + alle Codes als PDF (zum Ausdrucken und Scannen).

Inhalt
  Teil A  Testplan: Testfälle T1–T9 mit Schritten, erwartetem Ergebnis und Feld zum Abhaken
  Teil B  Codes:  Retourenschein-QR, Box-QR-Codes, securPharm-DataMatrix (GS1 mit GS-Trennzeichen)

Aufruf:
    python3 docs/make_testpack_1_8.py                 # Vorgang VTEST1801, Boxen ab GX181
    python3 docs/make_testpack_1_8.py --ref VTEST1802 --box 171

Ausgabe: docs/testpaket_<Vorgang>.pdf und docs/testpaket_<Vorgang>.json (gleiche Codes als Daten,
für die automatische Prüfung mit docs/check_testpack_1_8.py).

Die Seriennummern sind Testdaten (Präfix TST) und je Lauf neu. Jede Seriennummer lässt sich in
Odoo nur einmal erfassen: für einen zweiten Durchlauf ein neues Paket mit neuer Referenz erzeugen.
"""
import argparse
import csv
import json
import os
import time

import zxingcpp  # pip install zxing-cpp
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GS = "\x1d"
EXPIRY = "280331"  # 31.03.2028

# Artikel aus import/greenrx_artikel_pzn.csv (in Production angelegt)
ARTICLES = {
    "A": ("07560038", "BRILIQUE 90 mg"),
    "B": ("18063573", "CALQUENCE 100 mg"),
    "C": ("15530910", "FASENRA 30 mg"),
    "D": ("16020967", "LOKELMA 10 g"),
    "E": ("09278978", "KOMBOGLYZE 2,5/850 mg"),  # nicht auf dem Retourenschein
}


def load_gtins():
    """GTIN je PZN genau wie im Artikelstamm (import/greenrx_artikel_pzn.csv)."""
    with open(os.path.join(ROOT, "import", "greenrx_artikel_pzn.csv"), encoding="utf-8") as f:
        return {r["plcs_pzn"]: r["barcode"] for r in csv.DictReader(f)}


GTINS = load_gtins()


def gtin_from_pzn(pzn):
    return GTINS[pzn]


def pack_code(pzn, batch, serial):
    return "01%s17%s10%s%s21%s" % (gtin_from_pzn(pzn), EXPIRY, batch, GS, serial)


def build_data(ref, first_box):
    run = time.strftime("%m%d%H%M")
    box = lambda i: "GX%03d" % (first_box + i)  # noqa: E731
    sn = lambda n: "TST%s%03d" % (run, n)  # noqa: E731
    slip_lines = [("1801", "A", "CHA18", 3), ("1802", "B", "CHB18", 1), ("1803", "C", "CHC18", 1),
                  ("1804", "D", "CHD18", 1)]
    slip = "\n".join(["REF:%s" % ref, "REKL:S%s" % ref[1:], "TICKET|PZN|CHARGE|ANZAHL"]
                     + ["%s|%s|%s|%s" % (t, ARTICLES[a][0], b, q) for t, a, b, q in slip_lines])
    packs = {
        "T1": [(ARTICLES["A"][1] + " %s/3" % (i + 1), pack_code(ARTICLES["A"][0], "CHA18", sn(i + 1)))
               for i in range(3)],
        "T2": [(ARTICLES["B"][1], pack_code(ARTICLES["B"][0], "CHB18", sn(11)))],
        "T3": [(ARTICLES["E"][1] + " (nicht angemeldet)", pack_code(ARTICLES["E"][0], "CHE18", sn(21)))],
        "T4": [(ARTICLES["C"][1], pack_code(ARTICLES["C"][0], "CHC18", sn(31)))],
        "T5": [(ARTICLES["D"][1], pack_code(ARTICLES["D"][0], "CHD18", sn(41)))],
    }
    boxes = {"T1": box(0), "T2": box(1), "T3": box(2), "T4": box(3), "T5": box(4), "T6": box(5)}
    return {"ref": ref, "slip": slip, "slip_lines": slip_lines, "boxes": boxes, "packs": packs, "run": run}


def test_cases(d):
    b, ref = d["boxes"], d["ref"]
    return [
        ("Vorbereitung", "Administrator", [
            "Testbenutzer lager.test (Lager) und vp.test (VP) sind angemeldet bzw. in zwei Browsern offen.",
            "Boxen %s–%s sind leer (Lager › Pakete). Sonst mit --box andere Boxen erzeugen." % (b["T1"], b["T6"]),
            "PC mit Handscanner (Tastaturmodus, GS1/FNC1) für T1, für die anderen Fälle geht auch die Kamera.",
        ], "Ausgangslage klar."),
        ("T1  Schnell scannen und sofort weiter (R03, R16, Formblatt)", "lager.test", [
            "GreenRX › Wareneingang scannen › Retourenschein-QR (Seite Codes) scannen.",
            "Erwartet: „Vorgang aus dem Retourenschein angelegt“, Referenz %s, 4 Tickets." % ref,
            "Box %s scannen. Die 3 BRILIQUE-Packungen (T1) ohne Pause hintereinander scannen "
            "und sofort F9 drücken." % b["T1"],
            "Packung 3/3 mit „Entfernen“ herausnehmen: Paketübersicht oben zeigt 2. Wieder scannen: 3.",
            "Vorprüfung: alle Fragen Ja, Ampel Grün, Weiter, Drucken.",
            "Formblatt prüfen: Ticketvergleich PZN, Bezeichnung, Charge = ja (Ticket 1801, CHA18). "
            "„Wareneingangsprüfung durchgeführt von“ = Benutzer lager.test. Anlage: „Erfasst von“ = lager.test.",
            "„Ausdruck geprüft und unterschrieben“ anhaken, Box abschließen.",
        ], "Alle 3 Packungen in der Box, keine Meldung „Scan nicht verarbeitet“. Box wartet auf VP."),
        ("T2  Gelb vorgeschlagen, Rot gewählt (R09)", "lager.test", [
            "Box %s scannen, CALQUENCE (T2) scannen, Weiter." % b["T2"],
            "Vorprüfung: „Keine Beschädigungen…“ = Nein, alle anderen Ja. Vorschlag: Gelb.",
            "Rot wählen, Bemerkung leer lassen, Weiter drücken.",
            "Erwartet: Weiter geht nicht, Feld „Grund (Pflicht, weil strenger als der Vorschlag)“.",
            "Grund eintragen (z. B. „Umkarton nass“), Weiter, Drucken (3 Seiten mit rotem Sperrblatt), "
            "bestätigen, abschließen.",
        ], "Ohne Grund kein Weiter; mit Grund Box rot ins Sperrlager."),
        ("T3  Artikel nicht auf dem Retourenschein (R10)", "lager.test", [
            "Box %s scannen, KOMBOGLYZE (T3) scannen." % b["T3"],
            "Erwartet: Warnung „Artikel steht nicht auf dem Retourenschein“.",
            "Vorprüfung alle Ja, Vorschlag Gelb, Gelb wählen, Weiter, Drucken.",
            "Formblatt prüfen: „Ist die Ware für uns?“ = nein. PZN, Bezeichnung, Charge = nein "
            "mit Vermerk „keine Position im Ticket“.",
            "Bestätigen, abschließen.",
        ], "Formblatt zeigt bei fehlender Ticketposition „nein“, nicht „ja“."),
        ("T4  VP übernimmt die Vorprüfung (R18, 4-Augen)", "lager.test, dann vp.test", [
            "lager.test: Box %s scannen, FASENRA (T4) scannen, Weiter zur Vorprüfung. Nichts beantworten." % b["T4"],
            "vp.test: GreenRX › Wareneingang scannen › Vorgang %s öffnen, Box %s scannen." % (ref, b["T4"]),
            "vp.test: Vorprüfung alle Ja, Grün, Weiter, Drucken.",
            "Formblatt prüfen: „Wareneingangsprüfung durchgeführt von“ = vp.test. Anlage: „Erfasst von“ = lager.test.",
            "vp.test: bestätigen, abschließen.",
        ], "Prüfer und Erfasser stehen richtig auf dem Formblatt."),
        ("T5  Stammdaten nach dem Druck geändert (R11, Neudruck)", "lager.test, Administrator", [
            "lager.test: Box %s scannen, LOKELMA (T5) scannen, Vorprüfung alle Ja, Grün, Drucken, bestätigen. "
            "Noch nicht abschließen." % b["T5"],
            "Administrator: Lager › Produkte › LOKELMA 10 g › Feld „Lagertemperatur“ umstellen, speichern.",
            "lager.test: Box abschließen.",
            "Erwartet: Fehler „…nach dem letzten Druck geändert. Bitte neu drucken…“.",
            "lager.test: Drucken (Neuausdruck Nr. 1). Erwartet: Haken „geprüft und unterschrieben“ ist weg. "
            "Neu anhaken, abschließen.",
            "Administrator: Lagertemperatur wieder zurückstellen.",
        ], "Abschluss erst nach Neudruck und neuer Bestätigung."),
        ("T6  Dieselbe Packung noch einmal (R08)", "lager.test", [
            "Box %s scannen. BRILIQUE 1/3 (T1, schon abgeschlossen in %s) noch einmal scannen." % (b["T6"], b["T1"]),
            "Erwartet: „Diese Packung ist schon im System“ mit Hinweis auf %s." % b["T1"],
            "Box %s abbrechen (Erfassung abbrechen)." % b["T6"],
        ], "Doppelte Seriennummer wird abgelehnt, auch nach dem Abschluss der ersten Box."),
        ("T7  Paket fertig", "lager.test", [
            "„Paket fertig“ drücken.",
            "Erwartet: Abfrage einer Begründung, weil KOMBOGLYZE nicht angemeldet ist. Begründung eintragen.",
        ], "Eingang gebucht, Vorgang erledigt. Boxen erscheinen bei der VP."),
        ("T8  Umgehung gesperrt (R02, R08)", "Administrator", [
            "Lager › Pakete › %s öffnen › „Auspacken“." % b["T1"],
            "Erwartet: Fehler „…darf dort nicht ausgepackt oder umgepackt werden…“.",
            "GreenRX › Übersicht › Seriennummern › eine BRILIQUE-Seriennummer öffnen, Namen ändern, speichern.",
            "Erwartet: Fehler „…kann nicht geändert werden“. Änderung verwerfen.",
        ], "Box-Inhalt und Seriennummern bleiben unverändert."),
        ("T9  VP-Freigabe", "vp.test, Administrator", [
            "vp.test: GreenRX › VP-Freigabe. %s (Grün) freigeben." % b["T1"],
            "vp.test: %s (Gelb) mit Begründung freigeben; ohne Begründung kommt ein Hinweis." % b["T3"],
            "vp.test: %s steht am Ende mit „nur sperren“, kein Freigeben-Knopf (vp.test war beteiligt)." % b["T4"],
            "vp.test: %s freigeben." % b["T5"],
            "Administrator: %s freigeben (unbeteiligt). %s (Rot) bleibt gesperrt." % (b["T4"], b["T2"]),
            "GreenRX › Übersicht › Bestand GreenRX: %s, %s, %s, %s in GreenRX/Bestand, %s im Sperrlager." % (
                b["T1"], b["T3"], b["T4"], b["T5"], b["T2"]),
        ], "Freigaben nur durch unbeteiligte VP, Ware an den erwarteten Lagerorten."),
    ]


def draw_code(c, kind, value, x, y, size):
    fmt = zxingcpp.BarcodeFormat.QRCode if kind == "QR" else zxingcpp.BarcodeFormat.DataMatrix
    img = Image.fromarray(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(value, fmt), scale=1))
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)
    c.drawImage(ImageReader(img), x, y, size, size)


def build(ref, first_box, out_pdf, out_json):
    d = build_data(ref, first_box)
    c = canvas.Canvas(out_pdf, pagesize=A4)
    c.setTitle("GreenRX 1.8.0 – Testpaket %s" % ref)
    w, h = A4
    left, width = 18 * mm, w - 36 * mm

    def header(title, sub=None):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(left, h - 18 * mm, title)
        if sub:
            c.setFont("Helvetica", 8.5)
            c.drawString(left, h - 23 * mm, sub)
        return h - 31 * mm

    # Teil A: Testplan
    y = header("GreenRX 1.8.0 – Testplan (Vorgang %s)" % ref,
               "Prüfumgebung. Packungscodes sind Testdaten, keine echten securPharm-Seriennummern. "
               "Lauf %s." % d["run"])
    for title, who, steps, expected in test_cases(d):
        lines = []
        for i, step in enumerate(steps, 1):
            lines += simpleSplit("%s. %s" % (i, step), "Helvetica", 8.5, width - 8 * mm)
        exp_lines = simpleSplit("Ergebnis: " + expected, "Helvetica-Bold", 8.5, width - 8 * mm)
        need = (len(lines) + len(exp_lines)) * 4.2 * mm + 16 * mm
        if y - need < 15 * mm:
            c.showPage()
            y = header("GreenRX 1.8.0 – Testplan (Fortsetzung)")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(left, y, title)
        c.setFont("Helvetica", 8)
        c.drawRightString(left + width, y, "Rolle: " + who)
        y -= 5.5 * mm
        c.setFont("Helvetica", 8.5)
        for line in lines:
            c.drawString(left + 4 * mm, y, line)
            y -= 4.2 * mm
        c.setFont("Helvetica-Bold", 8.5)
        for line in exp_lines:
            c.drawString(left + 4 * mm, y, line)
            y -= 4.2 * mm
        c.setFont("Helvetica", 8.5)
        c.rect(left + 4 * mm, y - 1.2 * mm, 3 * mm, 3 * mm)
        c.drawString(left + 9 * mm, y - 0.5 * mm, "OK")
        c.rect(left + 20 * mm, y - 1.2 * mm, 3 * mm, 3 * mm)
        c.drawString(left + 25 * mm, y - 0.5 * mm, "Fehler – Notiz: ______________________________________________")
        y -= 9 * mm
    if y < 40 * mm:
        c.showPage()
        y = header("GreenRX 1.8.0 – Testplan (Fortsetzung)")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Nur automatisch geprüft (nicht über die Oberfläche testbar)")
    y -= 5.5 * mm
    c.setFont("Helvetica", 8.5)
    for line in simpleSplit(
            "R01 Kontextwerte per API · R05 seitenweiser Abruf alter Connector · R06 Firmentrennung beim Import · "
            "R07 gleichzeitige Scans an zwei Arbeitsplätzen · R12 Reihenfolge der Suchantworten · R13 Menge 0 · "
            "R14 mehrdeutige GS1-Codes ohne Trennzeichen · R15 Firmenregeln. Nachweis: Testklasse TestReviewFixes.",
            "Helvetica", 8.5, width):
        c.drawString(left, y, line)
        y -= 4.2 * mm
    c.showPage()

    # Teil B: Codes
    y = header("Codes 1: Retourenschein und Boxen (Vorgang %s)" % ref)
    draw_code(c, "QR", d["slip"], left, y - 55 * mm, 55 * mm)
    c.setFont("Helvetica", 8.5)
    ty = y - 4 * mm
    for line in d["slip"].split("\n"):
        c.drawString(left + 62 * mm, ty, line)
        ty -= 4.2 * mm
    ty -= 2 * mm
    for t, a, batch, qty in d["slip_lines"]:
        c.drawString(left + 62 * mm, ty, "Ticket %s: %s – %s Pck., Charge %s" % (t, ARTICLES[a][1], qty, batch))
        ty -= 4.2 * mm
    y -= 70 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Boxen")
    y -= 4 * mm
    for i, (case, name) in enumerate(d["boxes"].items()):
        col, row = i % 4, i // 4
        x, by = left + col * 44 * mm, y - 36 * mm - row * 50 * mm
        draw_code(c, "QR", name, x, by, 32 * mm)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 6 * mm, by - 5 * mm, "%s (%s)" % (name, case))
    c.showPage()

    items = [(case, title, value) for case, packs in d["packs"].items() for title, value in packs]
    per_page, cols, size = 9, 3, 34 * mm
    for start in range(0, len(items), per_page):
        y = header("Codes 2: Packungen (securPharm-DataMatrix, GS1)")
        for idx, (case, title, value) in enumerate(items[start:start + per_page]):
            col, row = idx % cols, idx // cols
            x, py = left + col * 60 * mm, y - 38 * mm - row * 72 * mm
            draw_code(c, "DM", value, x, py, size)
            c.setFont("Helvetica-Bold", 9)
            ty = py - 5 * mm
            for line in simpleSplit("%s · %s" % (case, title), "Helvetica-Bold", 9, 55 * mm):
                c.drawString(x, ty, line)
                ty -= 4 * mm
            c.setFont("Helvetica", 6.5)
            first, _, second = value.replace(GS, "<GS>").partition("<GS>")
            c.drawString(x, ty - 0.5 * mm, first + "<GS>")
            c.drawString(x, ty - 4 * mm, second)
        c.showPage()
    c.save()
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    return out_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="VTEST1801", help="Referenz des Test-Vorgangs")
    parser.add_argument("--box", type=int, default=181, help="erste Box-Nummer (Standard 181 = GX181)")
    args = parser.parse_args()
    base = os.path.join(ROOT, "docs", "testpaket_%s" % args.ref)
    print(build(args.ref, args.box, base + ".pdf", base + ".json"))


if __name__ == "__main__":
    main()
