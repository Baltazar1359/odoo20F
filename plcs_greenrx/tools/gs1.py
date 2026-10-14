"""Parser für Packungscodes (securPharm) im GS1- und im IFA-Format.

Reines Python ohne Odoo-Abhängigkeit, damit es einzeln getestet werden kann.

Unterstützt:
- GS1 DataMatrix: AI 01 (GTIN), 21 (Seriennummer), 10 (Charge), 17 (Verfall),
  dazu weitere gängige AIs. Trennzeichen: FNC1/GS (ASCII 29) oder die
  Ersatzzeichen, die Handscanner oder die Kamera liefern (#, Alt029, <GS>).
- Klammer-Schreibweise für Handeingabe: (01)04150...(21)...(10)...(17)...
- IFA-Format (ASC MH10.8.2, "[)>" RS "06" ...): 9N PPN, 8P GTIN, 1T Charge,
  D Verfall, S Seriennummer.
- Fehlende Trennzeichen: wenn nur eine Lesart möglich ist, wird sie genommen
  (markiert als "heuristic"), sonst Fehler.
"""

import calendar
import datetime
import re

FNC1 = "\x1d"
RS = "\x1e"
EOT = "\x04"

# Ersatzdarstellungen für das Trennzeichen (wie Odoo: "(Alt029|#|\x1D)")
_SEPARATOR_ALIASES = ("Alt029", "<GS>", "{GS}", "[GS]", "^]", "#")
_SYMBOLOGY_IDS = ("]d2", "]C1", "]Q3", "]e0", "]J1", "]d1")

# AI -> (feste Länge oder None für variabel, maximale Länge, Name)
_AI = {
    "00": (18, 18, "sscc"),
    "01": (14, 14, "gtin"),
    "02": (14, 14, "content_gtin"),
    "10": (None, 20, "batch"),
    "11": (6, 6, "prod_date"),
    "13": (6, 6, "pack_date"),
    "15": (6, 6, "best_before"),
    "16": (6, 6, "sell_by"),
    "17": (6, 6, "expiry"),
    "20": (2, 2, "variant"),
    "21": (None, 20, "serial"),
    "22": (None, 20, "cpv"),
    "240": (None, 30, "additional_id"),
    "241": (None, 30, "customer_part"),
    "30": (None, 8, "count"),
    "37": (None, 8, "count_items"),
    "710": (None, 20, "nhrn_de"),
    "711": (None, 20, "nhrn_fr"),
    "712": (None, 20, "nhrn_es"),
    "713": (None, 20, "nhrn_br"),
    "714": (None, 20, "nhrn_pt"),
}
_DATE_AIS = {"11", "13", "15", "16", "17"}


class Gs1Error(ValueError):
    """Code kann nicht gelesen werden. Die Meldung ist für das Lager gedacht."""


def gtin_check_digit_ok(gtin):
    if not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in gtin]
    check = digits.pop()
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def gtin_to_pzn(gtin):
    """NTIN mit Präfix 4150 enthält die PZN: 0 4150 PPPPPPPP C."""
    if gtin and len(gtin) == 14 and gtin.startswith("04150"):
        return gtin[5:13]
    if gtin and len(gtin) == 13 and gtin.startswith("4150"):
        return gtin[4:12]
    return False


def pzn_to_gtin(pzn):
    """PZN (8-stellig) -> GTIN-14 (NTIN 4150...). Gibt False zurück bei ungültiger PZN."""
    pzn = (pzn or "").strip()
    if not pzn.isdigit():
        return False
    pzn = pzn.zfill(8)
    if len(pzn) != 8:
        return False
    body = "04150" + pzn
    for c in "0123456789":
        if gtin_check_digit_ok(body + c):
            return body + c
    return False


def ppn_to_pzn(ppn):
    """PPN = 11 + PZN (8) + 2 Prüfzeichen."""
    if ppn and len(ppn) == 12 and ppn.startswith("11"):
        return ppn[2:10]
    return False


