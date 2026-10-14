import re

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command, Domain

from ..tools import gs1, slip

# Felder, die die Suche in der Datenbank durchsucht (Referenz, Reklamation, Gutschrift,
# Kundennummer, Absender, Ticketnummer, Artikel der Positionen)
SEARCH_FIELDS = (
    "name", "complaint_number", "credit_note", "customer_number",
    "partner_id.complete_name", "partner_name",
    "line_ids.ticket_number", "line_ids.pzn", "line_ids.product_name",
    "line_ids.product_id.name", "line_ids.product_id.default_code",
    "ativ_credit_note_ids.name", "ativ_credit_note_ids.partner_name", "ativ_credit_note_ids.customer_number",
    "ativ_credit_note_ids.complaint_number", "ativ_credit_note_ids.ticket_numbers",
)
STARTABLE_STATES = ("open", "in_progress")
ATIV_CATEGORIES = [
    ("expected", "Erwartet"),
    ("announced", "Angemeldet (noch nicht genehmigt)"),
    ("closed", "Bei ATIV abgeschlossen"),
    ("cancel", "Abgelehnt / storniert"),
    ("unknown", "Unbekannt"),
]


def _norm(value):
    return re.sub(r"[\s\-./]", "", str(value or "")).lower()


def _norm_pattern(q):
    """ILIKE-Muster zum normalisierten Suchbegriff: zwischen den Zeichen darf alles stehen.

    So findet die Datenbank auch "K-10234" für "k 10234" (Trennzeichen egal). Das Muster
    ist eine Obermenge; die genaue Prüfung und Rangfolge folgt in grx_search.
    """
    chars = (c.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for c in q)
    return "%" + "%".join(chars) + "%"


