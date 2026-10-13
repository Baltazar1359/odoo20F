"""QR-Code des GreenRX-Retourenscheins lesen.

Inhalt (Stand Retourenschein 10/2026):

    REF:V202601693
    REKL:S23124
    TICKET|PZN|CHARGE|ANZAHL
    1001982|13570727|214124|4
    1001983|07560038|33t32|3

Handscanner im Tastaturmodus liefern Zeilenumbrüche oft als Enter und Odoo
teilt Scans zusätzlich an "|" auf. Der Parser arbeitet deshalb mit Feldern
(Tokens) statt mit Zeilen: nach der Kopfzeile folgen immer Gruppen zu vier
Feldern.
"""

import re

HEADER = ["TICKET", "PZN", "CHARGE", "ANZAHL"]
_SPLIT = re.compile(r"[\r\n|;\t\x1d]+")


class SlipError(ValueError):
    """Retourenschein-Code kann nicht gelesen werden."""


def looks_like_slip(raw):
    return bool(raw) and str(raw).strip().upper().startswith("REF:")


def parse(raw):
    """Rückgabe: {"reference", "complaint", "lines": [{ticket, pzn, batch, qty}]}"""
    if not looks_like_slip(raw):
        raise SlipError("Kein Retourenschein-Code (REF: fehlt).")
    text = str(raw).strip()
    # REF/REKL ohne Trennzeichen dazwischen ("REF:V1REKL:S2TICKET...") trennen
    text = re.sub(r"(?i)(REKL:|TICKET\|?)", lambda m: "\n" + m.group(1), text)
    tokens = [t.strip() for t in _SPLIT.split(text) if t.strip()]
    reference = complaint = False
    rest = []
    for token in tokens:
        upper = token.upper()
        if upper.startswith("REF:") and not reference:
            reference = token[4:].strip()
        elif upper.startswith("REKL:") and not complaint:
            complaint = token[5:].strip()
        else:
            rest.append(token)
    if not reference:
        raise SlipError("Referenznummer fehlt im Code.")
    if [t.upper() for t in rest[:4]] == HEADER:
        rest = rest[4:]
    if len(rest) % 4:
        raise SlipError(
            "Die Produktzeilen im QR-Code sind nicht vollständig lesbar. Wahrscheinlich gehen beim "
            "Handscanner die Zeilenumbrüche verloren. Bitte mit der Kamera scannen oder den Scanner so "
            "einstellen, dass Zeilenumbrüche übertragen werden.")
    lines = []
    for i in range(0, len(rest), 4):
        ticket, pzn, batch, qty = rest[i:i + 4]
        if not qty.isdigit():
            raise SlipError("Anzahl „%s“ in Zeile %s ist keine Zahl." % (qty, i // 4 + 1))
        if pzn.isdigit():
            pzn = pzn.zfill(8)
        lines.append({"ticket": ticket, "pzn": pzn, "batch": batch, "qty": int(qty)})
    if not lines:
        raise SlipError("Im Code stehen keine Produkte.")
    return {"reference": reference, "complaint": complaint or False, "lines": lines}
