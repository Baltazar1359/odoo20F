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
)


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
        [("open", "Offen"), ("in_progress", "In Erfassung"), ("done", "Erfasst"), ("cancel", "Storniert")],
        default="open", required=True, tracking=True, index=True)
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

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)", "Diesen Vorgang (Referenznummer) gibt es schon.")

    @api.depends("partner_id", "partner_name")
    def _compute_display_sender(self):
        for ticket in self:
            ticket.display_sender = ticket.partner_id.display_name or ticket.partner_name or ""

    @api.depends("line_ids.ticket_number")
    def _compute_ticket_numbers(self):
        for ticket in self:
            ticket.ticket_numbers = ", ".join(n for n in ticket.line_ids.mapped("ticket_number") if n) or False

    @api.depends("line_ids.qty_expected", "box_ids.qty", "box_ids.state", "box_ids.product_id", "box_ids.batch")
    def _compute_totals(self):
        for ticket in self:
            ticket.qty_expected_total = sum(ticket.line_ids.mapped("qty_expected"))
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
        lines = self.line_ids.filtered(lambda l: l.product_id == product)
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
                "soll": line.qty_expected, "ist": 0,
            }
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
            } for l in self.line_ids],
            "total": self.qty_expected_total,
            "received": self.qty_received_total,
            "soll_ist": self._grx_soll_ist(),
            "slip_note": self.slip_note or "",
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
        tickets = self.search(Domain("state", "in", ("open", "in_progress")) & Domain.OR(
            Domain(fname, "=ilike", pattern) for fname in SEARCH_FIELDS))
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
        found.sort(key=lambda x: x[0][0])
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
                ("ticket_id.picking_id.state", "=", "done"), ("user_id", "!=", user.id)])
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
        config = self.company_id._plcs_grx_config()
        if not self.picking_id:
            picking_type = config.plcs_grx_receipt_type_id
            src = picking_type.default_location_src_id or self.env.ref("stock.stock_location_customers")
            picking = self.env["stock.picking"].create({
                "picking_type_id": picking_type.id,
                "partner_id": self.partner_id.id,
                "origin": self.name,
                "location_id": src.id,
                "location_dest_id": config.plcs_grx_quarantine_location_id.id,
                "plcs_grx_ticket_id": self.id,
                "company_id": self.company_id.id,
            })
            self.sudo().picking_id = picking
        if self.state == "open":
            self.sudo().state = "in_progress"
        return self._grx_ui_data()

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
            raise UserError(self.env._(
                "Box %s ist noch nicht abgeschlossen. Erst die Box abschließen oder abbrechen.",
                ", ".join(open_boxes.package_id.mapped("name"))))
        if not boxes:
            raise UserError(self.env._("Für dieses Paket ist noch keine Box erfasst."))
        deviations = [r for r in self._grx_soll_ist() if r["status"] != "ok"]
        if deviations and not (deviation_note or "").strip():
            raise UserError(self.env._(
                "Soll und Ist stimmen nicht überein (%s). Bitte eine Begründung angeben.",
                "; ".join(self._grx_row_label(r) for r in deviations)))
        picking = self.picking_id
        if picking.state not in ("done", "cancel"):
            if picking.state == "draft":
                picking.action_confirm()
            for move in picking.move_ids:
                move.picked = True
            picking.with_context(skip_backorder=True, cancel_backorder=True).button_validate()
            if picking.state != "done":
                raise UserError(self.env._("Eingang %s konnte nicht gebucht werden.", picking.name))
        vals = {"state": "done"}
        if deviations:
            vals["deviation_note"] = deviation_note
            self.sudo().message_post(body=self.env._(
                "Paket mit Soll/Ist-Abweichung abgeschlossen: %(dev)s. Begründung: %(note)s",
                dev="; ".join(self._grx_row_label(r) for r in deviations), note=deviation_note))
        self.sudo().write(vals)
        for box in boxes.filtered(lambda b: b.state in ("to_release", "blocked")):
            box.sudo().message_post(body=self.env._("Eingang %s gebucht. Box wartet auf die VP.", picking.name))
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
            if ticket.state == "open" and lines:
                vals["line_ids"] = [Command.clear()] + [Command.create(l) for l in lines]
            ticket.write(vals)
            stats["updated"] += 1
        else:
            vals.update({"source": source, "external_id": ext or False,
                         "line_ids": [Command.create(l) for l in lines]})
            ticket = self.create(vals)
            stats["created"] += 1
        return ticket


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