class PlcsGreenrxTicket(models.Model):
    """Ein Retourenpaket = ein Vorgang im GreenRX-ERP (Referenz V…).

    Ein Vorgang enthält je Produkt eine Position mit eigener Ticketnummer,
    PZN, erwarteter Charge und Packungsanzahl (siehe QR-Code des Retourenscheins).
    Der technische Modellname bleibt plcs.greenrx.ticket.
    """

    _name = "plcs.greenrx.ticket"
    _description = "GreenRX-Retourenvorgang (Paket)"
    _inherit = ["mail.thread"]
    _order = "date_ticket desc, id desc"

    name = fields.Char("Referenznummer (Vorgang)", required=True, index=True, tracking=True,
                       help="Vorgangsnummer laut Retourenschein, z. B. V202601693.")
    reference = fields.Char(related="name", string="Referenznummer")
    complaint_number = fields.Char("Reklamationsnummer", index=True, tracking=True)
    credit_note = fields.Char("Gutschriftnummer", tracking=True)
    partner_id = fields.Many2one("res.partner", "Absender", index=True, tracking=True)
    partner_name = fields.Char("Absender (Name)", help="Name laut GreenRX-ERP, falls kein Kontakt zugeordnet ist.")
    customer_number = fields.Char("Kundennummer", index=True)
    sender_type = fields.Selection(
        [("pharmacy", "Apotheke"), ("wholesaler", "Großhändler"), ("manufacturer", "Hersteller")],
        string="Absender-Art", default="pharmacy")
    date_ticket = fields.Date("Anmeldedatum", default=fields.Date.context_today)
    state = fields.Selection(
        [("open", "Offen"), ("in_progress", "In Erfassung"), ("done", "Erfasst"),
         ("closed", "Nicht erwartet"), ("cancel", "Storniert")],
        default="open", required=True, tracking=True, index=True,
        help="„Nicht erwartet“: laut ATIV abgeschlossen, noch nicht genehmigt oder vor dem Stichtag. "
             "Die Lagerleitung kann den Vorgang trotzdem öffnen.")
    source = fields.Selection(
        [("manual", "Von Hand"), ("import", "Import"), ("api", "Schnittstelle"), ("slip", "Retourenschein (QR)")],
        default="manual", required=True, readonly=True)
    external_id = fields.Char("Externe ID", index=True, readonly=True, copy=False,
                              help="ID des Vorgangs im GreenRX-ERP.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many("plcs.greenrx.ticket.line", "ticket_id", "Angemeldete Produkte", copy=True)
    ticket_numbers = fields.Char("Ticketnummern", compute="_compute_ticket_numbers", store=True, index="trigram")
    picking_id = fields.Many2one("stock.picking", "Eingang (Odoo)", readonly=True, copy=False)
    box_ids = fields.One2many("plcs.greenrx.box", "ticket_id", "Boxen", readonly=True)
    qty_expected_total = fields.Integer("Soll (Packungen)", compute="_compute_totals")
    qty_received_total = fields.Integer("Ist (Packungen)", compute="_compute_totals")
    has_deviation = fields.Boolean("Soll/Ist-Abweichung", compute="_compute_totals")
    deviation_note = fields.Text("Abweichung (Begründung)", readonly=True)
    slip_note = fields.Text("Abweichung Retourenschein", readonly=True,
                            help="Unterschiede zwischen gescanntem Retourenschein und den Daten aus dem GreenRX-ERP.")
    display_sender = fields.Char("Absender (Anzeige)", compute="_compute_display_sender")
    # ATIV-Quelle (nur lesen; die Synchronisierung schreibt hier)
    ativ_connector_id = fields.Many2one("plcs.greenrx.connector", "ATIV-Schnittstelle", readonly=True, copy=False)
    ativ_credit_note_ids = fields.One2many("plcs.ativ.credit.note", "grx_ticket_id", "ATIV-Gutschriften",
                                           readonly=True)
    ativ_document_ids = fields.One2many("plcs.ativ.document", "grx_ticket_id", "ATIV-Dokumente", readonly=True)
    ativ_document_count = fields.Integer("Dokumente", compute="_compute_ativ_counts")
    ativ_ticket_ids = fields.Many2many("plcs.ativ.ticket", compute="_compute_ativ_counts", string="ATIV-Tickets")
    ativ_status = fields.Selection(ATIV_CATEGORIES, "Status laut ATIV", readonly=True, copy=False, index=True)
    ativ_status_reason = fields.Char("Begründung ATIV-Status", readonly=True, copy=False)
    ativ_override = fields.Boolean("Erwartung von Hand festgelegt", readonly=True, copy=False, tracking=True,
                                   help="Die Lagerleitung hat den Vorgang trotz ATIV-Status geöffnet. "
                                        "Die Synchronisierung ändert den Status dann nicht mehr.")
    ativ_auto_state = fields.Boolean(readonly=True, copy=False,
                                     help="Status „Nicht erwartet“/„Storniert“ wurde von der Synchronisierung gesetzt.")
    ativ_last_sync = fields.Datetime("Zuletzt mit ATIV abgeglichen", readonly=True, copy=False)
    ativ_pickup_date = fields.Datetime("Abholtermin (ATIV)", compute="_compute_ativ_counts")

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)", "Diesen Vorgang (Referenznummer) gibt es schon.")

    @api.depends("partner_id", "partner_name")
    def _compute_display_sender(self):
        for ticket in self:
            ticket.display_sender = ticket.partner_id.display_name or ticket.partner_name or ""

    def _compute_ativ_counts(self):
        for ticket in self:
            ticket.ativ_document_count = len(ticket.ativ_document_ids)
            ticket.ativ_ticket_ids = ticket.ativ_credit_note_ids.ativ_ticket_ids
            dates = [d for d in ticket.ativ_credit_note_ids.mapped("pickup_date") if d]
            ticket.ativ_pickup_date = max(dates) if dates else False

    @api.depends("line_ids.ticket_number")
    def _compute_ticket_numbers(self):
        for ticket in self:
            ticket.ticket_numbers = ", ".join(n for n in ticket.line_ids.mapped("ticket_number") if n) or False

    @api.depends("line_ids.qty_expected", "line_ids.ativ_removed", "box_ids.qty", "box_ids.state", "box_ids.product_id", "box_ids.batch")
    def _compute_totals(self):
        for ticket in self:
            ticket.qty_expected_total = sum(ticket.line_ids.filtered(lambda l: not l.ativ_removed).mapped("qty_expected"))
            boxes = ticket.box_ids.filtered(lambda b: b.state != "cancel")
            ticket.qty_received_total = sum(boxes.mapped("qty"))
            ticket.has_deviation = any(row["status"] != "ok" for row in ticket._grx_soll_ist())

    @api.model_create_multi
    def create(self, vals_list):
        # Über Datensätze importieren angelegt → Herkunft "Import"
        if self.env.context.get("import_file"):
            for vals in vals_list:
                vals.setdefault("source", "import")
        return super().create(vals_list)

    @api.model
    def get_import_templates(self):
        return [{
            "label": self.env._("Vorlage GreenRX-Retourenvorgänge"),
            "template": "/plcs_greenrx/static/xls/greenrx_tickets_vorlage.csv",
        }]

    # ------------------------------------------------------------------
    # Positionen und Soll / Ist
    # ------------------------------------------------------------------
    def _grx_match_line(self, product, batch=False):
        """Position des Retourenscheins für Artikel + Charge finden.

        Rückgabe: (Position oder leer, Grund) mit Grund in
        "ok" | "batch_differs" | "not_on_slip".
        """
        self.ensure_one()
        lines = self.line_ids.filtered(lambda l: l.product_id == product and not l.ativ_removed)
        if not lines:
            return self.env["plcs.greenrx.ticket.line"], "not_on_slip"
        if batch:
            exact = lines.filtered(lambda l: (l.batch_expected or "").strip().lower() == batch.strip().lower())
            if exact:
                return exact[0], "ok"
            no_batch = lines.filtered(lambda l: not l.batch_expected)
            if no_batch:
                return no_batch[0], "ok"
            return lines[0], "batch_differs"
        return lines[0], "ok"

    def _grx_soll_ist(self):
        """Soll/Ist je Position (Ticket) des Vorgangs, dazu Boxen ohne passende Position."""
        self.ensure_one()
        rows = []
        by_line = {}
        for line in self.line_ids:
            row = {
                "line_id": line.id,
                "product_id": line.product_id.id,
                "product": line.product_id.display_name or line.product_name or line.pzn or "?",
                "ticket": line.ticket_number or "",
                "pzn": line.pzn or "",
                "batch": line.batch_expected or "",
                "soll": 0 if line.ativ_removed else line.qty_expected, "ist": 0,
            }
            if line.ativ_removed:
                row["product"] += " " + self.env._("(bei ATIV entfernt)")
            rows.append(row)
            by_line[line.id] = row
        extra = {}
        for box in self.box_ids.filtered(lambda b: b.state != "cancel" and b.product_id):
            line = box.line_id
            if line and line.id in by_line:
                by_line[line.id]["ist"] += box.qty
            else:
                key = (box.product_id.id, box.batch)
                row = extra.setdefault(key, {
                    "line_id": False, "product_id": box.product_id.id, "product": box.product_id.display_name,
                    "ticket": "", "pzn": box.product_id.plcs_pzn or "", "batch": box.batch or "",
                    "soll": 0, "ist": 0,
                })
                row["ist"] += box.qty
        rows.extend(extra.values())
        for row in rows:
            if row["soll"] == row["ist"]:
                row["status"], row["status_text"] = "ok", "OK"
            elif row["soll"] == 0:
                row["status"], row["status_text"] = "extra", self.env._("nicht auf dem Schein")
            elif row["ist"] > row["soll"]:
                row["status"], row["status_text"] = "over", self.env._("%s zu viel", row["ist"] - row["soll"])
            else:
                row["status"], row["status_text"] = "open", self.env._("%s offen", row["soll"] - row["ist"])
        return rows

    # ------------------------------------------------------------------
    # Daten für die Scan-Oberfläche
    # ------------------------------------------------------------------
    def _grx_ui_data(self, with_boxes=True):
        self.ensure_one()
        data = {
            "id": self.id,
            "name": self.name,
            "reference": self.name,
            "complaint_number": self.complaint_number or "",
            "credit_note": self.credit_note or "",
            "sender": self.display_sender or "",
            "customer_number": self.customer_number or "",
            "state": self.state,
            "source": self.source,
            "picking": self.picking_id.name or "",
            "items": [{
                "product": l.product_id.display_name or l.product_name or l.pzn or "?",
                "ticket": l.ticket_number or "",
                "pzn": l.pzn or "",
                "batch": l.batch_expected or "",
                "qty": l.qty_expected,
                "known": bool(l.product_id),
            } for l in self.line_ids if not l.ativ_removed],
            "total": self.qty_expected_total,
            "received": self.qty_received_total,
            "soll_ist": self._grx_soll_ist(),
            "slip_note": self.slip_note or "",
            "startable": self.state in STARTABLE_STATES,
            "state_label": dict(self._fields["state"].selection).get(self.state, ""),
            "ativ_status": dict(self._fields["ativ_status"].selection).get(self.ativ_status, "") if self.ativ_status else "",
            "ativ_reason": self.ativ_status_reason or "",
            "ativ_stages": ", ".join(sorted(set(n for n in self.ativ_credit_note_ids.ativ_ticket_ids.mapped("stage_name") if n))),
            "date": self.date_ticket.strftime("%d.%m.%Y") if self.date_ticket else "",
            "city": next((c.partner_city for c in self.ativ_credit_note_ids if c.partner_city), ""),
            "documents": self.ativ_document_count,
            "pickup": fields.Datetime.context_timestamp(self, self.ativ_pickup_date).strftime("%d.%m.%Y")
            if self.ativ_pickup_date else "",
        }
        if with_boxes:
            data["boxes"] = [b._grx_summary() for b in self.box_ids.filtered(
                lambda b: b.state != "cancel").sorted("id")]
        return data

    @api.model
    def grx_search(self, query, limit=20):
        """Offene Vorgänge suchen: Referenz, Ticket-, Reklamations-, Gutschrift-, Kundennummer, Apotheke."""
        q = _norm(query)
        if not q:
            return []
        pattern = _norm_pattern(q)
        match_domain = Domain.OR(Domain(fname, "=ilike", pattern) for fname in SEARCH_FIELDS)
        tickets = self.search(Domain("state", "in", STARTABLE_STATES) & match_domain)
        # Nicht erwartete, erfasste oder stornierte Vorgänge zur Information (nicht startbar)
        tickets |= self.search(Domain("state", "not in", STARTABLE_STATES) & match_domain,
                               order="date_ticket desc, id desc", limit=50)
        labels = (
            ("name", self.env._("Referenz")),
            ("complaint_number", self.env._("Reklamation")),
            ("credit_note", self.env._("Gutschrift")),
            ("customer_number", self.env._("Kundennr.")),
            ("display_sender", self.env._("Apotheke")),
        )
        found = []
        for ticket in tickets:
            match = False
            ticket_nos = [_norm(n) for n in ticket.line_ids.mapped("ticket_number") if n]
            # exakter Treffer zuerst (gescanntes Etikett)
            for fname, label in labels:
                if _norm(ticket[fname]) == q:
                    match = (0, label)
                    break
            if not match and q in ticket_nos:
                match = (0, self.env._("Ticket"))
            if not match:
                for fname, label in labels:
                    if q in _norm(ticket[fname]):
                        match = (1, label)
                        break
            if not match and any(q in n for n in ticket_nos):
                match = (1, self.env._("Ticket"))
            if not match:
                if any(q in _norm(l.product_id.display_name or l.product_name) or q == _norm(l.pzn)
                       for l in ticket.line_ids):
                    match = (2, self.env._("Medikament"))
            if match:
                found.append((match, ticket))
        found.sort(key=lambda x: (x[1].state not in STARTABLE_STATES, x[0][0]))
        result = []
        for (_rank, label), ticket in found[:limit]:
            data = ticket._grx_ui_data(with_boxes=False)
            data["match"] = self.env._("Treffer: %s", label)
            result.append(data)
        return result

    @api.model
    def grx_from_slip(self, raw):
        """QR-Code des Retourenscheins gescannt → Vorgang finden (oder anlegen) und zurückgeben."""
        try:
            data = slip.parse(raw)
        except slip.SlipError as e:
            raise UserError(str(e)) from e
        company = self.env.company
        ticket = self.search([("name", "=", data["reference"]), ("company_id", "=", company.id)], limit=1)
        lines = [{
            "ticket_number": l["ticket"], "pzn": l["pzn"], "batch_expected": l["batch"], "qty_expected": l["qty"],
        } for l in data["lines"]]
        created = False
        if ticket:
            if ticket.state == "done":
                raise UserError(self.env._(
                    "Vorgang %s ist schon erfasst (Eingang %s). Paket nicht noch einmal annehmen, Lagerleitung fragen.",
                    ticket.name, ticket.picking_id.name or "-"))
            if ticket.state == "cancel":
                raise UserError(self.env._("Vorgang %s ist storniert. Lagerleitung fragen.", ticket.name))
            if ticket.state == "closed":
                raise UserError(self.env._(
                    "Vorgang %(n)s wird laut ATIV nicht erwartet (%(r)s). Paket zur Seite legen und Lagerleitung "
                    "fragen. Sie kann den Vorgang mit „Trotzdem erwarten“ öffnen.",
                    n=ticket.name, r=ticket.ativ_status_reason or "-"))
            note = ticket._grx_compare_slip(data)
            vals = {}
            if data["complaint"] and not ticket.complaint_number:
                vals["complaint_number"] = data["complaint"]
            if not ticket.line_ids and ticket.state == "open":
                vals["line_ids"] = [Command.create(l) for l in lines]
                note = False
            if note != (ticket.slip_note or False):
                vals["slip_note"] = note
                if note:
                    ticket.sudo().message_post(body=self.env._("Retourenschein weicht ab: %s", note))
            if vals:
                ticket.sudo().write(vals)
        else:
            if not company.plcs_grx_create_from_slip:
                raise UserError(self.env._(
                    "Vorgang %s ist in Odoo nicht bekannt. Bitte warten, bis er aus dem GreenRX-ERP übertragen ist, "
                    "oder die Lagerleitung fragen.", data["reference"]))
            ticket = self.sudo().create({
                "name": data["reference"],
                "complaint_number": data["complaint"],
                "source": "slip",
                "company_id": company.id,
                "line_ids": [Command.create(l) for l in lines],
            }).sudo(False)
            ticket.sudo().message_post(body=self.env._("Vorgang aus dem QR-Code des Retourenscheins angelegt."))
            created = True
        result = ticket._grx_ui_data(with_boxes=False)
        result["created_from_slip"] = created
        result["match"] = self.env._("Retourenschein gescannt")
        return result

    def _grx_compare_slip(self, data):
        """Unterschiede zwischen Retourenschein und gespeicherten Positionen (Text oder False)."""
        self.ensure_one()
        if not self.line_ids:
            return False
        diffs = []
        if data.get("complaint") and self.complaint_number and data["complaint"] != self.complaint_number:
            diffs.append(self.env._("Reklamation Schein %(a)s, Odoo %(b)s", a=data["complaint"], b=self.complaint_number))
        stored = {(l.ticket_number or "", (l.pzn or "").zfill(8)): l for l in self.line_ids}
        for item in data["lines"]:
            line = stored.pop((item["ticket"], item["pzn"]), None)
            if not line:
                diffs.append(self.env._("Ticket %(t)s / PZN %(p)s nur auf dem Schein", t=item["ticket"], p=item["pzn"]))
                continue
            if line.qty_expected != item["qty"]:
                diffs.append(self.env._("Ticket %(t)s: Anzahl Schein %(a)s, Odoo %(b)s",
                                        t=item["ticket"], a=item["qty"], b=line.qty_expected))
            if (line.batch_expected or "") and line.batch_expected != item["batch"]:
                diffs.append(self.env._("Ticket %(t)s: Charge Schein %(a)s, Odoo %(b)s",
                                        t=item["ticket"], a=item["batch"], b=line.batch_expected))
        for (ticket_no, pzn), _line in stored.items():
            diffs.append(self.env._("Ticket %(t)s / PZN %(p)s fehlt auf dem Schein", t=ticket_no or "-", p=pzn))
        return "; ".join(diffs) or False

    @api.model
    def grx_home(self):
        """Startseite der Scan-Oberfläche: was ist offen, was ist als Nächstes dran."""
        Box = self.env["plcs.greenrx.box"]
        user = self.env.user
        in_progress = self.search([("state", "=", "in_progress")], order="write_date desc", limit=30)
        rows = []
        for ticket in in_progress:
            open_box = ticket.box_ids.filtered(lambda b: b.state in ("scan", "check", "print"))
            mine = open_box.filtered(lambda b: b.user_id == user)
            rows.append({
                "id": ticket.id,
                "name": ticket.name,
                "picking": ticket.picking_id.name or "",
                "sender": ticket.display_sender or "",
                "boxes": len(ticket.box_ids.filtered(lambda b: b.state != "cancel")),
                "open_box": (mine or open_box)[:1].package_id.name or "",
                "open_box_state": dict(Box._fields["state"].selection).get((mine or open_box)[:1].state, ""),
                "mine": bool(mine),
                "other_user": (open_box - mine)[:1].user_id.name or "",
            })
        rows.sort(key=lambda r: (not r["mine"], r["name"]))
        is_vp = user.has_group("plcs_greenrx.group_grx_vp")
        vp_count = 0
        if is_vp:
            vp_count = Box.search_count([
                ("state", "in", ("to_release", "blocked")), ("vp_user_id", "=", False),
                ("ticket_id.state", "=", "done"), ("picking_id.state", "=", "done"), ("user_id", "!=", user.id)])
        return {
            "user": user.name,
            "initials": "".join(p[:1] for p in (user.name or "?").split()[:2]).upper(),
            "today": fields.Date.context_today(self).strftime("%d.%m."),
            "in_progress": rows,
            "open_count": self.search_count([("state", "=", "open")]),
            "is_vp": is_vp,
            "vp_count": vp_count,
        }

    def grx_resume(self):
        """In Erfassung befindlichen Vorgang fortsetzen (ggf. mit offener Box des Benutzers)."""
        self.ensure_one()
        data = self.grx_start()
        box = self.box_ids.filtered(
            lambda b: b.state in ("scan", "check", "print") and b.user_id == self.env.user)[:1]
        return {"ticket": data, "box": box._grx_ui_data() if box else False}

    def grx_start(self):
        """Paket ausgewählt: Eingang anlegen (falls nötig) und Daten zurückgeben."""
        self.ensure_one()
        if self.state not in ("open", "in_progress"):
            raise UserError(self.env._("Vorgang %s ist nicht mehr offen.", self.name))
        if not self.picking_id:
            self.sudo().picking_id = self._grx_create_receipt(self.name)
        if self.state == "open":
            self.sudo().state = "in_progress"
        return self._grx_ui_data()

    def _grx_create_receipt(self, origin):
        self.ensure_one()
        config = self.company_id._plcs_grx_config()
        picking_type = config.plcs_grx_receipt_type_id
        src = picking_type.default_location_src_id or self.env.ref("stock.stock_location_customers")
        return self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "partner_id": self.partner_id.id,
            "origin": origin,
            "location_id": src.id,
            "location_dest_id": config.plcs_grx_quarantine_location_id.id,
            "plcs_grx_ticket_id": self.id,
            "company_id": self.company_id.id,
        })

    def _grx_receipts(self):
        """Alle Eingänge des Vorgangs: der erste (picking_id) und ggf. Nachträge."""
        self.ensure_one()
        return self.picking_id | self.env["stock.picking"].search([
            ("plcs_grx_ticket_id", "=", self.id),
            ("picking_type_id", "=", self.company_id.plcs_grx_receipt_type_id.id),
        ])

    def _grx_open_receipt(self):
        """Eingang für die nächste abgeschlossene Box.

        Ist der Eingang schon gebucht (vor 1.3.2 ließ er sich außerhalb von „Paket fertig“
        buchen), kommt die Box in einen Nachtrags-Eingang, der mit „Paket fertig“ gebucht wird.
        """
        self.ensure_one()
        receipt = self._grx_receipts().filtered(lambda p: p.state not in ("done", "cancel"))[:1]
        if receipt:
            return receipt
        receipt = self._grx_create_receipt(self.env._("%s (Nachtrag)", self.name))
        self.sudo().message_post(body=self.env._(
            "Eingang %(old)s war schon gebucht. Weitere Boxen kommen in den Nachtrags-Eingang %(new)s.",
            old=self.picking_id.name, new=receipt.name))
        return receipt

    def grx_data(self):
        self.ensure_one()
        return self._grx_ui_data()

    def grx_finish(self, deviation_note=False):
        """Paket fertig: alle Boxen abgeschlossen → Eingang buchen (Ware in Quarantäne/Sperrlager)."""
        self.ensure_one()
        if self.state != "in_progress":
            raise UserError(self.env._("Vorgang %s ist nicht in Erfassung.", self.name))
        boxes = self.box_ids.filtered(lambda b: b.state != "cancel")
        open_boxes = boxes.filtered(lambda b: b.state in ("scan", "check", "print"))
        if open_boxes:
            states = dict(open_boxes._fields["state"].selection)
            raise UserError(self.env._(
                "Noch nicht abgeschlossen: %s. Erst die Box abschließen oder abbrechen "
                "(Box-Nummer scannen oder von Hand eingeben).",
                ", ".join("%s (Schritt „%s“, erfasst von %s)" % (b.package_id.name, states[b.state], b.user_id.name)
                          for b in open_boxes)))
        if not boxes:
            raise UserError(self.env._("Für dieses Paket ist noch keine Box erfasst."))
        deviations = [r for r in self._grx_soll_ist() if r["status"] != "ok"]
        if deviations and not (deviation_note or "").strip():
            raise UserError(self.env._(
                "Soll und Ist stimmen nicht überein (%s). Bitte eine Begründung angeben.",
                "; ".join(self._grx_row_label(r) for r in deviations)))
        receipts = self._grx_receipts()
        for picking in receipts.filtered(lambda p: p.state not in ("done", "cancel")):
            if not picking.move_ids:
                picking.action_cancel()
                continue
            if picking.state == "draft":
                picking.action_confirm()
            for move in picking.move_ids:
                move.picked = True
            picking.with_context(plcs_grx_finish=True, skip_backorder=True, cancel_backorder=True).button_validate()
            if picking.state != "done":
                raise UserError(self.env._("Eingang %s konnte nicht gebucht werden.", picking.name))
        picking_names = ", ".join(receipts.filtered(lambda p: p.state == "done").mapped("name"))
        vals = {"state": "done"}
        if deviations:
            vals["deviation_note"] = deviation_note
            self.sudo().message_post(body=self.env._(
                "Paket mit Soll/Ist-Abweichung abgeschlossen: %(dev)s. Begründung: %(note)s",
                dev="; ".join(self._grx_row_label(r) for r in deviations), note=deviation_note))
        self.sudo().write(vals)
        for box in boxes.filtered(lambda b: b.state in ("to_release", "blocked")):
            box.sudo().message_post(body=self.env._("Eingang %s gebucht. Box wartet auf die VP.", picking_names))
        return self._grx_ui_data()

    @staticmethod
    def _grx_row_label(row):
        prefix = ("Ticket %s · " % row["ticket"]) if row.get("ticket") else ""
        return "%s%s: %s" % (prefix, row["product"], row["status_text"])

    def action_cancel(self):
        for ticket in self:
            if ticket.box_ids.filtered(lambda b: b.state not in ("cancel",)):
                raise UserError(self.env._("Vorgang %s hat schon erfasste Boxen.", ticket.name))
            if ticket.picking_id and ticket.picking_id.state != "done":
                ticket.picking_id.action_cancel()
            ticket.state = "cancel"

    # ------------------------------------------------------------------
    # Schnittstelle
    # ------------------------------------------------------------------
    @api.model
    def api_upsert(self, tickets, source="api"):
        """Vorgänge anlegen oder aktualisieren (Schnittstelle GreenRX-ERP).

        tickets: Liste von dicts mit
          name (Referenznummer/Vorgang, Pflicht), external_id, complaint_number, credit_note,
          partner_name, customer_number, sender_type, date_ticket,
          lines = [{ticket_number, gtin|pzn|product_name, batch, qty, credit_note}].
        Begonnene Vorgänge werden nur in den Kopfdaten aktualisiert.
        Rückgabe: {"created": n, "updated": n, "skipped": n, "errors": [...]}
        """
        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
        for item in tickets or []:
            try:
                with self.env.cr.savepoint():
                    self._api_upsert_one(item, source, stats)
            except (UserError, ValueError) as e:
                stats["errors"].append("%s: %s" % (item.get("name") or item.get("external_id"), e))
        return stats

    def _api_upsert_one(self, item, source, stats):
        name = (item.get("name") or "").strip()
        ext = str(item.get("external_id") or "").strip()
        if not name:
            raise UserError(self.env._("Referenznummer (Vorgang) fehlt."))
        ticket = self.browse()
        if ext:
            ticket = self.search([("external_id", "=", ext), ("source", "=", source)], limit=1)
        if not ticket:
            ticket = self.search([("name", "=", name)], limit=1)
        partner = self.env["res.partner"]
        if item.get("customer_number"):
            partner = partner.search([("ref", "=", item["customer_number"])], limit=1)
        vals = {"name": name}
        for key in ("complaint_number", "credit_note", "partner_name", "customer_number"):
            if key in item:
                vals[key] = item.get(key) or False
        if partner:
            vals["partner_id"] = partner.id
        if item.get("sender_type") in ("pharmacy", "wholesaler", "manufacturer"):
            vals["sender_type"] = item["sender_type"]
        if item.get("date_ticket"):
            vals["date_ticket"] = item["date_ticket"]
        lines = []
        for line in item.get("lines") or []:
            qty = int(line.get("qty") or 0)
            if qty <= 0:
                continue
            lines.append({
                "ticket_number": line.get("ticket_number") or False,
                "gtin": line.get("gtin") or False,
                "pzn": line.get("pzn") or False,
                "product_name": line.get("product_name") or False,
                "batch_expected": line.get("batch") or False,
                "credit_note": line.get("credit_note") or False,
                "qty_expected": qty,
            })
        if ticket:
            if ticket.state in ("done", "cancel"):
                stats["skipped"] += 1
                return ticket
            ticket.write(vals)
            if ticket.state == "open" and lines:
                ticket._grx_merge_lines(lines)
            stats["updated"] += 1
        else:
            vals.update({"source": source, "external_id": ext or False,
                         "line_ids": [Command.create(l) for l in lines]})
            ticket = self.create(vals)
            stats["created"] += 1
        return ticket

    # ------------------------------------------------------------------
    # Positionen ohne Löschen abgleichen (Schnittstelle, Review-Punkt 1)
    # ------------------------------------------------------------------
    def _grx_merge_lines(self, lines):
        """Positionen anhand Ticketnummer + PZN aktualisieren oder ergänzen, nie löschen.

        Positionen, die in der Lieferung fehlen, bleiben stehen (Hinweis im Verlauf).
        """
        self.ensure_one()
        Line = self.env["plcs.greenrx.ticket.line"]
        existing = {(l.ticket_number or "", _norm(l.pzn)): l for l in self.line_ids}
        seen = set()
        for vals in lines:
            key = (vals.get("ticket_number") or "", _norm(vals.get("pzn")))
            line = existing.get(key)
            if line:
                diff = {k: v for k, v in vals.items() if (line[k] or False) != (v or False)}
                if diff:
                    line.write(diff)
            else:
                Line.create(dict(vals, ticket_id=self.id))
            seen.add(key)
        missing = [k for k in existing if k not in seen]
        if missing:
            self.sudo().message_post(body=self.env._(
                "Schnittstelle: Positionen fehlen in der neuen Lieferung und bleiben stehen: %s",
                ", ".join("%s/%s" % k for k in missing)))

    # ------------------------------------------------------------------
    # ATIV-Synchronisierung
    # ------------------------------------------------------------------
    def _ativ_aggregate_status(self, credit_notes):
        cats = [c for c in credit_notes.mapped("status_category") if c]
        known = [c for c in cats if c != "unknown"]
        if "expected" in cats:
            category = "expected"
        elif not known:
            category = "unknown"
        elif all(c == "cancel" for c in known):
            category = "cancel"
        elif "closed" in known:
            category = "closed"
        else:
            category = "announced"
        reason = "; ".join("%s: %s" % (c.name, c.status_reason or "-") for c in credit_notes)
        return category, reason

    def _ativ_refresh_from_mirror(self):
        """Soll-Daten und Status aus den gespiegelten ATIV-Gutschriften übernehmen.

        Lokale PLCS-Daten (Boxen, Scans, Ist, Ampel, Begründungen, Eingänge) werden nicht berührt.
        Rückgabe: True, wenn sich am Vorgang etwas geändert hat.
        """
        self.ensure_one()
        ticket = self.sudo()
        cns = ticket.env["plcs.ativ.credit.note"].search([("grx_ticket_id", "=", ticket.id)], order="date_order, id")
        active = cns.filtered(lambda c: not c.source_missing) or cns
        if not active:
            return False
        first = active[:1]
        category, reason = ticket._ativ_aggregate_status(active)
        vals = {
            "complaint_number": next((c.complaint_number for c in active if c.complaint_number),
                                     ticket.complaint_number) or False,
            "partner_name": first.partner_name or ticket.partner_name or False,
            "customer_number": first.customer_number or ticket.customer_number or False,
            "credit_note": ", ".join(active.mapped("name")) or ticket.credit_note or False,
            "ativ_status": category, "ativ_status_reason": reason,
            "ativ_connector_id": first.connector_id.id,
        }
        if first.date_order:
            vals["date_ticket"] = first.date_order.date()
        if not ticket.partner_id and vals["customer_number"]:
            partner = ticket.env["res.partner"].search([("ref", "=", vals["customer_number"])], limit=1)
            if partner:
                vals["partner_id"] = partner.id
        def current(fname):
            value = ticket[fname]
            if isinstance(ticket._fields[fname], fields.Many2one):
                return value.id or False
            return value or False
        diff = {k: v for k, v in vals.items() if current(k) != (v or False)}
        old_status = ticket.ativ_status
        changed = bool(set(diff) - {"ativ_status_reason"})
        diff["ativ_last_sync"] = fields.Datetime.now()
        ticket.write(diff)
        changed |= ticket._ativ_sync_lines(active)
        changed |= ticket._ativ_apply_status(category, old_status)
        return changed

    def _ativ_sync_lines(self, credit_notes):
        self.ensure_one()
        Line = self.env["plcs.greenrx.ticket.line"].sudo()
        src_lines = credit_notes.line_ids.filtered(lambda l: not l.removed)
        by_key = {l.ativ_line_key: l for l in self.line_ids if l.ativ_line_key}
        unlinked = self.line_ids.filtered(lambda l: not l.ativ_line_key)
        notes, changed = [], False
        seen = set()
        for sl in src_lines:
            code = (sl.product_code or "").strip()
            barcode = (sl.product_barcode or "").strip()
            vals = {
                "ticket_number": sl.ticket_number or False,
                "pzn": code.zfill(8) if code.isdigit() else (code or False),
                "gtin": barcode.zfill(14) if barcode.isdigit() and len(barcode) in (13, 14) else False,
                "product_name": sl.product_name or False,
                "batch_expected": sl.batch or False,
                "credit_note": sl.credit_note_id.name or False,
                "qty_expected": int(round(sl.qty or 0)),
            }
            seen.add(sl.source_key)
            line = by_key.get(sl.source_key)
            if not line:
                line = unlinked.filtered(lambda l: (l.ticket_number or "") == (vals["ticket_number"] or "")
                                         and _norm(l.pzn) == _norm(vals["pzn"]))[:1]
                if line:
                    unlinked -= line
                    line.ativ_line_key = sl.source_key
            label = "Ticket %s / %s" % (vals["ticket_number"] or "-", vals["product_name"] or vals["pzn"] or "?")
            if self.state == "done":
                if not line:
                    notes.append(self.env._("%s neu bei ATIV", label))
                else:
                    diff = [k for k in ("qty_expected", "batch_expected", "pzn") if (line[k] or False) != (vals[k] or False)]
                    if diff:
                        notes.append(self.env._("%(l)s geändert (%(f)s)", l=label, f=", ".join(diff)))
                if line and sl.grx_line_id != line:
                    sl.grx_line_id = line
                continue
            if line:
                diff = {k: v for k, v in vals.items() if (line[k] or False) != (v or False)}
                if line.box_ids.filtered(lambda b: b.state != "cancel"):
                    for k in ("pzn", "gtin"):
                        if k in diff:
                            notes.append(self.env._("%(l)s: Artikel bei ATIV geändert, bleibt wegen erfasster Box",
                                                    l=label))
                            diff.pop(k)
                if diff:
                    if self.state == "in_progress":
                        notes.append(self.env._("%(l)s geändert (%(f)s)", l=label, f=", ".join(sorted(diff))))
                    line.write(diff)
                    changed = True
                if line.ativ_removed:
                    line.ativ_removed = False
                    changed = True
            else:
                line = Line.create(dict(vals, ticket_id=self.id, ativ_line_key=sl.source_key))
                changed = True
                if self.state == "in_progress":
                    notes.append(self.env._("%s neu bei ATIV", label))
            if sl.grx_line_id != line:
                sl.grx_line_id = line
        if self.state != "done":
            for key, line in by_key.items():
                if key not in seen and not line.ativ_removed:
                    line.ativ_removed = True
                    changed = True
                    notes.append(self.env._("Ticket %s bei ATIV entfernt (Soll jetzt 0)", line.ticket_number or "-"))
        if notes and self.state in ("in_progress", "done"):
            self.message_post(body=self.env._("Änderung bei ATIV nach Erfassungsbeginn: %s", "; ".join(notes)))
        return changed

    def _ativ_apply_status(self, category, old_category):
        """Status aus ATIV übernehmen, soweit PLCS noch nicht begonnen hat."""
        self.ensure_one()
        if self.ativ_override:
            return False
        has_work = bool(self.box_ids.filtered(lambda b: b.state != "cancel")) or bool(self.picking_id)
        new_state = False
        if category in ("expected", "unknown"):
            if self.state == "closed" or (self.state == "cancel" and self.ativ_auto_state):
                new_state = "open"
        else:
            target = "cancel" if category == "cancel" else "closed"
            if (self.state == "open" and not has_work) or (self.state in ("closed", "cancel") and self.ativ_auto_state):
                new_state = target if self.state != target else False
            elif self.state in ("in_progress", "done") and category != old_category and category == "cancel":
                self.message_post(body=self.env._(
                    "Hinweis: ATIV meldet den Vorgang als abgelehnt/storniert (%s). Die Erfassung bei PLCS bleibt "
                    "unverändert. Lagerleitung informieren.", self.ativ_status_reason or "-"))
        if not new_state:
            return False
        self.write({"state": new_state, "ativ_auto_state": new_state != "open"})
        return True

    def action_ativ_override(self):
        """Lagerleitung: Vorgang trotz ATIV-Status als erwartet öffnen."""
        if not self.env.user.has_group("stock.group_stock_manager"):
            raise UserError(self.env._("Nur die Lagerleitung darf die Erwartung von Hand festlegen."))
        for ticket in self.sudo():
            if ticket.state not in ("closed", "cancel"):
                continue
            ticket.write({"state": "open", "ativ_override": True, "ativ_auto_state": False})
            ticket.message_post(body=self.env._("Von Hand als erwartet geöffnet (ATIV: %s).",
                                                ticket.ativ_status_reason or "-"))
        return True

    def action_ativ_release_override(self):
        """Festlegung aufheben: Status wieder aus ATIV übernehmen."""
        if not self.env.user.has_group("stock.group_stock_manager"):
            raise UserError(self.env._("Nur die Lagerleitung darf das."))
        for ticket in self.sudo().filtered("ativ_override"):
            ticket.ativ_override = False
            ticket._ativ_apply_status(ticket.ativ_status, ticket.ativ_status)
        return True

    def action_ativ_refresh(self):
        """Diesen Vorgang sofort mit ATIV abgleichen (alle seine Gutschriften)."""
        self.ensure_one()
        cns = self.ativ_credit_note_ids
        connector = cns.connector_id[:1] or self.ativ_connector_id
        if not connector:
            raise UserError(self.env._("Dieser Vorgang stammt nicht aus der ATIV-Schnittstelle."))
        connector = connector.sudo()
        client = connector._ativ_client()
        from ..tools.ativ_client import AtivError  # noqa: PLC0415
        try:
            payloads = connector._ativ_fetch_payloads(client, cns.mapped("source_id"))
        except AtivError as e:
            raise UserError(self.env._("Abruf bei ATIV fehlgeschlagen: %s", e)) from e
        for payload in payloads.values():
            connector._ativ_apply_payload(payload)
        for cn in cns.filtered(lambda c: c.source_id not in payloads):
            cn.source_missing = True
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("Mit ATIV abgeglichen"),
                       "message": self.env._("%s Gutschriften gelesen.", len(payloads)), "type": "success",
                       "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
        }

    def action_ativ_documents(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("ATIV-Dokumente %s", self.name),
            "res_model": "plcs.ativ.document", "view_mode": "kanban,list,form",
            "domain": [("grx_ticket_id", "=", self.id)],
        }


