"""Gespiegelte ATIV-Daten (nur lesend für PLCS).

Diese Modelle halten die Daten aus dem GreenRX-ERP von ATIV getrennt von den lokalen
PLCS-Wareneingangsdaten. Die Synchronisierung schreibt ausschließlich hier und in die
Soll-Daten (Positionen) des PLCS-Vorgangs. Boxen, Scans, Ist-Mengen, Ampel, Begründungen und
Eingänge bleiben unberührt.
"""
from odoo import api, fields, models

STATUS_CATEGORIES = [
    ("expected", "Erwartet"),
    ("announced", "Angemeldet (noch nicht genehmigt)"),
    ("closed", "Bei ATIV abgeschlossen"),
    ("cancel", "Abgelehnt / storniert"),
    ("unknown", "Unbekannt"),
]


class PlcsAtivCreditNote(models.Model):
    _name = "plcs.ativ.credit.note"
    _description = "ATIV-Gutschrift (Quelle, nur lesen)"
    _order = "date_order desc, id desc"

    name = fields.Char("Gutschriftnummer", index=True, readonly=True)
    connector_id = fields.Many2one("plcs.greenrx.connector", "Schnittstelle", required=True, index=True,
                                   ondelete="cascade", readonly=True)
    company_id = fields.Many2one(related="connector_id.company_id", store=True, index=True)
    source_key = fields.Char("Quellidentität", required=True, index=True, readonly=True,
                             help="Quellsystem:Modell:ID bei ATIV. Technischer Schlüssel, kein Anzeigename.")
    source_id = fields.Integer("ID bei ATIV", readonly=True)
    grx_ticket_id = fields.Many2one("plcs.greenrx.ticket", "PLCS-Vorgang", index=True, readonly=True,
                                    ondelete="set null")
    vorgang_name = fields.Char("Vorgangsnummer", index=True, readonly=True)
    vorgang_key = fields.Char("Quellidentität Vorgang", index=True, readonly=True)
    vorgang_source = fields.Char("Herkunft des Vorgangs", readonly=True)
    complaint_number = fields.Char("Reklamationsnummer", index=True, readonly=True)
    partner_name = fields.Char("Kunde", readonly=True)
    customer_number = fields.Char("Kundennummer", index=True, readonly=True)
    partner_email = fields.Char("E-Mail", readonly=True)
    partner_phone = fields.Char("Telefon", readonly=True)
    partner_address = fields.Text("Kundenadresse", readonly=True)
    partner_city = fields.Char("Ort", readonly=True)
    pickup_address = fields.Text("Abholadresse", readonly=True)
    pickup_date = fields.Datetime("Abholtermin (geplant)", readonly=True,
                                  help="Feld „Erwartete Ankunft“ (date_planned) der Gutschrift bei ATIV.")
    pickup_done = fields.Boolean("Abholung erfolgt", readonly=True)
    source_state = fields.Char("Status (technisch)", readonly=True)
    source_state_label = fields.Char("Status Gutschrift", readonly=True)
    date_order = fields.Datetime("Datum Gutschrift", readonly=True)
    date_approve = fields.Datetime("Bestätigt am", readonly=True)
    receipt_status = fields.Char("Eingangsstatus ATIV", readonly=True)
    ativ_receipt_done = fields.Boolean("Wareneingang bei ATIV abgeschlossen", readonly=True)
    quarantine_reason = fields.Char("Quarantänegrund", readonly=True)
    amount_untaxed = fields.Float("Netto", readonly=True)
    amount_tax = fields.Float("Steuer", readonly=True)
    amount_total = fields.Float("Brutto", readonly=True)
    currency_name = fields.Char("Währung", readonly=True)
    created_by = fields.Char("Angelegt von (ATIV)", readonly=True)
    buyer = fields.Char("Zuständig (ATIV)", readonly=True)
    source_create_date = fields.Datetime("Angelegt bei ATIV", readonly=True)
    source_write_date = fields.Datetime("Geändert bei ATIV", readonly=True)
    status_category = fields.Selection(STATUS_CATEGORIES, "Einordnung", readonly=True, index=True)
    status_reason = fields.Char("Begründung Einordnung", readonly=True)
    line_ids = fields.One2many("plcs.ativ.credit.note.line", "credit_note_id", "Positionen", readonly=True)
    ativ_ticket_ids = fields.Many2many("plcs.ativ.ticket", "plcs_ativ_credit_note_ticket_rel", "credit_note_id",
                                       "ticket_id", "Tickets", readonly=True)
    document_ids = fields.One2many("plcs.ativ.document", "credit_note_id", "Dokumente (Gutschrift)", readonly=True)
    source_missing = fields.Boolean("Bei ATIV nicht mehr gefunden", readonly=True,
                                    help="Beim letzten Vollabgleich fehlte die Gutschrift bei ATIV. "
                                         "Der Datensatz wird nicht automatisch gelöscht.")
    contract_version = fields.Char("Datenvertrag", readonly=True)
    payload_hash = fields.Char("Prüfsumme", readonly=True)
    payload_json = fields.Text("Übertragene Daten", readonly=True)
    last_sync = fields.Datetime("Zuletzt abgeglichen", readonly=True)
    ticket_numbers = fields.Char("Ticketnummern", compute="_compute_ticket_numbers", store=True, index="trigram")

    _source_uniq = models.Constraint("UNIQUE(connector_id, source_key)", "Gutschrift ist schon vorhanden.")

    @api.depends("ativ_ticket_ids.ticket_number")
    def _compute_ticket_numbers(self):
        for rec in self:
            rec.ticket_numbers = ", ".join(n for n in rec.ativ_ticket_ids.mapped("ticket_number") if n) or False


