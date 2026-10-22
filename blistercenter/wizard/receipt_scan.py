"""Packungen im Wareneingang erfassen: per Handscanner (DataMatrix) oder von Hand.

Jeder Scan zählt eine Packung: Artikel (über GTIN/NTIN bzw. PZN), Charge und Verfall kommen aus dem Code.
Dieselbe Charge mehrfach gescannt erhöht nur die Menge; bei vielen gleichen Packungen genügt ein Scan und
die Menge wird eingetippt. Ist der Code nicht lesbar, werden Artikel, Charge, Verfall und Menge von Hand
eingetragen („Hinzufügen“). „Übernehmen“ schreibt die Zeilen in den Wareneingang; gebucht wird wie gewohnt.

1.6.0 (Test Mehrdad 22.10.2026: Handeingabe fehlte, Handscanner ging nicht):
- Handeingabe im selben Fenster (R55).
- Scan-Feld wandelt die Trennzeichen-Tasten des Handscanners in das GS-Zeichen um und arbeitet jede
  Packung nach „Enter“ der Reihe nach ab (Feld-Widget bc_scan_input, R56).
- Code ohne Trennzeichen: Gilt für alle möglichen Lesarten derselbe Artikel, dieselbe Charge und derselbe
  Verfall, wird die Packung gezählt. Sonst werden Artikel und Verfall in die Handeingabe übernommen und
  die Charge wird von der Packung abgeschrieben (R57).
- Übersicht je Artikel: bestellt, schon im Eingang, in dieser Liste (R58).
"""
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..tools import bc_auth, gs1

# Höchstzahl Lesarten, die bei einem Code ohne Trennzeichen verglichen werden
_MAX_READINGS = 50

# 1.6.1: So kommt das GS-Trennzeichen bei Handscannern im Tastaturmodus oft an.
# Windows setzt für Alt+29 (ohne führende 0) das Zeichen der Codepage 437 ein: „↔“.
_GS_LOOKALIKES = ("\u2194", "\u241d")
# Symbologie-Kennung am Anfang (]d2 = GS1-DataMatrix). Mit US-Tastatur-Emulation auf deutschem
# Windows wird aus „]“ ein „+“.
_SYMBOLOGY_PREFIXES = ("]d2", "]d1", "]C1", "]Q3", "]e0", "+d2", "+d1", "+C1", "+Q3", "+e0")
# Zeichen, die in Packungscodes nicht vorkommen, aber entstehen, wenn der Scanner eine andere
# Tastatursprache als der PC nutzt
_LAYOUT_SIGNS = set("ßäöüÄÖÜ´`§°")


def clean_scanner_input(raw):
    """Eingabe des Handscanners bereinigen (1.6.1). Rückgabe: (Code, Hinweise)."""
    hints = []
    code = (raw or "").replace("\r", "").replace("\n", "")
    for sign in _GS_LOOKALIKES:
        code = code.replace(sign, gs1.FNC1)
    code = code.strip(" \t")
    for prefix in _SYMBOLOGY_PREFIXES:
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    # Trennzeichen direkt am Anfang ist das FNC1 der GS1-DataMatrix
    code = code.lstrip(gs1.FNC1)
    if _LAYOUT_SIGNS & set(code):
        hints.append("Der Code enthält Zeichen wie ß/ä/ö/ü: Tastatursprache des Scanners auf Deutsch stellen.")
    return code, hints


def visible(raw, limit=90):
    """Scan für die Anzeige: Steuerzeichen sichtbar machen."""
    out = []
    for ch in raw or "":
        if ch == gs1.FNC1:
            out.append("‹GS›")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("‹%02X›" % ord(ch))
        else:
            out.append(ch)
    text = "".join(out)
    return text if len(text) <= limit else text[:limit] + "…"