class PlcsGreenrxTicketLine(models.Model):
    _name = "plcs.greenrx.ticket.line"
    _description = "GreenRX-Retourenvorgang: angemeldetes Produkt (Ticket)"
    _order = "ticket_id, id"
    _rec_name = "ticket_number"

    ticket_id = fields.Many2one("plcs.greenrx.ticket", "Vorgang", required=True, ondelete="cascade", index=True)
    ticket_number = fields.Char("Ticketnummer", index=True)
    product_id = fields.Many2one("product.product", "Artikel")
    product_name = fields.Char("Bezeichnung laut Schein")
    pzn = fields.Char("PZN")
    gtin = fields.Char("GTIN")
    batch_expected = fields.Char("Charge laut Schein")
    credit_note = fields.Char("Gutschriftnummer")
    qty_expected = fields.Integer("Packungen (Soll)", default=1)
    ativ_line_key = fields.Char("Quellidentität ATIV-Position", index=True, readonly=True, copy=False)
    ativ_removed = fields.Boolean("Bei ATIV entfernt", readonly=True, copy=False,
                                  help="Die Position fehlt inzwischen in der ATIV-Gutschrift. Sie bleibt sichtbar, "
                                       "zählt aber mit Soll 0.")
    box_ids = fields.One2many("plcs.greenrx.box", "line_id", "Boxen", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._plcs_resolve_product(vals)
        return super().create(vals_list)

    def write(self, vals):
        if not vals.get("product_id") and ("pzn" in vals or "gtin" in vals):
            self._plcs_resolve_product(vals)
        return super().write(vals)

    def _plcs_resolve_product(self, vals):
        if vals.get("product_id"):
            return
        gtin = (vals.get("gtin") or "").strip() or False
        pzn = (vals.get("pzn") or "").strip() or False
        if pzn and pzn.isdigit():
            pzn = pzn.zfill(8)
            vals["pzn"] = pzn
        if not gtin and pzn:
            gtin = gs1.pzn_to_gtin(pzn)
        if gtin or pzn:
            product = self.env["product.product"]._plcs_find_by_code(gtin=gtin, pzn=pzn)
            if len(product) == 1:
                vals["product_id"] = product.id