def parse_yymmdd(value):
    """GS1-Datum YYMMDD. Tag 00 = letzter Tag des Monats."""
    if not (value and len(value) == 6 and value.isdigit()):
        raise Gs1Error("Datum %r hat nicht das Format JJMMTT." % value)
    year = 2000 + int(value[0:2])
    month = int(value[2:4])
    day = int(value[4:6])
    if not 1 <= month <= 12:
        raise Gs1Error("Monat %02d im Datum %s ist ungültig." % (month, value))
    if day == 0:
        day = calendar.monthrange(year, month)[1]
    try:
        return datetime.date(year, month, day)
    except ValueError as e:
        raise Gs1Error("Datum %s ist ungültig." % value) from e


def normalize(raw):
    """Scanner-Eigenheiten entfernen und Trennzeichen vereinheitlichen."""
    if raw is None:
        raise Gs1Error("Leerer Scan.")
    code = str(raw)
    code = code.replace("\r", "").replace("\n", "").replace("\t", "")
    for alias in _SEPARATOR_ALIASES:
        code = code.replace(alias, FNC1)
    code = code.strip(" ")
    for sid in _SYMBOLOGY_IDS:
        if code.startswith(sid):
            code = code[len(sid):]
            break
    return code


def _match_ai(code, pos):
    for length in (2, 3, 4):
        ai = code[pos:pos + length]
        if ai in _AI:
            return ai
    return None


def _value_ok(ai, value):
    fixed, max_len, _name = _AI[ai]
    if not value:
        return False
    if fixed and len(value) != fixed:
        return False
    if len(value) > max_len:
        return False
    if ai in ("01", "02", "00"):
        return value.isdigit() and (ai == "00" or gtin_check_digit_ok(value))
    if ai in _DATE_AIS:
        try:
            parse_yymmdd(value)
        except Gs1Error:
            return False
    if ai in ("30", "37", "20"):
        return value.isdigit()
    return True


def _parse_with_separators(code):
    result = {}
    pos = 0
    n = len(code)
    while pos < n:
        if code[pos] == FNC1:
            pos += 1
            continue
        ai = _match_ai(code, pos)
        if not ai:
            raise Gs1Error("Unbekannter Datenbezeichner an Stelle %d: %r" % (pos + 1, code[pos:pos + 4]))
        pos += len(ai)
        fixed, max_len, _name = _AI[ai]
        if fixed:
            value = code[pos:pos + fixed]
            pos += fixed
        else:
            end = code.find(FNC1, pos)
            if end == -1:
                end = n
            value = code[pos:end]
            pos = end
        if not _value_ok(ai, value):
            raise Gs1Error("Wert für AI (%s) ist ungültig: %r" % (ai, value))
        if ai in result and result[ai] != value:
            raise Gs1Error("AI (%s) kommt zweimal mit unterschiedlichem Wert vor." % ai)
        result[ai] = value
    return result


def _parse_without_separators(code, pos=0, acc=None, solutions=None, limit=5):
    """Alle Lesarten suchen, wenn Trennzeichen fehlen."""
    if acc is None:
        acc = {}
    if solutions is None:
        solutions = []
    if len(solutions) >= limit:
        return solutions
    if pos == len(code):
        solutions.append(dict(acc))
        return solutions
    for length in (2, 3, 4):
        ai = code[pos:pos + length]
        if ai not in _AI or ai in acc:
            continue
        fixed, max_len, _name = _AI[ai]
        start = pos + length
        if fixed:
            ends = [start + fixed]
        else:
            ends = range(start + 1, min(len(code), start + max_len) + 1)
        for end in ends:
            if end > len(code):
                continue
            value = code[start:end]
            if not _value_ok(ai, value):
                continue
            acc[ai] = value
            _parse_without_separators(code, end, acc, solutions, limit)
            del acc[ai]
    return solutions


def _parse_ifa(code):
    """ASC MH10.8.2 / IFA-Format 06."""
    body = code
    body = body.replace(EOT, "")
    if body.startswith("[)>"):
        body = body[3:]
    body = body.strip(RS)
    if body.startswith("06"):
        body = body[2:]
    parts = [p for p in re.split("[%s%s]" % (FNC1, RS), body) if p]
    if len(parts) < 2:
        raise Gs1Error("IFA-Code ohne Trennzeichen kann nicht gelesen werden. Scanner-Einstellung prüfen.")
    data = {}
    for part in parts:
        for di, key in (("9N", "ppn"), ("8P", "gtin"), ("1T", "batch"), ("16D", "prod_date"), ("D", "expiry"), ("S", "serial")):
            if part.startswith(di):
                data[key] = part[len(di):]
                break
    out = {"format": "ifa"}
    if data.get("gtin"):
        gtin = data["gtin"].zfill(14)
        out["gtin"] = gtin
    if data.get("ppn"):
        out["ppn"] = data["ppn"]
        out["pzn"] = ppn_to_pzn(data["ppn"])
    if data.get("batch"):
        out["batch"] = data["batch"]
    if data.get("serial"):
        out["serial"] = data["serial"]
    if data.get("expiry"):
        out["expiry"] = parse_yymmdd(data["expiry"])
    return out