class PlcsAtivCreditNoteLine(models.Model):
    _name = "plcs.ativ.credit.note.line"
    _description = "ATIV-Gutschrift: Position (Quelle, nur lesen)"
    _order = "credit_note_id, sequence, id"

    credit_note_id = fields.Many2one("plcs.ativ.credit.note", "Gutschrift", required=True, index=True,
                                     ondelete="cascade", readonly=True)
    source_key = fields.Char("Quellidentität", required=True, index=True, readonly=True)
    source_id = fields.Integer("ID bei ATIV", readonly=True)
    sequence = fields.Integer(readonly=True)
    ativ_ticket_id = fields.Many2one("plcs.ativ.ticket", "Ticket", readonly=True, index=True)
    ticket_number = fields.Char("Ticketnummer", readonly=True)
    product_code = fields.Char("PZN", readonly=True)
    product_barcode = fields.Char("GTIN/Barcode", readonly=True)
    product_name = fields.Char("Artikel", readonly=True)
    description = fields.Char("Beschreibung", readonly=True)
    qty = fields.Float("Menge (Soll)", readonly=True)
    uom = fields.Char("Einheit", readonly=True)
    price_unit = fields.Float("Einzelpreis", readonly=True)
    taxes = fields.Char("Steuern", readonly=True)
    price_subtotal = fields.Float("Netto", readonly=True)
    price_tax = fields.Float("Steuer", readonly=True)
    price_total = fields.Float("Brutto", readonly=True)
    batch = fields.Char("Charge", readonly=True)
    expiry_date = fields.Date("Verfall", readonly=True)
    storage_conditions = fields.Char("Lagerbedingungen", readonly=True)
    condition_note = fields.Char("Zustand / Quarantänegrund", readonly=True)
    qty_sellable = fields.Float("ATIV: verkaufsfähig", readonly=True)
    qty_quarantine = fields.Float("ATIV: Quarantäne", readonly=True)
    qty_blocked = fields.Float("ATIV: gesperrt", readonly=True)
    qty_destroyed = fields.Float("ATIV: vernichtet", readonly=True)
    removed = fields.Boolean("Bei ATIV entfernt", readonly=True)
    grx_line_id = fields.Many2one("plcs.greenrx.ticket.line", "PLCS-Position", readonly=True, index=True,
                                  ondelete="set null")

    _source_uniq = models.Constraint("UNIQUE(credit_note_id, source_key)", "Position ist schon vorhanden.")