class BlistercenterReceiptScan(models.TransientModel):
    _name = "blistercenter.receipt.scan"
    _description = "Packungen im Wareneingang erfassen"

    picking_id = fields.Many2one("stock.picking", "Wareneingang", required=True, readonly=True)
    scan = fields.Char("Scan", help="DataMatrix-Code der Packung mit dem Handscanner lesen.")
    message = fields.Char("Meldung", readonly=True)
    message_type = fields.Selection(
        [("ok", "OK"), ("warning", "Hinweis"), ("error", "Fehler")], readonly=True)
    line_ids = fields.One2many("blistercenter.receipt.scan.line", "scan_id", "Erfasste Packungen")
    applied = fields.Boolean("Übernommen", readonly=True, copy=False)

    # Handeingabe (R55)
    allowed_product_ids = fields.Many2many(
        "product.product", string="Artikel dieses Eingangs", compute="_compute_allowed_product_ids")
    manual_product_id = fields.Many2one(
        "product.product", "Artikel", domain="[('id', 'in', allowed_product_ids)]")
    manual_lot = fields.Char("Charge", help="Charge so, wie sie auf der Packung steht (Ch.-B. / Lot).")
    manual_expiry = fields.Char(
        "Verfall", help="Wie auf der Packung: MM/JJJJ (z. B. 05/2028) oder TT.MM.JJJJ.")
    manual_qty = fields.Integer("Anzahl Packungen", default=1)

    overview = fields.Html("Übersicht", compute="_compute_overview", sanitize=False)

    # ------------------------------------------------------------------
    # Anzeige
    # ------------------------------------------------------------------
    @api.depends("picking_id")
    def _compute_allowed_product_ids(self):
        for wizard in self:
            wizard.allowed_product_ids = wizard._bc_products()

    @api.depends("picking_id", "line_ids.product_id", "line_ids.qty")
    def _compute_overview(self):
        for wizard in self:
            moves = wizard.picking_id.move_ids.filtered(lambda m: m.state != "cancel")
            if not moves:
                wizard.overview = False
                continue
            rows = []
            for product in moves.product_id:
                product_moves = moves.filtered(lambda m, p=product: m.product_id == p)
                ordered = sum(product_moves.mapped("product_uom_qty"))
                booked = sum(product_moves.move_line_ids.filtered(
                    lambda ml: ml.lot_id or ml.lot_name).mapped("quantity"))
                listed = sum(wizard.line_ids.filtered(lambda l, p=product: l.product_id == p).mapped("qty"))
                total = booked + listed
                if total == ordered:
                    state, css = self.env._("vollständig"), "text-success"
                elif total > ordered:
                    state, css = self.env._("mehr als bestellt"), "text-warning"
                else:
                    state, css = self.env._("fehlt %(qty)s", qty="%g" % (ordered - total)), "text-muted"
                rows.append(Markup(
                    "<tr><td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s</td>"
                    "<td class='text-end fw-bold'>%s</td><td class='%s'>%s</td></tr>") % (
                    product.display_name, "%g" % ordered, "%g" % booked, "%g" % listed, css, state))
            wizard.overview = Markup(
                "<table class='table table-sm mb-0'><thead><tr><th>%s</th><th class='text-end'>%s</th>"
                "<th class='text-end'>%s</th><th class='text-end'>%s</th><th></th></tr></thead>"
                "<tbody>%s</tbody></table>") % (
                self.env._("Artikel"), self.env._("Bestellt"), self.env._("Schon im Eingang"),
                self.env._("In dieser Liste"), Markup("").join(rows))

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------
    def _bc_products(self):
        return self.picking_id.move_ids.filtered(lambda m: m.state not in ("done", "cancel")).product_id

    def _bc_find_product(self, data):
        gtin, pzn = data.get("gtin"), data.get("pzn")
        for product in self._bc_products():
            codes = {c for c in (product.barcode, product.default_code,
                                 "plcs_pzn" in product._fields and product.plcs_pzn) if c}
            if gtin and (gtin in codes or gtin.lstrip("0") in {c.lstrip("0") for c in codes}):
                return product
            if pzn and pzn in codes:
                return product
        return self.env["product.product"]

    def _bc_set_message(self, text, kind):
        self.message, self.message_type = text, kind

    def _bc_add_packs(self, product, lot_name, expiry, qty):
        """Packungen in die Liste aufnehmen. Gibt (Zeile, Fehlertext) zurück."""
        line = self.line_ids.filtered(lambda l: l.product_id == product and l.lot_name == lot_name)[:1]
        if line and line.expiration_date != expiry:
            # Prüfbericht 21.10.2026 Befund 2: widersprüchliches Verfallsdatum nicht still überschreiben
            return line, self.env._(
                "%(product)s · Charge %(lot)s: Verfall %(new)s weicht von der ersten Packung ab (%(first)s). "
                "Packung nicht gezählt – bitte prüfen.",
                product=product.display_name, lot=lot_name, new=expiry.strftime("%d.%m.%Y"),
                first=line.expiration_date.strftime("%d.%m.%Y") if line.expiration_date else "-")
        if line:
            line.qty += qty
            return line, False
        vals = {"product_id": product.id, "lot_name": lot_name, "expiration_date": expiry, "qty": qty}
        Line = self.env["blistercenter.receipt.scan.line"]
        if not isinstance(self.id, int):
            # Formular im Browser (Onchange): Zeile nur im Formular, gespeichert wird mit dem Fenster
            self.line_ids |= Line.new(vals)
        else:
            Line.create(dict(vals, scan_id=self.id))
        line = self.line_ids.filtered(lambda l: l.product_id == product and l.lot_name == lot_name)[:1]
        return line, False

    def _bc_readings_without_gs(self, raw):
        """Lesarten eines Codes ohne Trennzeichen (R57).

        Rückgabe: (Lesarten, vollständig). „vollständig“ ist falsch, wenn die Suche vorzeitig endete
        (Rechenbudget oder Höchstzahl erreicht); dann ist keine Lesart sicher eindeutig.
        """
        try:
            code = gs1.normalize(raw)
        except gs1.Gs1Error:
            return [], False
        if not code or gs1.FNC1 in code or code.startswith(("(", "[)>", gs1.RS)):
            return [], False
        budget = {"left": gs1._SEARCH_BUDGET, "exhausted": False}
        solutions = gs1._parse_without_separators(code, limit=_MAX_READINGS, budget=budget)
        complete = not budget["exhausted"] and len(solutions) < _MAX_READINGS
        return self._bc_as_readings(solutions), complete

    def _bc_as_readings(self, solutions):
        readings = []
        for ais in solutions:
            if not ("10" in ais and "17" in ais):
                continue
            try:
                expiry = gs1.parse_yymmdd(ais["17"])
            except gs1.Gs1Error:
                continue
            gtin = ais["01"]
            readings.append({"gtin": gtin, "pzn": gs1.gtin_to_pzn(gtin), "batch": ais["10"], "expiry": expiry})
        return readings

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------
    @api.onchange("scan")
    def _onchange_scan(self):
        original = self.scan
        if not original:
            return
        self.scan = False
        raw, hints = clean_scanner_input(original)
        self._bc_process_scan(raw)
        if self.message_type == "error":
            # 1.6.1: zeigen, was der Scanner geliefert hat (Fehlersuche ohne Entwickler)
            extra = [self.env._("Gelesen: %(code)s", code=visible(original))] + hints
            try:
                has_gs = gs1.FNC1 in gs1.normalize(raw)
            except gs1.Gs1Error:
                has_gs = False
            if not has_gs:
                extra.append(self.env._("Kein Trennzeichen (‹GS›) angekommen."))
            self.message = "%s %s" % (self.message, " · ".join(extra))

    @api.onchange("manual_lot", "manual_expiry")
    def _onchange_manual_scan(self):
        """1.6.1: Scan landet im Feld „Charge“ oder „Verfall“ (Cursor stand dort): als Scan behandeln."""
        for name in ("manual_lot", "manual_expiry"):
            value = self[name] or ""
            code, _hints = clean_scanner_input(value)
            if len(code) >= 20 and (code[:2] == "01" and code[2:16].isdigit() or code.startswith("[)>")):
                self[name] = False
                self.scan = value
                self._onchange_scan()
                return

    def _bc_process_scan(self, raw):
        if not raw:
            self._bc_set_message(self.env._("Leerer Scan."), "error")
            return
        try:
            data = gs1.parse(raw)
        except gs1.Gs1Error as exc:
            self._bc_scan_without_gs(raw, str(exc))
            return
        if not (data.get("batch") and data.get("expiry")):
            product = self._bc_find_product(data)
            self.manual_product_id = product
            self.manual_lot = data.get("batch") or False
            self.manual_expiry = data["expiry"].strftime("%m/%Y") if data.get("expiry") else False
            self._bc_set_message(self.env._(
                "Im Code fehlen Charge oder Verfall. Bitte von der Packung abschreiben und „Hinzufügen“."),
                "error")
            return
        product = self._bc_find_product(data)
        if not product:
            self._bc_set_message(self.env._(
                "PZN %(pzn)s gehört nicht zu diesem Wareneingang.", pzn=data.get("pzn") or data.get("gtin") or "-"),
                "error")
            return
        line, error = self._bc_add_packs(product, data["batch"], data["expiry"], 1)
        if error:
            self._bc_set_message(error, "error")
            return
        self._bc_set_message(self._bc_ok_text(product, line), "ok")
        if data.get("heuristic"):
            self.message += " " + self.env._("(Code ohne Trennzeichen gelesen – Charge bitte prüfen.)")
            self.message_type = "warning"
        elif line.warning:
            self.message_type = "warning"

    def _bc_ok_text(self, product, line):
        return self.env._(
            "%(product)s · Charge %(lot)s · Verfall %(exp)s · Menge %(qty)s",
            product=product.display_name, lot=line.lot_name, exp=line.expiration_date.strftime("%d.%m.%Y"),
            qty="%g" % line.qty)

    def _bc_scan_without_gs(self, raw, parse_error):
        """Code ohne Trennzeichen: nur zählen, wenn alle Lesarten dasselbe ergeben (R57)."""
        readings, complete = self._bc_readings_without_gs(raw)
        if not readings:
            self._bc_set_message(parse_error, "error")
            return
        matching = []
        for reading in readings:
            product = self._bc_find_product(reading)
            if product:
                matching.append((product, reading))
        if not matching:
            self._bc_set_message(self.env._(
                "PZN %(pzn)s gehört nicht zu diesem Wareneingang.",
                pzn=readings[0]["pzn"] or readings[0]["gtin"]), "error")
            return
        results = {(p.id, r["batch"], r["expiry"]) for p, r in matching}
        product, reading = matching[0]
        if len(results) == 1 and complete:
            line, error = self._bc_add_packs(product, reading["batch"], reading["expiry"], 1)
            if error:
                self._bc_set_message(error, "error")
                return
            self._bc_set_message(self._bc_ok_text(product, line) + " " + self.env._(
                "(Code ohne Trennzeichen gelesen – Charge bitte mit der Packung vergleichen.)"), "warning")
            return
        # Mehrere Lesarten: was eindeutig ist, in die Handeingabe übernehmen
        products = {p.id for p, _r in matching}
        expiries = {r["expiry"] for _p, r in matching}
        self.manual_product_id = product if len(products) == 1 else False
        self.manual_expiry = expiries.pop().strftime("%m/%Y") if len(expiries) == 1 else False
        self.manual_lot = False
        self.manual_qty = 1
        self._bc_set_message(self.env._(
            "Der Scanner liefert keine Trennzeichen, die Charge ist nicht sicher lesbar. Bitte unten die Charge "
            "von der Packung abschreiben und „Hinzufügen“. Dauerhaft: Scanner auf GS1-Ausgabe einstellen "
            "(siehe Prüfanleitung)."), "error")

    # ------------------------------------------------------------------
    # Handeingabe (R55)
    # ------------------------------------------------------------------
    def action_add_manual(self):
        self.ensure_one()
        self._bc_check_open()
        product = self.manual_product_id
        lot_name = (self.manual_lot or "").strip()
        errors = []
        if not product:
            errors.append(self.env._("Artikel fehlt."))
        elif product not in self._bc_products():
            errors.append(self.env._("%(product)s gehört nicht zu diesem Wareneingang.",
                                     product=product.display_name))
        if not lot_name:
            errors.append(self.env._("Charge fehlt."))
        elif len(lot_name) > 20 or " " in lot_name:
            errors.append(self.env._("Charge: höchstens 20 Zeichen, ohne Leerzeichen."))
        expiry = False
        try:
            expiry, _month_only = gs1.parse_manual_expiry(self.manual_expiry)
        except gs1.Gs1Error as exc:
            errors.append(str(exc))
        if self.manual_qty < 1:
            errors.append(self.env._("Anzahl Packungen muss mindestens 1 sein."))
        if errors:
            raise UserError(" ".join(errors))
        line, error = self._bc_add_packs(product, lot_name, expiry, self.manual_qty)
        if error:
            raise UserError(error)
        message = self.env._(
            "Von Hand: %(product)s · Charge %(lot)s · Verfall %(exp)s · Menge jetzt %(qty)s",
            product=product.display_name, lot=lot_name, exp=expiry.strftime("%d.%m.%Y"), qty="%g" % line.qty)
        self.write({
            "manual_lot": False, "manual_expiry": False, "manual_qty": 1,
            "message": message, "message_type": "warning" if line.warning else "ok",
        })
        return self._bc_reopen()

    def _bc_reopen(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Packungen erfassen"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {"dialog_size": "large"},
        }

    def _bc_check_open(self):
        picking = self.picking_id
        if self.applied or picking.bc_kind != "receipt" or picking.state in ("done", "cancel"):
            raise UserError(self.env._("Der Wareneingang ist nicht mehr offen."))

    # ------------------------------------------------------------------
    # Übernehmen
    # ------------------------------------------------------------------
    def action_apply(self):
        self.ensure_one()
        # Befund 5: dasselbe Scan-Fenster nur einmal übernehmen (auch bei doppeltem Klick oder Wiederholung)
        self.flush_recordset(["applied"])
        self.env.cr.execute("SELECT applied FROM blistercenter_receipt_scan WHERE id = %s FOR UPDATE", (self.id,))
        row = self.env.cr.fetchone()
        if row and row[0]:
            return {"type": "ir.actions.act_window_close"}
        picking = self.picking_id
        if picking.bc_kind != "receipt" or picking.state in ("done", "cancel"):
            raise UserError(self.env._("Der Wareneingang ist nicht mehr offen."))
        lines = self.line_ids.filtered(lambda l: l.qty > 0)
        if not lines:
            raise UserError(self.env._("Noch keine Packung erfasst."))
        self._bc_check_consistent_expiry(lines)
        MoveLine = self.env["stock.move.line"]
        Lot = self.env["stock.lot"]
        for product in lines.product_id:
            move = picking.move_ids.filtered(
                lambda m: m.product_id == product and m.state not in ("done", "cancel"))[:1]
            if not move:
                raise UserError(self.env._("%(product)s gehört nicht zu diesem Wareneingang.",
                                           product=product.display_name))
            # Vorbelegte Zeilen ohne Charge ersetzen; schon erfasste Chargen bleiben
            move.move_line_ids.filtered(lambda ml: not ml.lot_id and not ml.lot_name).unlink()
            for line in lines.filtered(lambda l: l.product_id == product):
                lot_name = (line.lot_name or "").strip()
                if not lot_name or not line.expiration_date:
                    raise UserError(self.env._("%(product)s: Charge und Verfall sind Pflicht.",
                                               product=product.display_name))
                existing = move.move_line_ids.filtered(lambda ml: (ml.lot_id.name or ml.lot_name) == lot_name)[:1]
                if existing:
                    # Nur mit einem schon erfassten Datum vergleichen (Scan oder neu eingegebene Charge).
                    # Eine von Hand gewählte vorhandene Charge hat nur das Stammdatum: dann bleibt es beim
                    # Hinweis (R47), das gescannte Datum wird festgehalten.
                    earlier = existing.bc_scanned_expiry or (
                        not existing.lot_id and existing.expiration_date and existing.expiration_date.date())
                    if earlier and earlier != line.expiration_date:
                        raise UserError(self.env._(
                            "%(product)s · Charge %(lot)s ist in diesem Wareneingang schon mit Verfall %(earlier)s "
                            "erfasst, jetzt wurde %(new)s erfasst. Bitte Packungen prüfen.",
                            product=product.display_name, lot=lot_name,
                            earlier=earlier.strftime("%d.%m.%Y"), new=line.expiration_date.strftime("%d.%m.%Y")))
                    vals = {"quantity": existing.quantity + line.qty}
                    if not existing.bc_scanned_expiry:
                        vals["bc_scanned_expiry"] = line.expiration_date
                    bc_auth.grant(existing, "scan").write(vals)
                    continue
                vals = {
                    "move_id": move.id, "picking_id": picking.id, "product_id": product.id,
                    "uom_id": move.uom_id.id, "location_id": move.location_id.id,
                    "location_dest_id": move.location_dest_id.id, "quantity": line.qty, "picked": True,
                    # Befund 1: erfasstes Datum immer festhalten, auch wenn die Charge schon existiert
                    "bc_scanned_expiry": line.expiration_date,
                }
                lot = Lot.search([("product_id", "=", product.id), ("name", "=", lot_name),
                                  ("company_id", "in", [picking.company_id.id, False])], limit=1)
                if lot:
                    vals["lot_id"] = lot.id
                else:
                    vals.update({"lot_name": lot_name,
                                 "expiration_date": fields.Datetime.to_datetime(line.expiration_date)})
                MoveLine.create(vals)
        self.applied = True
        self.flush_recordset(["applied"])
        picking.message_post(body=self.env._(
            "Erfasst: %(count)s Packungen in %(lots)s Charge(n).",
            count="%g" % sum(lines.mapped("qty")), lots=len(lines)))
        return {"type": "ir.actions.act_window_close"}

    def _bc_check_consistent_expiry(self, lines):
        """Befund 2 auch serverseitig: dieselbe Charge darf nur ein Verfallsdatum haben."""
        dates = {}
        for line in lines:
            key = (line.product_id.id, (line.lot_name or "").strip())
            if key in dates and dates[key] != line.expiration_date:
                raise UserError(self.env._(
                    "%(product)s · Charge %(lot)s steht mit zwei Verfallsdaten in der Liste. Bitte prüfen.",
                    product=line.product_id.display_name, lot=key[1]))
            dates[key] = line.expiration_date


class BlistercenterReceiptScanLine(models.TransientModel):
    _name = "blistercenter.receipt.scan.line"
    _description = "Erfasste Packungen"

    scan_id = fields.Many2one("blistercenter.receipt.scan", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", "Artikel", required=True)
    lot_name = fields.Char("Charge", required=True)
    expiration_date = fields.Date("Verfall", required=True)
    qty = fields.Float("Menge", default=1, digits="Product Unit")
    warning = fields.Char("Hinweis", compute="_compute_warning")

    @api.depends("product_id", "lot_name", "expiration_date")
    def _compute_warning(self):
        Lot = self.env["stock.lot"].sudo()
        for line in self:
            lot = line.product_id and line.lot_name and Lot.search([
                ("product_id", "=", line.product_id.id), ("name", "=", line.lot_name.strip())], limit=1)
            notes = []
            if lot and lot.bc_blocked:
                notes.append(self.env._("Charge gesperrt"))
            if lot and lot.expiration_date and line.expiration_date \
                    and lot.expiration_date.date() != line.expiration_date:
                notes.append(self.env._("bekannte Charge hat Verfall %(date)s",
                                        date=lot.expiration_date.strftime("%d.%m.%Y")))
            line.warning = ", ".join(notes) or False