def parse(raw):
    """Packungscode lesen.

    Rückgabe: dict mit gtin, pzn, serial, batch, expiry (datetime.date),
    format ('gs1' | 'ifa'), heuristic (bool), ais (Rohwerte).
    """
    code = normalize(raw)
    if not code:
        raise Gs1Error("Leerer Scan.")

    if code.startswith("[)>") or code.startswith(RS + "06") or code.startswith("06" + FNC1):
        return _parse_ifa(code)

    heuristic = False
    if code.startswith("("):
        # Klammer-Schreibweise (Handeingabe)
        pairs = re.findall(r"\((\d{2,4})\)([^(]*)", code)
        if not pairs:
            raise Gs1Error("Klammer-Schreibweise nicht lesbar.")
        code = FNC1.join(ai + val for ai, val in pairs)

    if FNC1 in code:
        ais = _parse_with_separators(code)
    else:
        # Ohne Trennzeichen alle Lesarten prüfen. Eindeutig ist der Code nur,
        # wenn genau eine Lesart Seriennummer und GTIN enthält.
        solutions = _parse_without_separators(code)
        wanted = [s for s in solutions if "01" in s and "21" in s]
        if len(wanted) == 1:
            ais = wanted[0]
            variable = [ai for ai in ais if not _AI[ai][0]]
            # nur ein variables Feld (am Ende) = sicher; sonst als geraten markieren
            heuristic = len(variable) > 1
        elif not wanted:
            raise Gs1Error("Code ist kein lesbarer GS1-Packungscode.")
        else:
            raise Gs1Error(
                "Im Code fehlen die Trennzeichen (GS). Seriennummer und Charge "
                "lassen sich nicht eindeutig trennen. Scanner auf GS1/FNC1-Ausgabe einstellen."
            )

    out = {"format": "gs1", "heuristic": heuristic, "ais": ais}
    if "01" in ais:
        out["gtin"] = ais["01"]
        out["pzn"] = gtin_to_pzn(ais["01"])
    if "710" in ais:
        nhrn = ais["710"]
        out["nhrn"] = nhrn
        if not out.get("pzn"):
            out["pzn"] = ppn_to_pzn(nhrn) or (nhrn if len(nhrn) == 8 and nhrn.isdigit() else False)
    if "21" in ais:
        out["serial"] = ais["21"]
    if "10" in ais:
        out["batch"] = ais["10"]
    if "17" in ais:
        out["expiry"] = parse_yymmdd(ais["17"])
    return out


_BOX_RE = re.compile(r"^\s*(?:GX)?\s*0*(\d{1,3})\s*$", re.IGNORECASE)


def parse_box_code(raw, prefix="GX", digits=3, allow_number_only=False):
    """Box-Code GX001 lesen. Mit allow_number_only (Handeingabe) auch '12' -> GX012.

    Gibt den normierten Namen zurück oder False.
    """
    if raw is None:
        return False
    code = str(raw).strip().upper()
    if code.startswith(prefix):
        rest = code[len(prefix):]
        if rest.isdigit() and len(rest) == digits:
            return prefix + rest
        return False
    if allow_number_only and code.isdigit() and 1 <= len(code) <= digits:
        return prefix + code.zfill(digits)
    return False


# ----------------------------------------------------------------------
# Handeingabe einer Packung (Code nicht lesbar)
# ----------------------------------------------------------------------
# GS1-Zeichensatz 82 (für Seriennummer und Charge), ohne Leerzeichen
_GS1_CS82 = re.compile(r"^[A-Za-z0-9!\"%&'()*+,\-./:;<=>?_]{1,20}$")
_EXPIRY_MONTH_RE = re.compile(r"^(\d{1,2})\s*[./-]\s*(\d{2}|\d{4})$")
_EXPIRY_DAY_RE = re.compile(r"^(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{2}|\d{4})$")