class PlcsAtivTicket(models.Model):
    _name = "plcs.ativ.ticket"
    _description = "ATIV-Retourenticket (Quelle, nur lesen)"
    _order = "ticket_number desc, id desc"
    _rec_name = "ticket_number"

    connector_id = fields.Many2one("plcs.greenrx.connector", "Schnittstelle", required=True, index=True,
                                   ondelete="cascade", readonly=True)
    company_id = fields.Many2one(related="connector_id.company_id", store=True, index=True)
    source_key = fields.Char("Quellidentität", required=True, index=True, readonly=True)
    source_id = fields.Integer("ID bei ATIV", readonly=True)
    ticket_number = fields.Char("Ticketnummer", index=True, readonly=True)
    title = fields.Char("Titel (bei ATIV = Charge)", readonly=True)
    description = fields.Html("Beschreibung", readonly=True, sanitize=True)
    stage_name = fields.Char("Bearbeitungsstand", readonly=True)
    stage_key = fields.Char("Bearbeitungsstand (technisch)", readonly=True)
    stage_category = fields.Selection(STATUS_CATEGORIES, "Einordnung", readonly=True)
    vorgang_name = fields.Char("Vorgangsnummer", readonly=True)
    complaint_number = fields.Char("Reklamationsnummer", readonly=True)
    partner_name = fields.Char("Kunde", readonly=True)
    partner_email = fields.Char("E-Mail", readonly=True)
    partner_phone = fields.Char("Telefon", readonly=True)
    address = fields.Text("Adresse laut Ticket", readonly=True)
    alt_pickup_address = fields.Text("Abweichende Abholadresse", readonly=True)
    product_name = fields.Char("Artikel", readonly=True)
    pzn = fields.Char("PZN", readonly=True)
    qty = fields.Float("Packungen", readonly=True)
    price = fields.Float("Preis je Packung", readonly=True)
    batch = fields.Char("Charge", readonly=True)
    expiry_date = fields.Date("Verfall", readonly=True)
    storage_conditions = fields.Char("Lagerbedingungen", readonly=True)
    return_reason = fields.Char("Retourengrund", readonly=True)
    gdp_confirmation = fields.Char("GDP-Lagerung bestätigt", readonly=True)
    rejection_reason = fields.Char("Ablehnungsgrund", readonly=True)
    pickup_done = fields.Boolean("Abholung erfolgt", readonly=True)
    assigned_user = fields.Char("Bearbeiter (ATIV)", readonly=True)
    created_by = fields.Char("Angelegt von (ATIV)", readonly=True)
    source_create_date = fields.Datetime("Angelegt bei ATIV", readonly=True)
    source_write_date = fields.Datetime("Geändert bei ATIV", readonly=True)
    close_date = fields.Datetime("Geschlossen am", readonly=True)
    credit_note_ids = fields.Many2many("plcs.ativ.credit.note", "plcs_ativ_credit_note_ticket_rel", "ticket_id",
                                       "credit_note_id", "Gutschriften", readonly=True)
    message_ids = fields.One2many("plcs.ativ.message", "ativ_ticket_id", "Verlauf", readonly=True)
    document_ids = fields.One2many("plcs.ativ.document", "ativ_ticket_id", "Dokumente", readonly=True)

    _source_uniq = models.Constraint("UNIQUE(connector_id, source_key)", "Ticket ist schon vorhanden.")

    @api.depends("ticket_number", "title")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s (%s)" % (rec.ticket_number or "?", rec.title) if rec.title else (rec.ticket_number or "?")


class PlcsAtivMessage(models.Model):
    _name = "plcs.ativ.message"
    _description = "ATIV-Verlauf (Nachrichten, Notizen, Statuswechsel)"
    _order = "date desc, id desc"

    connector_id = fields.Many2one("plcs.greenrx.connector", required=True, index=True, ondelete="cascade",
                                   readonly=True)
    source_key = fields.Char("Quellidentität", required=True, index=True, readonly=True)
    ativ_ticket_id = fields.Many2one("plcs.ativ.ticket", "Ticket", index=True, ondelete="cascade", readonly=True)
    credit_note_id = fields.Many2one("plcs.ativ.credit.note", "Gutschrift", index=True, ondelete="cascade",
                                     readonly=True)
    kind = fields.Selection([
        ("comment", "Nachricht"), ("note", "Notiz"), ("notification", "Systemmeldung"),
        ("observed", "Statuswechsel (beim Abruf erkannt)"),
    ], "Art", readonly=True)
    date = fields.Datetime("Zeitpunkt (ATIV)", readonly=True)
    author = fields.Char("Autor (ATIV)", readonly=True,
                         help="Herkunftsangabe aus ATIV. Es wird kein Benutzerkonto angelegt.")
    subtype = fields.Char("Untertyp", readonly=True)
    body = fields.Html("Inhalt", readonly=True, sanitize=True)
    body_hash = fields.Char("Prüfsumme Inhalt (ATIV)", readonly=True, copy=False,
                            help="SHA-256 des Inhalts, wie ATIV ihn liefert. Erkennt nachträglich bearbeitete "
                                 "Nachrichten, ohne den bereinigten (sanitized) Inhalt zu vergleichen.")
    tracking = fields.Text("Änderungen", readonly=True)
    attachment_names = fields.Char("Anhänge", readonly=True)

    _source_uniq = models.Constraint("UNIQUE(connector_id, source_key)", "Nachricht ist schon vorhanden.")


