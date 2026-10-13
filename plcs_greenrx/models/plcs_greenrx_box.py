from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import gs1

AMPEL = [("gruen", "Grün – unauffällig"), ("gelb", "Gelb – auffällig"), ("rot", "Rot – sperren")]
AMPEL_RANK = {"gruen": 0, "gelb": 1, "rot": 2}
AMPEL_SHORT = {"gruen": "Grün", "gelb": "Gelb", "rot": "Rot"}
AMPEL_DESC = {
    "gruen": "An die VP zur Freigabe",
    "gelb": "An die VP, sie entscheidet nach Prüfung",
    "rot": "Rotes Sperrblatt an die Box, ins Sperrregal, VP informiert",
}
ANSWER = [("ja", "Ja"), ("nein", "Nein")]

# Sichtprüfung des Lagers: Feld, Frage (KabQ-Checkliste), Ampel bei "Nein", Grund
QUESTIONS = [
    ("q_damage", "Sind keine offensichtlichen Beschädigungen oder Auffälligkeiten vorhanden?",
     "gelb", "Beschädigungen oder Auffälligkeiten"),
    ("q_temp_controlled", "Wurde die Ware temperaturgeführt transportiert?",
     "rot", "Nicht temperaturgeführt transportiert"),
    ("q_temp_log", "Ist der Temperaturverlauf ohne Abweichungen?",
     "rot", "Abweichung im Temperaturverlauf"),
    ("q_manufacturer", "Wurde die Ware nach den Herstellerangaben ordnungsgemäß transportiert?",
     "rot", "Transport nicht nach Herstellerangaben"),
    ("q_storage_proof", "Liegen die Nachweise zur ordnungsgemäßen Lagerung vor?",
     "gelb", "Nachweise zur Lagerung fehlen"),
    ("q_seal", "Haben alle Packungen einen intakten Originalitätsverschluss?",
     "rot", "Originalitätsverschluss nicht intakt"),
]

OPEN_STATES = ("scan", "check", "print")


