import re

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command

from ..tools import gs1


def _norm(value):
    return re.sub(r"[\s\-./]", "", str(value or "")).lower()


class PlcsGreenrxTicket(models.Model):
    _name = "plcs.greenrx.ticket"
    _description = "GreenRX-Ticket (Retourenpaket)"
    _inherit = ["mail.thread"]
    _order = "date_ticket desc, id desc"

    name = fields.Char("Ticketnummer", required=True, index=True, tracking=True)
    reference = fields.Char("Referenznummer", index=True, tracking=True)
    credit_note = fields.Char("Gutschriftnummer", tracking=True)
    partner_id = fields.Many2one("res.partner", "Absender", index=True, tracking=True)
    partner_name = fields.Char("Absender (Name)", help="Name laut GreenRX-ERP, falls kein Kontakt zugeordnet ist.")
    customer_number = fields.Char("Kundennummer", index=True)
    sender_type = fields.Selection(
        [("pharmacy", "Apotheke"), ("wholesaler", "Großhändler"), ("manufacturer", "Hersteller")],
        string="Absender-Art", default="pharmacy")
    date_ticket = fields.Date("Ticketdatum", default=fields.Date.context_today)
    state = fields.Selection(
        [("open", "Offen"), ("in_progress", "In Erfassung"), ("done", "Erfasst"), ("cancel", "Storniert")],
        default="open", required=True, tracking=True, index=True)
    source = fields.Selection(
        [("manual", "Von Hand"), ("import", "Import"), ("api", "Schnittstelle")],
        default="manual", required=True, readonly=True)
    external_id = fields.Char("Externe ID", index=True, readonly=True, copy=False,
                              help="ID des Tickets im GreenRX-ERP.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    line_ids = fields.One2many("plcs.greenrx.ticket.line", "ticket_id", "Inhalt laut Ticket", copy=True)
    picking_id = fields.Many2one("stock.picking", "Eingang (Odoo)", readonly=True, copy=False)
    box_ids = fields.One2many("plcs.greenrx.box", "ticket_id", "Boxen", readonly=True)
    qty_expected_total = fields.Integer("Soll (Packungen)", compute="_compute_totals")
    qty_received_total = fields.Integer("Ist (Packungen)", compute="_compute_totals")
    has_deviation = fields.Boolean("Soll/Ist-Abweichung", compute="_compute_totals")
    deviation_note = fields.Text("Abweichung (Begründung)", readonly=True)
    display_sender = fields.Char("Absender (Anzeige)", compute="_compute_display_sender")

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)", "Diese Ticketnummer gibt es schon.")

    @api.depends("partner_id", "partner_name")
    def _compute_display_sender(self):
        for ticket in self:
            ticket.display_sender = ticket.partner_id.display_name or ticket.partner_name or ""

    @api.depends("line_ids.qty_expected", "box_ids.qty", "box_ids.state", "box_ids.product_id")
    def _compute_totals(self):
        for ticket in self:
            ticket.qty_expected_total = sum(ticket.line_ids.mapped("qty_expected"))
            boxes = ticket.box_ids.filtered(lambda b: b.state != "cancel")
            ticket.qty_received_total = sum(boxes.mapped("qty"))
            ticket.has_deviation = any(row["status"] != "ok" for row in ticket._grx_soll_ist())

    @api.model
    def get_import_templates(self):
        return [{
            "label": self.env._("Vorlage GreenRX-Tickets"),
            "template": "/plcs_greenrx/static/xls/greenrx_tickets_vorlage.csv",
        }]

    # ------------------------------------------------------------------
    # Soll / Ist
    # ------------------------------------------------------------------
    def _grx_soll_ist(self):
        self.ensure_one()
        rows = {}
        for line in self.line_ids:
            key = line.product_id.id or ("name", line.product_name or line.pzn)
            row = rows.setdefault(key, {
                "product_id": line.product_id.id,
                "product": line.product_id.display_name or line.product_name or line.pzn or "?",
                "soll": 0, "ist": 0,
            })
            row["soll"] += line.qty_expected
        for box in self.box_ids.filtered(lambda b: b.state != "cancel" and b.product_id):
            row = rows.setdefault(box.product_id.id, {
                "product_id": box.product_id.id,
                "product": box.product_id.display_name,
                "soll": 0, "ist": 0,
            })
            row["ist"] += box.qty
        result = []
        for row in rows.values():
            if row["soll"] == row["ist"]:
                row["status"] = "ok"
                row["status_text"] = "OK"
            elif row["soll"] == 0:
                row["status"] = "extra"
                row["status_text"] = self.env._("nicht laut Ticket")
            elif row["ist"] > row["soll"]:
                row["status"] = "over"
                row["status_text"] = self.env._("%s zu viel", row["ist"] - row["soll"])
            else:
                row["status"] = "open"
                row["status_text"] = self.env._("%s offen", row["soll"] - row["ist"])
            result.append(row)
        return result

    # ------------------------------------------------------------------
    # Daten für die Scan-Oberfläche
    # ------------------------------------------------------------------
    def _grx_ui_data(self, with_boxes=True):
        self.ensure_one()
        data = {
            "id": self.id,
            "name": self.name,
            "reference": self.reference or "",
            "credit_note": self.credit_note or "",
            "sender": self.display_sender or "",
            "customer_number": self.customer_number or "",
            "state": self.state,
            "picking": self.picking_id.name or "",
            "items": [{"product": l.product_id.display_name or l.product_name or l.pzn or "?",
                       "qty": l.qty_expected} for l in self.line_ids],
            "total": self.qty_expected_total,
            "received": self.qty_received_total,
            "soll_ist": self._grx_soll_ist(),
        }
        if with_boxes:
            data["boxes"] = [b._grx_summary() for b in self.box_ids.filtered(
                lambda b: b.state != "cancel").sorted("id")]
        return data

    @api.model
    def grx_search(self, query, limit=20):
        """Offene Tickets nach Apotheke, Kunden-, Referenz-, Ticket- oder Gutschriftnummer suchen."""
        q = _norm(query)
        if not q:
            return []
        tickets = self.search([("state", "in", ("open", "in_progress"))], limit=1000)
        labels = (
            ("name", self.env._("Ticket")),
            ("reference", self.env._("Referenz")),
            ("credit_note", self.env._("Gutschrift")),
            ("customer_number", self.env._("Kundennr.")),
            ("display_sender", self.env._("Apotheke")),
        )
        found = []
        for ticket in tickets:
            match = False
            # exakter Treffer auf Ticket/Referenz zuerst (gescanntes Paketetikett)
            for fname, label in labels:
                if _norm(ticket[fname]) == q:
                    match = (0, label)
                    break
            if not match:
                for fname, label in labels:
                    if q in _norm(ticket[fname]):
                        match = (1, label)
                        break
            if not match:
                if any(q in _norm(l.product_id.display_name or l.product_name) or q == _norm(l.pzn)
                       for l in ticket.line_ids):
                    match = (2, self.env._("Medikament"))
            if match:
                data = ticket._grx_ui_data(with_boxes=False)
                data["match"] = self.env._("Treffer: %s", match[1])
                found.append((match[0], data))
        found.sort(key=lambda x: x[0])
        return [d for _rank, d in found[:limit]]

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
        """In Erfassung befindliches Ticket fortsetzen (ggf. mit offener Box des Benutzers)."""
        self.ensure_one()
        data = self.grx_start()
        box = self.box_ids.filtered(
            lambda b: b.state in ("scan", "check", "print") and b.user_id == self.env.user)[:1]
        return {"ticket": data, "box": box._grx_ui_data() if box else False}

    def grx_start(self):
        """Paket ausgewählt: Eingang anlegen (falls nötig) und Daten zurückgeben."""
        self.ensure_one()
        if self.state not in ("open", "in_progress"):
            raise UserError(self.env._("Ticket %s ist nicht mehr offen.", self.name))
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
            raise UserError(self.env._("Ticket %s ist nicht in Erfassung.", self.name))
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
                "; ".join("%s: %s" % (r["product"], r["status_text"]) for r in deviations)))
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
                dev="; ".join("%s: %s" % (r["product"], r["status_text"]) for r in deviations),
                note=deviation_note))
        self.sudo().write(vals)
        for box in boxes.filtered(lambda b: b.state in ("to_release", "blocked")):
            box.sudo().message_post(body=self.env._("Eingang %s gebucht. Box wartet auf die VP.", picking.name))
        return self._grx_ui_data()

    def action_cancel(self):
        for ticket in self:
            if ticket.box_ids.filtered(lambda b: b.state not in ("cancel",)):
                raise UserError(self.env._("Ticket %s hat schon erfasste Boxen.", ticket.name))
            if ticket.picking_id and ticket.picking_id.state != "done":
                ticket.picking_id.action_cancel()
            ticket.state = "cancel"

    # ------------------------------------------------------------------
    # Schnittstelle
    # ------------------------------------------------------------------
    @api.model
    def api_upsert(self, tickets, source="api"):
        """Tickets anlegen oder aktualisieren (Schnittstelle GreenRX-ERP).

        tickets: Liste von dicts mit external_id oder name, reference, credit_note,
        partner_name, customer_number, sender_type, date_ticket und
        lines = [{gtin|pzn|product_name, qty}].
        Begonnene Tickets werden nur in den Kopfdaten aktualisiert.
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
            raise UserError(self.env._("Ticketnummer fehlt."))
        ticket = self.browse()
        if ext:
            ticket = self.search([("external_id", "=", ext), ("source", "=", source)], limit=1)
        if not ticket:
            ticket = self.search([("name", "=", name)], limit=1)
        partner = self.env["res.partner"]
        if item.get("customer_number"):
            partner = partner.search([("ref", "=", item["customer_number"])], limit=1)
        vals = {
            "name": name,
            "reference": item.get("reference") or False,
            "credit_note": item.get("credit_note") or False,
            "partner_name": item.get("partner_name") or False,
            "customer_number": item.get("customer_number") or False,
        }
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
                "gtin": line.get("gtin") or False,
                "pzn": line.get("pzn") or False,
                "product_name": line.get("product_name") or False,
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
    _description = "GreenRX-Ticket: Inhalt laut Ticket"

    ticket_id = fields.Many2one("plcs.greenrx.ticket", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", "Artikel")
    product_name = fields.Char("Bezeichnung laut Ticket")
    pzn = fields.Char("PZN")
    gtin = fields.Char("GTIN")
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