class PlcsAtivDocument(models.Model):
    _name = "plcs.ativ.document"
    _description = "ATIV-Dokument (Lieferschein, PDF, Foto)"
    _order = "source_create_date desc, id desc"

    connector_id = fields.Many2one("plcs.greenrx.connector", required=True, index=True, ondelete="cascade",
                                   readonly=True)
    company_id = fields.Many2one(related="connector_id.company_id", store=True)
    source_key = fields.Char("Quellidentität", required=True, index=True, readonly=True)
    source_id = fields.Integer("ID bei ATIV", readonly=True)
    name = fields.Char("Dateiname", readonly=True)
    mimetype = fields.Char("Typ", readonly=True)
    file_size = fields.Integer("Größe (Byte)", readonly=True)
    checksum = fields.Char("Prüfsumme (ATIV)", readonly=True)
    source_model = fields.Char("Gehört zu (ATIV)", readonly=True)
    source_res_id = fields.Integer(readonly=True)
    source_create_date = fields.Datetime("Hochgeladen bei ATIV", readonly=True)
    author = fields.Char("Hochgeladen von (ATIV)", readonly=True)
    ativ_ticket_id = fields.Many2one("plcs.ativ.ticket", "Ticket", index=True, ondelete="cascade", readonly=True)
    credit_note_id = fields.Many2one("plcs.ativ.credit.note", "Gutschrift", index=True, ondelete="cascade",
                                     readonly=True)
    grx_ticket_id = fields.Many2one("plcs.greenrx.ticket", "PLCS-Vorgang", index=True, ondelete="set null",
                                    readonly=True)
    attachment_id = fields.Many2one("ir.attachment", "Datei in PLCS", readonly=True, ondelete="set null")
    state = fields.Selection([
        ("meta", "Nur Angaben (Abruf bei Bedarf)"), ("queued", "Wird übernommen"),
        ("copied", "In PLCS gespeichert"), ("error", "Fehler"),
    ], "Datei", default="meta", readonly=True)
    error = fields.Char("Fehler", readonly=True)
    is_image = fields.Boolean(compute="_compute_is_image")

    _source_uniq = models.Constraint("UNIQUE(connector_id, source_key)", "Dokument ist schon vorhanden.")

    @api.depends("mimetype")
    def _compute_is_image(self):
        for doc in self:
            doc.is_image = (doc.mimetype or "").startswith("image/")

    def action_fetch(self):
        """Datei jetzt von ATIV holen (mit den Rechten der Schnittstelle) und in PLCS speichern."""
        for connector in self.connector_id:
            docs = self.filtered(lambda d: d.connector_id == connector)
            connector.sudo()._ativ_fetch_documents(docs.sudo())
        if len(self) == 1 and self.attachment_id:
            return self.action_open()
        return True

    def action_open(self):
        self.ensure_one()
        if not self.attachment_id:
            return self.action_fetch()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=%s" % (self.attachment_id.id, "false" if self.is_image or
                                                     self.mimetype == "application/pdf" else "true"),
            "target": "new",
        }