class PlcsGreenrxBox(models.Model):
    _name = "plcs.greenrx.box"
    _description = "GreenRX: Erfassung einer Box"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char("Bezeichnung", compute="_compute_name", store=True)
    ticket_id = fields.Many2one("plcs.greenrx.ticket", "Ticket", required=True, index=True, readonly=True)
    picking_id = fields.Many2one(related="ticket_id.picking_id", string="Eingang", store=True)
    company_id = fields.Many2one(related="ticket_id.company_id", store=True)
    package_id = fields.Many2one("stock.package", "Box", required=True, index=True, readonly=True,
                                 domain=[("plcs_is_grx_box", "=", True)])
    box_manual = fields.Boolean("Box von Hand eingegeben", readonly=True)
    state = fields.Selection([
        ("scan", "Packungen scannen"),
        ("check", "Vorprüfung"),
        ("print", "Drucken"),
        ("to_release", "Wartet auf VP"),
        ("blocked", "Gesperrt"),
        ("released", "Freigegeben"),
        ("cancel", "Abgebrochen"),
    ], default="scan", required=True, tracking=True, index=True, readonly=True)
    user_id = fields.Many2one("res.users", "Erfasst von", default=lambda self: self.env.user, readonly=True, index=True)

    # Inhalt (aus dem ersten Scan)
    product_id = fields.Many2one("product.product", "Artikel", readonly=True)
    batch = fields.Char("Charge", readonly=True)
    line_id = fields.Many2one("plcs.greenrx.ticket.line", "Ticket (Position)", readonly=True, index="btree_not_null",
                              help="Position des Retourenscheins, zu der die Box gehört (Artikel + Charge).")
    ticket_number = fields.Char(related="line_id.ticket_number", string="Ticketnummer", store=True)
    slip_match = fields.Selection([("ok", "passt"), ("batch_differs", "Charge weicht ab"),
                                   ("not_on_slip", "nicht auf dem Schein")],
                                  "Abgleich Retourenschein", readonly=True)
    expiry_date = fields.Date("Verfall", readonly=True)
    scan_ids = fields.One2many("plcs.greenrx.scan", "box_id", "Scans", readonly=True)
    accepted_scan_ids = fields.One2many("plcs.greenrx.scan", "box_id", "Packungen in der Box",
                                        domain=[("accepted", "=", True)], readonly=True)
    qty = fields.Integer("Packungen", compute="_compute_qty", store=True)
    lot_ids = fields.One2many("stock.lot", "plcs_grx_box_id", "Seriennummern", readonly=True)

    # Vorprüfung
    q_damage = fields.Selection(ANSWER, "Keine Beschädigungen/Auffälligkeiten", tracking=True)
    q_temp_controlled = fields.Selection(ANSWER, "Temperaturgeführt transportiert", tracking=True)
    q_temp_log = fields.Selection(ANSWER, "Temperaturverlauf ohne Abweichung", tracking=True)
    q_manufacturer = fields.Selection(ANSWER, "Transport nach Herstellerangaben", tracking=True)
    q_storage_proof = fields.Selection(ANSWER, "Nachweise zur Lagerung vorhanden", tracking=True)
    q_seal = fields.Selection(ANSWER, "Originalitätsverschluss intakt", tracking=True)
    ampel_suggest = fields.Selection(AMPEL, "Vorschlag", compute="_compute_suggestion")
    reasons = fields.Text("Gründe (System)", compute="_compute_suggestion")
    ampel = fields.Selection(AMPEL, "Ampel", tracking=True, readonly=True, index=True)
    note = fields.Text("Bemerkung", readonly=True)
    checked_at = fields.Datetime("Vorprüfung am", readonly=True)

    # Druck und Abschluss
    printed_at = fields.Datetime("Gedruckt am", readonly=True)
    printed_by = fields.Many2one("res.users", "Gedruckt von", readonly=True)
    print_count = fields.Integer("Anzahl Drucke", readonly=True)
    signed_confirmed = fields.Boolean("Ausdruck geprüft und unterschrieben", readonly=True)
    content_rev = fields.Integer("Änderungsstand", readonly=True,
                                 help="Zählt jede Änderung an Packungen, Antworten, Ampel und Bemerkung.")
    printed_rev = fields.Integer("Gedruckter Stand", readonly=True,
                                 help="Änderungsstand beim letzten Druck. Weicht er ab, ist ein neuer Druck Pflicht.")
    closed_at = fields.Datetime("Abgeschlossen am", readonly=True)
    move_id = fields.Many2one("stock.move", "Lagerbewegung Eingang", readonly=True)

    # VP
    vp_user_id = fields.Many2one("res.users", "Entschieden von (VP)", readonly=True, tracking=True)
    vp_date = fields.Datetime("Entschieden am", readonly=True)
    vp_reason = fields.Text("Begründung VP", readonly=True)
    vp_picking_id = fields.Many2one("stock.picking", "Freigabe-/Sperrbewegung", readonly=True)
    location_id = fields.Many2one("stock.location", "Lagerort", compute="_compute_location")

    @api.depends("ticket_id.name", "package_id.name")
    def _compute_name(self):
        for box in self:
            box.name = "%s · %s" % (box.ticket_id.name or "?", box.package_id.name or "?")

    @api.depends("scan_ids.accepted")
    def _compute_qty(self):
        for box in self:
            box.qty = len(box.scan_ids.filtered("accepted"))

    @api.depends("package_id.quant_ids.location_id", "package_id.quant_ids.quantity")
    def _compute_location(self):
        for box in self:
            quants = box.package_id.sudo().quant_ids.filtered(lambda q: q.quantity > 0)
            box.location_id = quants[:1].location_id

    # ------------------------------------------------------------------
    # Ampel-Vorschlag
    # ------------------------------------------------------------------
    def _grx_system_checks(self):
        """Prüfungen, die Odoo selbst macht. Liste von dicts (label, ok, level, reason)."""
        self.ensure_one()
        company = self.company_id or self.env.company
        checks = []
        ticket = self.ticket_id
        if self.line_id.ticket_number:
            checks.append({"label": self.env._("Ware ist für uns: Vorgang %(v)s, Ticket %(t)s",
                                               v=ticket.name, t=self.line_id.ticket_number), "ok": True})
        else:
            checks.append({"label": self.env._("Ware ist für uns: Vorgang %s zugeordnet", ticket.name), "ok": True})
        if ticket.partner_id:
            checks.append({"label": self.env._("Absender im Kontaktstamm: %s", ticket.partner_id.display_name),
                           "ok": True})
        else:
            # Der Absender ist im GreenRX-ERP angemeldet und geprüft (Retourenschein/Ticket).
            # Ohne Kontakt in Odoo nur Hinweis, keine Auswirkung auf die Ampel.
            checks.append({"label": self.env._("Absender laut GreenRX-ERP angemeldet (Vorgang %s)", ticket.name),
                           "ok": True})
        checks.append({"label": self.env._("Seriennummern neu, keine doppelt"), "ok": True})
        checks.append({"label": self.env._("Ein Artikel, eine Charge (%s)", self.batch or "—"), "ok": True})
        if self.expiry_date:
            minimum = company.plcs_grx_min_shelf_months or 0
            limit = fields.Date.context_today(self) + relativedelta(months=minimum)
            exp_text = self.expiry_date.strftime("%m/%Y")
            if self.expiry_date >= limit:
                checks.append({"label": self.env._("Verfall %(exp)s (aus Scan): mind. %(m)s Monate",
                                                   exp=exp_text, m=minimum), "ok": True})
            else:
                checks.append({"label": self.env._("Verfall %(exp)s (aus Scan): weniger als %(m)s Monate",
                                                   exp=exp_text, m=minimum),
                               "ok": False, "level": "rot",
                               "reason": self.env._("Verfall %(exp)s – weniger als %(m)s Monate",
                                                    exp=exp_text, m=minimum)})
        if self.product_id:
            if self.slip_match == "not_on_slip":
                checks.append({"label": self.env._("Artikel steht nicht auf dem Retourenschein"), "ok": False,
                               "level": "gelb", "reason": self.env._("Artikel steht nicht auf dem Retourenschein")})
            elif self.slip_match == "batch_differs":
                checks.append({"label": self.env._("Charge weicht vom Retourenschein ab (Schein %(a)s, Scan %(b)s)",
                                                   a=self.line_id.batch_expected or "-", b=self.batch),
                               "ok": False, "level": "gelb",
                               "reason": self.env._("Charge weicht vom Retourenschein ab")})
            row = next((r for r in ticket._grx_soll_ist() if self.line_id and r["line_id"] == self.line_id.id), None)
            if row and row["status"] == "over":
                checks.append({"label": self.env._("Mehr Packungen als angemeldet (%s)", row["status_text"]),
                               "ok": False, "level": "gelb",
                               "reason": self.env._("Mehr Packungen als angemeldet")})
            elif row:
                checks.append({"label": self.env._("Abgleich mit Ticket %(t)s: %(ist)s von %(soll)s Packungen",
                                                   t=row["ticket"] or "-", ist=row["ist"], soll=row["soll"]), "ok": True})
        return checks

    @api.depends(lambda self: [q[0] for q in QUESTIONS] + ["expiry_date", "product_id", "qty", "batch", "slip_match"])
    def _compute_suggestion(self):
        for box in self:
            level, reasons = box._grx_suggestion()
            box.ampel_suggest = level
            box.reasons = "\n".join(reasons)

    def _grx_suggestion(self, answers=None):
        self.ensure_one()
        answers = answers if answers is not None else {q[0]: self[q[0]] for q in QUESTIONS}
        level = "gruen"
        reasons = []
        for check in self._grx_system_checks():
            if not check["ok"]:
                reasons.append(check["reason"])
                if AMPEL_RANK[check["level"]] > AMPEL_RANK[level]:
                    level = check["level"]
        for fname, _text, no_level, reason in QUESTIONS:
            if answers.get(fname) == "nein":
                reasons.append(reason)
                if AMPEL_RANK[no_level] > AMPEL_RANK[level]:
                    level = no_level
        return level, reasons

    # ------------------------------------------------------------------
    # Ablauf
    # ------------------------------------------------------------------
    def _grx_check_state(self, *states):
        for box in self:
            if box.state not in states:
                raise UserError(self.env._(
                    "Box %(box)s ist im Schritt „%(state)s“. Diese Aktion geht hier nicht.",
                    box=box.package_id.name, state=dict(box._fields["state"].selection)[box.state]))

    def _grx_check_owner(self):
        for box in self:
            if box.user_id != self.env.user and not self.env.user.has_group("plcs_greenrx.group_grx_vp"):
                raise AccessError(self.env._("Box %s wird von %s erfasst.", box.package_id.name, box.user_id.name))

    def _grx_changed(self, vals=None):
        """Inhalt der Box ändert sich: Änderungsstand hochzählen, Ausdruck ungültig machen.

        Nach jeder Änderung muss neu gedruckt und der Ausdruck neu bestätigt werden,
        damit Papier und Daten übereinstimmen.
        """
        self.ensure_one()
        vals = dict(vals or {}, content_rev=self.content_rev + 1)
        if self.printed_at or self.signed_confirmed:
            vals.update(printed_at=False, signed_confirmed=False)
            self.sudo().message_post(body=self.env._(
                "Ausdruck Nr. %s ist ungültig, weil die Box danach geändert wurde. Vor dem Abschluss neu drucken.",
                self.print_count))
        self.sudo().write(vals)

    @api.model
    def grx_open_box(self, ticket_id, code, manual=False):
        """Box-QR gescannt (oder von Hand eingegeben) → Erfassung anlegen oder fortsetzen."""
        ticket = self.env["plcs.greenrx.ticket"].browse(ticket_id).exists()
        if not ticket:
            raise UserError(self.env._("Ticket nicht gefunden."))
        if ticket.state == "open":
            ticket.grx_start()
        if ticket.state != "in_progress":
            raise UserError(self.env._("Ticket %s ist nicht in Erfassung.", ticket.name))
        company = ticket.company_id
        prefix = company.plcs_grx_box_prefix or "GX"
        name = gs1.parse_box_code(code, prefix=prefix, allow_number_only=bool(manual))
        maximum = company.plcs_grx_box_max or 200
        if not name or not (1 <= int(name[len(prefix):]) <= maximum):
            raise UserError(self.env._(
                "„%(code)s“ ist keine gültige Box. Erlaubt sind %(p)s001 bis %(p)s%(max)03d.",
                code=code, p=prefix, max=maximum))
        package = self.env["stock.package"].search(
            [("name", "=", name), ("plcs_is_grx_box", "=", True)], limit=1)
        if not package:
            raise UserError(self.env._("Box %s ist in Odoo nicht angelegt.", name))
        resume = self.search([("package_id", "=", package.id), ("state", "in", OPEN_STATES)], limit=1)
        if resume:
            if resume.ticket_id != ticket:
                raise UserError(self.env._(
                    "Box %(box)s wird gerade für Ticket %(t)s befüllt. Bitte eine leere Box nehmen.",
                    box=name, t=resume.ticket_id.name))
            resume._grx_check_owner()
            return resume._grx_ui_data()
        reason = package._plcs_grx_occupied_reason()
        if reason:
            raise UserError(self.env._("Box %(box)s ist nicht leer. %(reason)s Bitte eine leere Box nehmen.",
                                       box=name, reason=reason))
        box = self.sudo().create({
            "ticket_id": ticket.id,
            "package_id": package.id,
            "box_manual": bool(manual),
        })
        if manual:
            box.message_post(body=self.env._("Box-Nummer von Hand eingegeben (QR-Code nicht gescannt)."))
        return box._grx_ui_data()

    def grx_scan(self, raw):
        """Packung gescannt. Rückgabe: Ergebnis für die Oberfläche."""
        self.ensure_one()
        self._grx_check_state("scan")
        self._grx_check_owner()
        Scan = self.env["plcs.greenrx.scan"]
        log = {"box_id": self.id, "raw": (raw or "")[:500]}
        company = self.company_id
        prefix = company.plcs_grx_box_prefix or "GX"

        def reject(result, title, message, level="error"):
            Scan.sudo().create(dict(log, result=result, message=message, accepted=False))
            return {"status": level, "title": title, "message": message, "box": self._grx_ui_data()}

        if gs1.parse_box_code(raw, prefix=prefix):
            return reject("box_code", self.env._("Das ist ein Box-Code"),
                          self.env._("Erst diese Box abschließen. Danach die nächste Box scannen."))
        try:
            parsed = gs1.parse(raw)
        except gs1.Gs1Error as e:
            return reject("parse_error", self.env._("Code nicht lesbar"), str(e))
        log.update({
            "gtin": parsed.get("gtin") or False,
            "pzn": parsed.get("pzn") or False,
            "serial": parsed.get("serial") or False,
            "batch": parsed.get("batch") or False,
            "expiry_date": parsed.get("expiry") or False,
            "code_format": parsed.get("format"),
        })
        if parsed.get("heuristic") and not company.plcs_grx_allow_heuristic:
            return reject("parse_error", self.env._("Trennzeichen fehlen"), self.env._(
                "Der Scanner sendet keine GS-Trennzeichen. Seriennummer und Charge sind nicht sicher "
                "getrennt. Scanner auf GS1-Ausgabe (FNC1) einstellen oder Kamera nutzen."))
        missing = [label for key, label in (("serial", "Seriennummer"), ("batch", "Charge"),
                                             ("expiry", "Verfall")) if not parsed.get(key)]
        if not (parsed.get("gtin") or parsed.get("pzn")):
            missing.insert(0, "GTIN/PZN")
        if missing:
            return reject("incomplete", self.env._("Kein vollständiger securPharm-Code"), self.env._(
                "Im Code fehlt: %s. Bitte den DataMatrix-Code der Packung scannen.", ", ".join(missing)))

        product = self.env["product.product"]._plcs_find_by_code(gtin=parsed.get("gtin"), pzn=parsed.get("pzn"))
        if not product:
            return reject("unknown_product", self.env._("Artikel unbekannt"), self.env._(
                "Kein Artikel mit GTIN %(gtin)s / PZN %(pzn)s im Artikelstamm. Packung ins Klärungsfach "
                "legen und Lagerleitung Bescheid geben.", gtin=parsed.get("gtin") or "-", pzn=parsed.get("pzn") or "-"))
        if len(product) > 1:
            return reject("unknown_product", self.env._("Artikel nicht eindeutig"), self.env._(
                "Mehrere Artikel haben GTIN/PZN %s. Stammdaten bereinigen.", parsed.get("gtin") or parsed.get("pzn")))
        log["product_id"] = product.id
        if product.tracking == "none":
            return reject("unknown_product", self.env._("Artikel ohne Chargenverfolgung"), self.env._(
                "%s ist im Artikelstamm nicht chargen- oder seriennummerngeführt. Stammdaten prüfen lassen.",
                product.display_name))
        serial = parsed["serial"]

        # 1) Doppelscan in dieser Box: kein Fehler, nicht doppelt zählen
        if self.scan_ids.filtered(lambda s: s.accepted and s.serial == serial and s.product_id == product):
            return reject("duplicate_box", self.env._("Doppelscan erkannt"), self.env._(
                "SN %s ist schon in der Box. Wird nicht doppelt gezählt – einfach weiterscannen.", serial),
                level="info")
        # 2) Seriennummer schon im System
        known = self.env["stock.lot"].sudo().search(
            [("product_id", "=", product.id), ("name", "=", serial)], limit=1)
        if known:
            where = known.plcs_grx_box_id.name or known.display_name
            return reject("known_serial", self.env._("Diese Packung ist schon im System"), self.env._(
                "Die Seriennummer %(sn)s wurde schon einmal erfasst (%(where)s). Jede Seriennummer darf nur "
                "einmal im System sein. Packung nicht in die Box legen, sondern ins Klärungsfach, und "
                "Lagerleitung Bescheid geben.", sn=serial, where=where))
        other = Scan.sudo().search([
            ("accepted", "=", True), ("serial", "=", serial), ("product_id", "=", product.id),
            ("box_id", "!=", self.id), ("box_id.state", "in", OPEN_STATES)], limit=1)
        if other:
            return reject("known_serial", self.env._("Diese Packung ist schon erfasst"), self.env._(
                "Die Seriennummer %(sn)s liegt bereits in der offenen Erfassung %(box)s.",
                sn=serial, box=other.box_id.name))
        # 3) Box-Regeln: ein Artikel, eine Charge
        if self.product_id and product != self.product_id:
            return reject("wrong_product", self.env._("Anderer Artikel"), self.env._(
                "%(new)s passt nicht in Box %(box)s (dort liegt %(old)s). Packung zur Seite legen und für "
                "diesen Artikel eine eigene Box nehmen.",
                new=product.display_name, box=self.package_id.name, old=self.product_id.display_name))
        if self.batch and parsed["batch"] != self.batch:
            return reject("wrong_batch", self.env._("Andere Charge"), self.env._(
                "Andere Charge (%(ch)s): Packung nicht in Box %(box)s legen. Zur Seite legen und für "
                "diese Charge eine eigene Box nehmen.", ch=parsed["batch"], box=self.package_id.name))
        if self.expiry_date and parsed["expiry"] != self.expiry_date:
            return reject("wrong_batch", self.env._("Verfall weicht ab"), self.env._(
                "Gleiche Charge, aber anderes Verfallsdatum (%(new)s statt %(old)s). Packung ins Klärungsfach "
                "legen und Lagerleitung Bescheid geben.",
                new=parsed["expiry"].strftime("%m/%Y"), old=self.expiry_date.strftime("%m/%Y")))

        vals = {}
        if not self.product_id:
            line, match = self.ticket_id._grx_match_line(product, parsed["batch"])
            vals = {"product_id": product.id, "batch": parsed["batch"], "expiry_date": parsed["expiry"],
                    "line_id": line.id, "slip_match": match}
        self._grx_changed(vals)
        scan = Scan.sudo().create(dict(log, result="ok", accepted=True))
        result = {"status": "ok", "title": self.env._("Packung erfasst"),
                  "message": self.env._("SN %(sn)s · Ch. %(ch)s · Verf. %(exp)s",
                                        sn=serial, ch=parsed["batch"], exp=parsed["expiry"].strftime("%m/%Y")),
                  "scan_id": scan.id}
        warnings = [c["label"] for c in self._grx_system_checks() if not c["ok"]]
        if warnings:
            result["status"] = "warning"
            result["message"] += " — " + "; ".join(warnings)
        result["box"] = self._grx_ui_data()
        return result

    def grx_remove_scan(self, scan_id):
        """Letzte(n) Packung wieder herausnehmen (vor der Vorprüfung)."""
        self.ensure_one()
        self._grx_check_state("scan")
        self._grx_check_owner()
        scan = self.scan_ids.filtered(lambda s: s.id == scan_id and s.accepted)
        if not scan:
            raise UserError(self.env._("Diese Packung ist nicht in der Box."))
        scan.sudo().write({"accepted": False, "result": "removed",
                    "message": self.env._("Wieder aus der Box genommen von %s", self.env.user.name)})
        vals = {}
        if not self.scan_ids.filtered("accepted"):
            vals = {"product_id": False, "batch": False, "expiry_date": False, "line_id": False, "slip_match": False}
        self._grx_changed(vals)
        return self._grx_ui_data()

    def grx_to_check(self):
        self.ensure_one()
        self._grx_check_state("scan")
        self._grx_check_owner()
        if not self.qty:
            raise UserError(self.env._("Erst mindestens eine Packung scannen."))
        self.sudo().state = "check"
        return self._grx_ui_data()

    def grx_back(self):
        """Einen Schritt zurück. Ein vorhandener Ausdruck wird damit ungültig."""
        self.ensure_one()
        self._grx_check_owner()
        if self.state == "check":
            self._grx_changed({"state": "scan"})
        elif self.state == "print":
            self._grx_changed({"state": "check"})
        else:
            raise UserError(self.env._("Zurück ist in diesem Schritt nicht möglich."))
        return self._grx_ui_data()

    def grx_save_check(self, answers=None, ampel=None, note=None):
        """Antworten der Sichtprüfung speichern (ohne weiterzugehen)."""
        self.ensure_one()
        self._grx_check_state("check")
        self._grx_check_owner()
        vals = {}
        for fname, *_rest in QUESTIONS:
            if answers and fname in answers:
                value = answers[fname] or False
                if value not in (False, "ja", "nein"):
                    raise UserError(self.env._("Ungültige Antwort."))
                vals[fname] = value
        if ampel is not None:
            vals["ampel"] = ampel or False
        if note is not None:
            vals["note"] = note or False
        changed = {k: v for k, v in vals.items() if self[k] != v}
        if changed:
            self._grx_changed(changed)
        # Ampel darf nicht milder sein als der Vorschlag
        if self.ampel and AMPEL_RANK[self.ampel] < AMPEL_RANK[self.ampel_suggest]:
            self.sudo().ampel = False
        return self._grx_ui_data()

    def grx_to_print(self):
        self.ensure_one()
        self._grx_check_state("check")
        self._grx_check_owner()
        missing = [text for fname, text, *_r in QUESTIONS if not self[fname]]
        if missing:
            raise UserError(self.env._("Erst alle Fragen beantworten – noch %s offen.", len(missing)))
        if not self.ampel:
            raise UserError(self.env._("Bitte die Ampel wählen."))
        level, reasons = self._grx_suggestion()
        if AMPEL_RANK[self.ampel] < AMPEL_RANK[level]:
            raise UserError(self.env._("Die Ampel %(a)s ist milder als die Antworten (%(s)s). Nicht möglich.",
                                       a=AMPEL_SHORT[self.ampel], s=AMPEL_SHORT[level]))
        if self.ampel != "gruen" and not reasons and not (self.note or "").strip():
            raise UserError(self.env._("Bitte einen Grund eintragen (die Ampel ist strenger als der Vorschlag)."))
        self._grx_changed({"state": "print", "checked_at": fields.Datetime.now()})
        return self._grx_ui_data()

    @api.model
    def _grx_invalidate_legacy_prints(self):
        """Ausdrücke offener Boxen ohne Änderungsstand (vor Version 1.3.0 gedruckt) ungültig machen.

        Vor 1.3.0 konnte die Box nach dem Druck geändert werden, ohne dass der Ausdruck
        zurückgesetzt wurde. Ob ein solcher Ausdruck noch passt, ist nicht nachweisbar:
        neu drucken ist Pflicht. Rückgabe: betroffene Boxen.
        """
        boxes = self.sudo().search([("state", "=", "print"), ("printed_at", "!=", False), ("content_rev", "=", 0)])
        for box in boxes:
            box._grx_changed()
        return boxes

    def _grx_print_outdated(self):
        """True, wenn kein gültiger Ausdruck zum aktuellen Stand der Box vorliegt."""
        self.ensure_one()
        return not self.printed_at or self.printed_rev != self.content_rev

    def grx_print(self):
        """Dokumente drucken: KabQ-Checkliste, Anlage Box-Inhalt, bei Rot das Sperrblatt."""
        self.ensure_one()
        if self.state in ("scan", "check", "cancel"):
            raise UserError(self.env._("Drucken geht erst nach der Vorprüfung."))
        self.sudo().write({
            "printed_at": fields.Datetime.now(),
            "printed_by": self.env.user.id,
            "print_count": self.print_count + 1,
            "printed_rev": self.content_rev,
        })
        if self.print_count > 1:
            body = self.env._("Box-Dokumente gedruckt (Druck Nr. %(n)s, Neuausdruck Nr. %(r)s).",
                              n=self.print_count, r=self.print_count - 1)
        else:
            body = self.env._("Box-Dokumente gedruckt (Druck Nr. %s).", self.print_count)
        self.sudo().message_post(body=body)
        return self.env.ref("plcs_greenrx.action_report_grx_box_documents").report_action(self)

    def grx_confirm_signed(self, value=True):
        self.ensure_one()
        self._grx_check_state("print")
        self._grx_check_owner()
        if value and self._grx_print_outdated():
            raise UserError(self.env._("Erst die Dokumente drucken."))
        self.sudo().signed_confirmed = bool(value)
        return self._grx_ui_data()

    def grx_close(self):
        """Box abschließen: Seriennummern anlegen und Bewegung in den Eingang schreiben."""
        self.ensure_one()
        self._grx_check_state("print")
        self._grx_check_owner()
        if not self.printed_at:
            raise UserError(self.env._("Erst die Dokumente drucken."))
        if self._grx_print_outdated():
            raise UserError(self.env._(
                "Box %s wurde nach dem letzten Druck geändert. Bitte neu drucken, prüfen und unterschreiben.",
                self.package_id.name))
        if not self.signed_confirmed:
            raise UserError(self.env._("Bitte bestätigen: Ausdruck geprüft und unterschrieben."))
        reason = self.package_id._plcs_grx_occupied_reason(ignore_box=self)
        if reason:
            raise UserError(self.env._("Box %(box)s ist nicht mehr leer: %(r)s", box=self.package_id.name, r=reason))
        config = self.company_id._plcs_grx_config()
        picking = self.ticket_id.picking_id
        if not picking:
            raise UserError(self.env._("Zum Ticket gibt es keinen Eingang."))
        if picking.state == "done":
            raise UserError(self.env._("Eingang %s ist schon gebucht. Bitte ein neues Ticket anlegen lassen.", picking.name))
        if picking.state == "draft":
            picking.action_confirm()
        dest = config.plcs_grx_blocked_location_id if self.ampel == "rot" else config.plcs_grx_quarantine_location_id
        scans = self.scan_ids.filtered("accepted")
        exp_dt = fields.Datetime.to_datetime(self.expiry_date) if self.expiry_date else False
        Lot = self.env["stock.lot"].sudo()
        lots = Lot.create([{
            "name": s.serial,
            "product_id": self.product_id.id,
            "company_id": self.company_id.id,
            "plcs_is_grx_serial": True,
            "plcs_batch": self.batch,
            "plcs_grx_box_id": self.id,
            "expiration_date": exp_dt,
        } for s in scans])
        for scan, lot in zip(scans, lots):
            scan.sudo().lot_id = lot
        move = self.env["stock.move"].create({
            "product_id": self.product_id.id,
            "product_uom_qty": len(lots),
            "uom_id": self.product_id.uom_id.id,
            "picking_id": picking.id,
            "location_id": picking.location_id.id,
            "location_dest_id": dest.id,
            "company_id": self.company_id.id,
            "move_line_ids": [(0, 0, {
                "product_id": self.product_id.id,
                "lot_id": lot.id,
                "quantity": 1,
                "uom_id": self.product_id.uom_id.id,
                "location_id": picking.location_id.id,
                "location_dest_id": dest.id,
                "result_package_id": self.package_id.id,
                "picking_id": picking.id,
            }) for lot in lots],
        })
        # Nicht mit anderen Boxen desselben Artikels zusammenführen: je Box eine eigene Bewegung
        # (sonst löscht Odoo die neue Bewegung beim Zusammenführen → „Datensatz existiert nicht“).
        move = move._action_confirm(merge=False)
        move.picked = True
        state = "blocked" if self.ampel == "rot" else "to_release"
        self.sudo().write({"state": state, "closed_at": fields.Datetime.now(), "move_id": move.id})
        self.sudo().message_post(body=self.env._(
            "Box abgeschlossen: %(n)s × %(p)s, Charge %(ch)s, Ampel %(a)s → %(loc)s.",
            n=len(lots), p=self.product_id.display_name, ch=self.batch,
            a=AMPEL_SHORT[self.ampel], loc=dest.display_name))
        return self._grx_ui_data()

    def grx_cancel(self):
        """Erfassung abbrechen (nur vor dem Abschluss)."""
        self.ensure_one()
        self._grx_check_state(*OPEN_STATES)
        self._grx_check_owner()
        self.sudo().scan_ids.filtered("accepted").write({"accepted": False, "result": "removed",
                                                  "message": self.env._("Erfassung abgebrochen")})
        self.sudo().state = "cancel"
        self.sudo().message_post(body=self.env._("Erfassung abgebrochen von %s.", self.env.user.name))
        return self.ticket_id._grx_ui_data()

    # ------------------------------------------------------------------
    # VP
    # ------------------------------------------------------------------
    def _grx_check_vp(self):
        if not self.env.user.has_group("plcs_greenrx.group_grx_vp"):
            raise AccessError(self.env._("Nur die VP (Gruppe „GreenRX: VP-Freigabe“) darf freigeben oder sperren."))
        for box in self:
            if box.user_id == self.env.user:
                raise AccessError(self.env._(
                    "4-Augen-Prinzip: Box %s hast du selbst erfasst. Eine andere VP muss entscheiden.",
                    box.package_id.name))
            if box.ticket_id.picking_id.state != "done":
                raise UserError(self.env._(
                    "Das Paket %s ist noch nicht abgeschlossen (Eingang nicht gebucht).", box.ticket_id.name))

    @api.model
    def grx_vp_list(self):
        boxes = self.search([
            ("state", "in", ("to_release", "blocked")),
            ("ticket_id.picking_id.state", "=", "done"),
            ("vp_user_id", "=", False),
        ])
        own = boxes.filtered(lambda b: b.user_id == self.env.user)
        others = boxes - own
        order = {"rot": 0, "gelb": 1, "gruen": 2}
        others = others.sorted(lambda b: (order.get(b.ampel, 3), b.closed_at or b.create_date))
        return {
            "boxes": [b._grx_ui_data() for b in others],
            "own_count": len(own),
            "is_vp": self.env.user.has_group("plcs_greenrx.group_grx_vp"),
        }

    def _grx_internal_move(self, picking_type, dest, reason_text):
        self.ensure_one()
        quants = self.package_id.sudo().quant_ids.filtered(lambda q: q.quantity > 0 and q.location_id.usage == "internal")
        if not quants:
            raise UserError(self.env._("In Box %s liegt keine Ware.", self.package_id.name))
        src = quants[0].location_id
        Picking = self.env["stock.picking"].with_context(plcs_grx_controlled_move=True)
        picking = Picking.create({
            "picking_type_id": picking_type.id,
            "location_id": src.id,
            "location_dest_id": dest.id,
            "origin": "%s / %s" % (self.ticket_id.name, self.package_id.name),
            "plcs_grx_ticket_id": self.ticket_id.id,
            "plcs_grx_release_box_id": self.id,
            "company_id": self.company_id.id,
            "note": reason_text or False,
        })
        by_product = {}
        for quant in quants:
            by_product.setdefault(quant.product_id, self.env["stock.quant"])
            by_product[quant.product_id] |= quant
        for product, pquants in by_product.items():
            self.env["stock.move"].create({
                "product_id": product.id,
                "product_uom_qty": sum(pquants.mapped("quantity")),
                "uom_id": product.uom_id.id,
                "picking_id": picking.id,
                "location_id": src.id,
                "location_dest_id": dest.id,
                "company_id": self.company_id.id,
                "move_line_ids": [(0, 0, {
                    "product_id": product.id,
                    "lot_id": q.lot_id.id,
                    "quantity": q.quantity,
                    "uom_id": product.uom_id.id,
                    "location_id": q.location_id.id,
                    "location_dest_id": dest.id,
                    "package_id": self.package_id.id,
                    "result_package_id": self.package_id.id,
                    "picking_id": picking.id,
                }) for q in pquants],
            })
        picking.with_context(plcs_grx_controlled_move=True).action_confirm()
        picking.move_ids.picked = True
        picking.with_context(plcs_grx_controlled_move=True, skip_backorder=True, cancel_backorder=True).button_validate()
        if picking.state != "done":
            raise UserError(self.env._("Bewegung %s konnte nicht gebucht werden.", picking.name))
        return picking

    @api.model
    def action_grx_stock(self):
        """Bestand in den GreenRX-Lagerorten, gruppiert nach Lagerort und Box."""
        config = self.env.company._plcs_grx_config()
        locations = (config.plcs_grx_quarantine_location_id | config.plcs_grx_stock_location_id
                     | config.plcs_grx_blocked_location_id)
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Bestand GreenRX"),
            "res_model": "stock.quant",
            "views": [(self.env.ref("plcs_greenrx.view_grx_stock_quant_list").id, "list")],
            "search_view_id": [self.env.ref("stock.quant_search_view").id],
            "domain": [("location_id", "in", locations.ids), ("quantity", ">", 0)],
            "context": {"group_by": ["location_id", "package_id"], "create": False, "edit": False},
        }

    def _grx_vp_reason_required(self):
        """Begründungspflicht bei der VP-Freigabe: gesperrte (rote) und gelbe Boxen."""
        self.ensure_one()
        return self.state == "blocked" or self.ampel in ("gelb", "rot")

    def grx_vp_release(self, note=False):
        """VP gibt die Box frei → GreenRX-Bestand (verkaufsfähig)."""
        self.ensure_one()
        self._grx_check_state("to_release", "blocked")
        self._grx_check_vp()
        if self._grx_vp_reason_required() and not (note or "").strip():
            if self.state == "blocked":
                raise UserError(self.env._("Gesperrte Box freigeben: Bitte eine Begründung angeben."))
            raise UserError(self.env._("Gelbe Box freigeben: Bitte eine Begründung angeben."))
        config = self.company_id._plcs_grx_config()
        picking = self.sudo()._grx_internal_move(config.plcs_grx_release_type_id, config.plcs_grx_stock_location_id, note)
        self.sudo().write({"state": "released", "vp_user_id": self.env.user.id, "vp_date": fields.Datetime.now(),
                    "vp_reason": note or False, "vp_picking_id": picking.id})
        self.sudo().message_post(body=self.env._("Freigegeben durch VP %(vp)s → %(loc)s (%(p)s).",
                                          vp=self.env.user.name, loc=config.plcs_grx_stock_location_id.display_name,
                                          p=picking.name))
        return self._grx_ui_data()

    def grx_vp_block(self, reason):
        """VP sperrt die Box → Sperrlager."""
        self.ensure_one()
        self._grx_check_state("to_release", "blocked")
        self._grx_check_vp()
        if not (reason or "").strip():
            raise UserError(self.env._("Bitte einen Grund für die Sperre angeben."))
        config = self.company_id._plcs_grx_config()
        picking = False
        if self.location_id != config.plcs_grx_blocked_location_id:
            picking = self.sudo()._grx_internal_move(config.plcs_grx_block_type_id, config.plcs_grx_blocked_location_id, reason)
        self.sudo().write({"state": "blocked", "vp_user_id": self.env.user.id, "vp_date": fields.Datetime.now(),
                    "vp_reason": reason, "vp_picking_id": picking.id if picking else False})
        self.sudo().message_post(body=self.env._("Gesperrt durch VP %(vp)s: %(r)s", vp=self.env.user.name, r=reason))
        return self._grx_ui_data()

    # ------------------------------------------------------------------
    # Daten für die Oberfläche
    # ------------------------------------------------------------------
    def _grx_summary(self):
        self.ensure_one()
        return {
            "id": self.id,
            "box": self.package_id.name,
            "ticket_number": self.line_id.ticket_number or "",
            "slip_batch": self.line_id.batch_expected or "",
            "slip_qty": self.line_id.qty_expected or 0,
            "product": self.product_id.display_name or "",
            "batch": self.batch or "",
            "expiry": self.expiry_date.strftime("%m/%Y") if self.expiry_date else "",
            "qty": self.qty,
            "user": self.user_id.name,
            "state": self.state,
            "state_label": dict(self._fields["state"].selection)[self.state],
            "ampel": self.ampel or "",
            "ampel_short": AMPEL_SHORT.get(self.ampel, ""),
        }

    def _grx_ui_data(self):
        self.ensure_one()
        level, reasons = self._grx_suggestion()
        data = self._grx_summary()
        data.update({
            "ticket_id": self.ticket_id.id,
            "ticket": self.ticket_id._grx_ui_data(with_boxes=False),
            "box_manual": self.box_manual,
            "user": self.user_id.name,
            "user_id": self.user_id.id,
            "scans": [{
                "id": s.id, "serial": s.serial, "batch": s.batch,
                "expiry": s.expiry_date.strftime("%m/%Y") if s.expiry_date else "",
                "time": fields.Datetime.context_timestamp(self, s.create_date).strftime("%H:%M:%S"),
            } for s in self.scan_ids.filtered("accepted").sorted("id", reverse=True)],
            "sys_checks": [{"label": c["label"], "ok": c["ok"]} for c in self._grx_system_checks()],
            "questions": [{"field": f, "text": t, "value": self[f] or ""} for f, t, *_r in QUESTIONS],
            "suggest": level,
            "suggest_label": AMPEL_SHORT[level],
            "reasons": reasons,
            "note": self.note or "",
            "printed_at": fields.Datetime.context_timestamp(self, self.printed_at).strftime("%H:%M")
            if self.printed_at else "",
            "print_count": self.print_count,
            "signed_confirmed": self.signed_confirmed,
            "location": self.location_id.display_name or "",
            "closed_at": fields.Datetime.context_timestamp(self, self.closed_at).strftime("%d.%m.%Y %H:%M")
            if self.closed_at else "",
            "vp_user": self.vp_user_id.name or "",
            "vp_reason": self.vp_reason or "",
            "is_own": self.user_id == self.env.user,
        })
        return data

    def _grx_report_values(self):
        """Werte für die Druckdokumente (KabQ-Checkliste, Anlage, Sperrblatt)."""
        self.ensure_one()
        ticket = self.ticket_id
        tz_now = fields.Datetime.context_timestamp(self, self.checked_at or fields.Datetime.now())
        exp = self.expiry_date.strftime("%m/%Y") if self.expiry_date else ""
        pzn = self.product_id.plcs_pzn or gs1.gtin_to_pzn(self.product_id.barcode or "") or ""
        scans = self.scan_ids.filtered("accepted").sorted("id")
        packs = [{"n": i + 1, "serial": s.serial, "pzn": s.pzn or pzn, "batch": s.batch,
                  "expiry": s.expiry_date.strftime("%m/%Y") if s.expiry_date else ""}
                 for i, s in enumerate(scans)]
        row = next((r for r in ticket._grx_soll_ist() if self.line_id and r["line_id"] == self.line_id.id), None)
        soll = row["soll"] if row else 0
        ist_total = row["ist"] if row else self.qty
        # Menge: Ja nur bei vollständigem Abgleich, Nein bei Überschuss/fremdem Artikel, sonst offen
        menge_value = "ja" if soll and ist_total == soll else ("nein" if ist_total > soll else "")
        sender_types = dict(ticket._fields["sender_type"].selection)
        temp = self.product_id.plcs_storage_temp
        sender_name = ticket.display_sender or ""
        exp_ok = all(c["ok"] for c in self._grx_system_checks() if "Verfall" in c["label"])
        all_same_pzn = len({p["pzn"] for p in packs}) <= 1
        rows_annahme = [
            {"text": "Ist die Ware für uns?", "value": "ja" if self.line_id else "nein",
             "remark": ("Vorgang %s · Ticket %s" % (ticket.name, self.line_id.ticket_number or "-")) if self.line_id
             else "Artikel nicht auf dem Retourenschein (Vorgang %s)" % ticket.name},
            {"text": "Stammt die Ware von einem genehmigten Lieferanten?",
             "extra": "  ".join(("[X] " if ticket.sender_type == k else "[ ] ") + v for k, v in (
                 ("wholesaler", "Großhändler"), ("manufacturer", "Hersteller"), ("pharmacy", "Apotheke"))),
             "value": "ja" if ticket.partner_id else "",
             "remark": ("Stammdaten: %s" % sender_types.get(ticket.sender_type, "")) if ticket.partner_id
             else "Absender nicht im Kontaktstamm – VP prüft"},
            {"text": "Sind keine offensichtlichen Beschädigungen oder Auffälligkeiten vorhanden?",
             "value": self.q_damage, "remark": "Vorprüfung Lager: %s" % dict(ANSWER).get(self.q_damage, "-")},
            {"text": "Wurde die Ware temperaturgeführt transportiert?",
             "extra": ("[X] " if temp == "ambient" else "[ ] ") + "Ambient (15-25°C)   " +
                      ("[X] " if temp == "cool" else "[ ] ") + "Kühl (2-8°C)",
             "value": self.q_temp_controlled, "remark": "Kategorie aus dem Artikelstamm"},
            {"text": "Ist der Temperaturverlauf ohne Abweichungen?", "value": self.q_temp_log,
             "if_no": "Wenn nein: Temperaturprotokoll angefordert: ____  FB Gesperrt ausgefüllt: ____"},
        ]
        rows_eingang = [
            {"text": "Wurde die Ware anhand der Herstellerangaben der einzelnen Artikel ordnungsgemäß transportiert?",
             "value": self.q_manufacturer, "if_no": "Wenn nein: FB Gesperrt ausgefüllt"},
            {"text": "Liegen die Nachweise zur ordnungsgemäßen Lagerung der Ware vor?", "value": self.q_storage_proof,
             "remark": "Vorprüfung Lager: %s" % dict(ANSWER).get(self.q_storage_proof, "-")},
            {"text": "Entspricht die Lieferung der bestellten Ware (GreenRX: Abgleich mit dem Ticket statt Bestellung):",
             "value": "", "if_no": "Wenn nein: Zusatzblatt ausgefüllt"},
            {"sub": True, "text": "– PZN", "value": "ja" if all_same_pzn else "nein",
             "remark": "Scan: %s · %s/%s gleich" % (pzn or "-", len(packs) if all_same_pzn else "?", len(packs))},
            {"sub": True, "text": "– Bezeichnung", "value": "ja", "remark": self.product_id.display_name},
            {"sub": True, "text": "– Stärke", "value": "", "remark": "von Hand prüfen"},
            {"sub": True, "text": "– Packungsgröße", "value": "", "remark": "von Hand prüfen"},
            {"sub": True, "text": "– Charge", "value": "nein" if self.slip_match == "batch_differs" else "ja",
             "remark": "Scan: %s · %s/%s gleich%s" % (
                 self.batch, len(packs), len(packs),
                 (" · Schein: %s" % self.line_id.batch_expected) if self.line_id.batch_expected else "")},
            {"sub": True, "text": "– Verfallsdatum (mind. %s Monate)" % (self.company_id.plcs_grx_min_shelf_months or 0),
             "value": "ja" if exp_ok else "nein", "remark": "Scan: %s" % exp},
            {"sub": True, "text": "– Ware verifizierbar (RX-Arzneimittel)", "value": "", "remark": "securPharm-Status von Hand"},
            {"sub": True, "text": "– Menge", "value": menge_value,
             "remark": "Ticket %s · in dieser Box %s · im Paket bisher %s%s" % (
                 soll, self.qty, ist_total, "" if menge_value else " (Abgleich beim Paketabschluss)")},
            {"text": "Verfügen alle Artikel des Auftrages über einen intakten Originalitätsverschluss?",
             "value": self.q_seal,
             "if_no": "Wenn nein: Ware ohne intakten OV: FB Gesperrt ausgefüllt / Ware mit intaktem OV: FB Zusatzblatt ausgefüllt"},
        ]
        level, reasons = self._grx_suggestion()
        block_reason = "; ".join(reasons) or (self.note or "")
        pairs = [packs[i:i + 2] for i in range(0, len(packs), 2)]
        config = self.company_id
        reprint = ""
        if self.print_count > 1:
            printed = fields.Datetime.context_timestamp(self, self.printed_at or fields.Datetime.now())
            reprint = "Neuausdruck Nr. %s · %s Uhr" % (self.print_count - 1, printed.strftime("%d.%m.%Y, %H:%M"))
        return {
            "reprint": reprint,
            "ampel_upper": AMPEL_SHORT.get(self.ampel, "-").upper(),
            "sender": "%s%s" % (sender_name, (" (%s)" % ticket.customer_number) if ticket.customer_number else ""),
            "sender_name": sender_name,
            "ref_ticket": " / ".join(x for x in (ticket.name, self.line_id.ticket_number) if x),
            "ticket_number": self.line_id.ticket_number or "",
            "date": tz_now.strftime("%d.%m.%Y"),
            "checked_by": "%s · %s" % (self.user_id.name, tz_now.strftime("%d.%m.%Y, %H:%M")),
            "rows_annahme": rows_annahme,
            "rows_eingang": rows_eingang,
            "doc_ref": "%s · Box %s" % (self.picking_id.name or ticket.name, self.package_id.name),
            "pages": 3 if self.ampel == "rot" else 2,
            "batch_exp": "%s · %s" % (self.batch or "", exp),
            "packs": packs,
            "pack_pairs": pairs,
            "pzn": pzn,
            "expiry": exp,
            "blocked_at": tz_now.strftime("%d.%m.%Y, %H:%M"),
            "block_reason": block_reason,
            "blocked_location": config.plcs_grx_blocked_location_id.display_name or "Sperrlager",
        }

    @api.model
    def grx_ampel_meta(self):
        return {k: {"label": dict(AMPEL)[k], "short": AMPEL_SHORT[k], "desc": AMPEL_DESC[k]} for k in AMPEL_RANK}