def _year(value):
    year = int(value)
    return 2000 + year if year < 100 else year


def parse_manual_expiry(value):
    """Verfall von Hand: MM/JJJJ, MM/JJ, MM.JJJJ oder TT.MM.JJJJ.

    Rückgabe: (datetime.date, nur_monat). Ohne Tag gilt der letzte Tag des Monats
    (wie Tag 00 im GS1-Code).
    """
    text = (value or "").strip()
    if not text:
        raise Gs1Error("Verfall fehlt (Format MM/JJJJ).")
    match = _EXPIRY_DAY_RE.match(text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), _year(match.group(3))
        try:
            return datetime.date(year, month, day), False
        except ValueError as e:
            raise Gs1Error("Verfall %s ist kein gültiges Datum." % text) from e
    match = _EXPIRY_MONTH_RE.match(text)
    if match:
        month, year = int(match.group(1)), _year(match.group(2))
        if not 1 <= month <= 12:
            raise Gs1Error("Monat %02d im Verfall %s ist ungültig." % (month, text))
        return datetime.date(year, month, calendar.monthrange(year, month)[1]), True
    raise Gs1Error("Verfall %s nicht lesbar. Bitte als MM/JJJJ eingeben, z. B. 05/2028." % text)


def parse_manual(code, serial, batch, expiry):
    """Packungsdaten von Hand prüfen und wie einen Scan zurückgeben.

    code: PZN (8-stellig, auch mit "PZN"/Leerzeichen/Bindestrich) oder GTIN (13/14 Stellen).
    serial: Seriennummer, Groß-/Kleinschreibung zählt.
    Rückgabe: dict wie parse() mit format 'manual'; zusätzlich expiry_month_only.
    """
    errors = []
    gtin = pzn = False
    digits = re.sub(r"[\s\-]", "", (code or "").strip())
    if digits.upper().startswith("PZN"):
        digits = digits[3:]
    if not digits:
        errors.append("PZN fehlt.")
    elif not digits.isdigit():
        errors.append("PZN darf nur Ziffern enthalten.")
    elif len(digits) in (13, 14):
        gtin = digits.zfill(14)
        if not gtin_check_digit_ok(gtin):
            errors.append("GTIN %s: Prüfziffer stimmt nicht. Bitte noch einmal ablesen." % digits)
        else:
            pzn = gtin_to_pzn(gtin)
    elif len(digits) <= 8:
        pzn = digits.zfill(8)
        gtin = pzn_to_gtin(pzn)
        if not _pzn_check_ok(pzn):
            errors.append("PZN %s: Prüfziffer stimmt nicht. Bitte noch einmal ablesen." % pzn)
    else:
        errors.append("PZN hat 8 Stellen, GTIN 13 oder 14 Stellen.")

    serial = (serial or "").strip()
    if not serial:
        errors.append("Seriennummer fehlt.")
    elif not _GS1_CS82.match(serial):
        errors.append("Seriennummer: höchstens 20 Zeichen, nur Buchstaben, Ziffern und - . / ohne Leerzeichen.")

    batch = (batch or "").strip()
    if not batch:
        errors.append("Charge fehlt.")
    elif not _GS1_CS82.match(batch):
        errors.append("Charge: höchstens 20 Zeichen, ohne Leerzeichen.")

    exp_date, month_only = False, False
    try:
        exp_date, month_only = parse_manual_expiry(expiry)
    except Gs1Error as e:
        errors.append(str(e))

    if errors:
        raise Gs1Error(" ".join(errors))
    return {
        "format": "manual",
        "heuristic": False,
        "gtin": gtin,
        "pzn": pzn,
        "serial": serial,
        "batch": batch,
        "expiry": exp_date,
        "expiry_month_only": month_only,
    }


def _pzn_check_ok(pzn):
    """PZN-8: Prüfziffer = Summe(Ziffer_i * (i+1)) mod 11 über die ersten 7 Stellen; 10 ist ungültig."""
    if not (pzn and len(pzn) == 8 and pzn.isdigit()):
        return False
    total = sum(int(c) * (i + 1) for i, c in enumerate(pzn[:7]))
    check = total % 11
    return check != 10 and check == int(pzn[7])