class PlcsAtivRun(models.Model):
    _name = "plcs.ativ.run"
    _description = "ATIV-Synchronisierung: Lauf"
    _order = "id desc"

    connector_id = fields.Many2one("plcs.greenrx.connector", required=True, index=True, ondelete="cascade",
                                   readonly=True)
    mode = fields.Selection([
        ("full", "Erstübernahme / Vollabgleich"), ("incremental", "Änderungen"),
        ("single", "Einzelner Vorgang"),
        ("products", "Artikelstamm: Änderungen"), ("products_full", "Artikelstamm: alle Artikel"),
    ], "Art", readonly=True)
    trigger = fields.Selection([("cron", "Zeitplan"), ("manual", "Von Hand"), ("webhook", "Webhook")],
                               "Auslöser", readonly=True)
    user_id = fields.Many2one("res.users", "Gestartet von", readonly=True)
    state = fields.Selection([
        ("running", "Läuft"), ("done", "Erfolgreich"), ("partial", "Mit Fehlern"), ("failed", "Fehlgeschlagen"),
    ], default="running", readonly=True, index=True)
    started = fields.Datetime("Beginn", readonly=True, default=fields.Datetime.now)
    finished = fields.Datetime("Ende", readonly=True)
    cursor_from = fields.Datetime("Änderungen seit", readonly=True)
    cursor_candidate = fields.Datetime("Neuer Fortschrittsstand", readonly=True,
                                       help="Wird erst übernommen, wenn alle Positionen dieses Laufs erfolgreich "
                                            "verarbeitet sind.")
    count_planned = fields.Integer("Gutschriften geplant", readonly=True)
    count_cn_created = fields.Integer("Gutschriften neu", readonly=True)
    count_cn_updated = fields.Integer("Gutschriften aktualisiert", readonly=True)
    count_unchanged = fields.Integer("Gutschriften unverändert", readonly=True)
    count_created = fields.Integer("Vorgänge neu", readonly=True)
    count_updated = fields.Integer("Vorgänge aktualisiert", readonly=True)
    count_failed = fields.Integer("Fehler", readonly=True)
    count_missing = fields.Integer("Bei ATIV nicht mehr vorhanden", readonly=True)
    count_documents = fields.Integer("Dateien übernommen", readonly=True)
    count_products_created = fields.Integer("Artikel neu", readonly=True)
    count_products_updated = fields.Integer("Artikel ergänzt", readonly=True)
    count_products_skipped = fields.Integer("Artikel übersprungen", readonly=True,
                                            help="Ohne gültige PZN oder PZN nicht eindeutig.")
    count_products_differ = fields.Integer("Artikel mit Abweichung", readonly=True,
                                           help="Bezeichnung oder Lagertemperatur in PLCS weicht von ATIV ab.")
    count_images = fields.Integer("Produktbilder übernommen", readonly=True)
    message = fields.Text("Ergebnis", readonly=True)
    queue_ids = fields.One2many("plcs.ativ.queue", "run_id", "Positionen", readonly=True)
    pending_count = fields.Integer("Offen", compute="_compute_queue_counts")
    failed_count = fields.Integer("Fehlgeschlagen", compute="_compute_queue_counts")

    def _compute_queue_counts(self):
        Queue = self.env["plcs.ativ.queue"]
        for run in self:
            run.pending_count = Queue.search_count([("run_id", "=", run.id), ("state", "=", "pending")])
            run.failed_count = Queue.search_count([("run_id", "=", run.id), ("state", "=", "failed")])

    @api.depends("mode", "started")
    def _compute_display_name(self):
        modes = dict(self._fields["mode"].selection)
        for run in self:
            run.display_name = "%s %s" % (modes.get(run.mode, ""), fields.Datetime.to_string(run.started) or "")

    def action_retry_failed(self):
        self.env["plcs.ativ.queue"].search([("run_id", "in", self.ids), ("state", "=", "failed")]).action_retry()
        self.sudo().filtered(lambda r: r.state in ("partial", "failed")).write({"state": "running", "finished": False})
        self.connector_id.sudo()._ativ_trigger_worker()
        return True


class PlcsAtivQueue(models.Model):
    _name = "plcs.ativ.queue"
    _description = "ATIV-Synchronisierung: Arbeitsschritt"
    _order = "id"

    connector_id = fields.Many2one("plcs.greenrx.connector", required=True, index=True, ondelete="cascade",
                                   readonly=True)
    run_id = fields.Many2one("plcs.ativ.run", "Lauf", index=True, ondelete="set null", readonly=True)
    kind = fields.Selection([
        ("po", "Gutschrift abgleichen"), ("ref", "Änderung zuordnen (Webhook)"), ("document", "Datei übernehmen"),
        ("product", "Artikel abgleichen"),
    ], required=True, readonly=True)
    res_model = fields.Char("Modell (ATIV)", readonly=True)
    res_id = fields.Integer("ID (ATIV)", readonly=True, index=True)
    document_id = fields.Many2one("plcs.ativ.document", ondelete="cascade", readonly=True)
    state = fields.Selection([("pending", "Offen"), ("done", "Erledigt"), ("failed", "Fehlgeschlagen")],
                             default="pending", required=True, index=True, readonly=True)
    attempts = fields.Integer("Versuche", readonly=True)
    next_try = fields.Datetime("Nächster Versuch", readonly=True)
    error = fields.Text("Fehler", readonly=True)
    done_at = fields.Datetime("Erledigt am", readonly=True)

    def action_retry(self):
        self.sudo().filtered(lambda q: q.state == "failed").write(
            {"state": "pending", "next_try": False, "attempts": 0, "error": False})
        self.connector_id.sudo()._ativ_trigger_worker()
        return True
