"""Synchronisierung ATIV → PLCS (Datenvertrag plcs.ativ.gutschrift/1).

Ablauf
------
1. **Planen** (12-Stunden-Zeitplan, Knopf „ATIV-Daten synchronisieren“ oder Vollabgleich):
   Die IDs der betroffenen ATIV-Gutschriften werden ermittelt und als Arbeitsschritte in die
   Warteschlange gestellt. Bei „Änderungen“ zählen auch geänderte Positionen, Tickets, Vorgänge,
   Kunden, Anhänge und Nachrichten, selbst wenn die Gutschrift selbst unverändert ist.
2. **Abarbeiten** (Hintergrund-Job): Gutschriften werden portionsweise gelesen, als Datenvertrag
   aufgebaut und eingespielt. Jede Portion wird einzeln gespeichert. Fehler werden je Gutschrift
   protokolliert und später erneut versucht.
3. **Abschließen**: Erst wenn alle Schritte eines Laufs erfolgreich waren, wird der
   Fortschrittsstand (``ativ_cursor``) übernommen.

Ein Webhook aus ATIV stellt nur einen Arbeitsschritt „Änderung zuordnen“ ein; die Daten selbst
werden immer über die API gelesen.
"""
import ast
import base64
import hashlib
import json
import logging
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules import module as odoo_module
from odoo.tools import SQL

from ..tools.ativ_client import AtivClient, AtivError, binary_content

_logger = logging.getLogger(__name__)

CONTRACT_VERSION = "plcs.ativ.gutschrift/1"
OVERLAP = timedelta(minutes=15)
RETRY_DELAYS = (5, 15, 60, 240, 720)  # Minuten
SOURCE_MODELS = ("purchase.order", "purchase.order.line", "helpdesk.ticket", "x_vorgang", "ir.attachment",
                 "res.partner", "mail.message")

# Gewünschte Felder je ATIV-Modell: (Pflichtfelder, optionale Felder). Optionale Felder, die es bei
# ATIV nicht gibt, werden weggelassen und im Verbindungstest gemeldet.
FIELDS = {
    "purchase.order": (
        ("name", "partner_id", "state", "order_line", "write_date", "create_date"),
        ("x_studio_vorgang", "x_studio_ticket_1", "x_studio_complaint_number", "x_studio_pick_up_done",
         "x_wareneingang_done", "date_order", "date_approve", "date_planned", "create_uid", "user_id",
         "amount_untaxed", "amount_tax", "amount_total", "currency_id", "receipt_status", "company_id",
         "x_studio_quarantne_grund"),
    ),
    "purchase.order.line": (
        ("order_id", "product_id", "product_qty", "write_date"),
        ("name", "sequence", "display_type", "product_uom_id", "price_unit", "tax_ids", "price_subtotal",
         "price_tax", "price_total", "x_studio_ticket", "x_studio_sellable_qty", "x_studio_quarantine_qty",
         "x_studio_blocked_qty", "x_studio_destroyed_qty", "x_studio_quarantne_grund"),
    ),
    "helpdesk.ticket": (
        ("stage_id", "write_date"),
        ("name", "ticket_ref", "description", "partner_id", "partner_name", "partner_email", "partner_phone",
         "user_id", "create_uid", "create_date", "close_date", "x_studio_vorgang", "x_studio_complaint_number",
         "x_studio_batch_number", "x_studio_product", "x_studio_pzn", "x_studio_product_number",
         "x_studio_product_price", "x_studio_expiration_date", "x_studio_reason_for_product_return",
         "x_studio_confirmation_of_gdp_compliant_storage_1", "x_studio_rejection_reason",
         "x_studio_reason_for_rejection", "x_studio_abholadresse_kontaktadresse",
         "x_studio_contact_address_name", "x_studio_strae_hausnummer", "x_studio_strae2", "x_studio_plz",
         "x_studio_stadt", "x_studio_land", "x_studio_storage_conditions", "x_studio_pick_up_done"),
    ),
    "x_vorgang": (
        ("x_name",),
        ("x_complaint_number", "x_partner_id", "x_source", "create_date", "write_date"),
    ),
    "res.partner": (
        ("name",),
        ("display_name", "x_studio_vendor_number", "ref", "street", "street2", "zip", "city", "country_id",
         "email", "phone"),
    ),
    "product.product": (
        ("display_name",),
        ("default_code", "barcode", "name", "x_studio_storage_conditions"),
    ),
    "ir.attachment": (
        ("name", "res_model", "res_id"),
        ("mimetype", "file_size", "checksum", "create_date", "write_date", "create_uid", "type"),
    ),
    "mail.message": (
        ("model", "res_id", "date"),
        ("author_id", "message_type", "subtype_id", "body", "attachment_ids", "tracking_value_ids",
         "email_from"),
    ),
}

DEFAULT_BASE_DOMAIN = "[('write_date', '>', '2025-11-26 16:53:43')]"


def _m2o_id(value):
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return False


def _m2o_name(value):
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return value[1] or False
    return False


def _text(value):
    if value in (False, None):
        return False
    if isinstance(value, (list, tuple)):
        return _m2o_name(value)
    return str(value).strip() or False


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _chunks(seq, size):
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _names(value):
    return {n.strip().lower() for n in (value or "").split(",") if n.strip()}


class PlcsGreenrxConnector(models.Model):
    _inherit = "plcs.greenrx.connector"

    mode = fields.Selection([
        ("ativ", "ATIV-Gutschriften (Datenvertrag v1)"),
        ("legacy", "Helpdesk-Tickets (alte Feldzuordnung)"),
    ], string="Art der Schnittstelle", default="ativ", required=True)
    protocol = fields.Selection([("json2", "JSON-2-API (empfohlen)"), ("xmlrpc", "XML-RPC")],
                                string="Protokoll", default="json2", required=True)
    timeout = fields.Integer("Zeitlimit je Aufruf (s)", default=60)
    source_code = fields.Char("Kennung Quellsystem",
                              help="Teil der Quellidentität (Quellsystem:Modell:ID). Leer = Datenbankname bei ATIV. "
                                   "Nach der Erstübernahme nicht mehr ändern.")
    ativ_base_domain = fields.Char("Filter Gutschriften (Domain)", default=DEFAULT_BASE_DOMAIN,
                                   help="Welche ATIV-Gutschriften übertragen werden. Standard wie Favorit "
                                        "„ohne Test“ bei ATIV.")
    stages_expected = fields.Char("Stufen „erwartet“", default="Approved,Picked Up,On Hold",
                                  help="Ticketstufen (englischer Name bei ATIV), bei denen PLCS das Paket erwartet.")
    stages_announced = fields.Char("Stufen „angemeldet“", default="New,In Progress")
    stages_closed = fields.Char("Stufen „abgeschlossen“", default="Receipt,Stock,Sold,Archive")
    stages_cancel = fields.Char("Stufen „abgelehnt/storniert“", default="Rejected,Canceled,Cancelled")
    expected_since = fields.Date("Erwartet erst ab Gutschrift-Datum",
                                 help="Gutschriften vor diesem Datum gelten als bereits eingegangen, auch wenn ATIV "
                                      "sie noch als erwartet führt. Leer = kein Stichtag.")
    doc_copy = fields.Selection([
        ("expected", "Dateien erwarteter Vorgänge kopieren, übrige bei Bedarf"),
        ("all", "Alle Dateien kopieren"),
        ("none", "Keine Dateien kopieren, nur bei Bedarf abrufen"),
    ], string="Dokumente", default="expected", required=True)
    batch_size = fields.Integer("Gutschriften je Portion", default=25)
    max_attempts = fields.Integer("Versuche je Schritt", default=5)
    ativ_cursor = fields.Datetime("Fortschrittsstand", readonly=True, copy=False,
                                  help="Änderungen bis zu diesem Zeitpunkt sind vollständig übernommen.")
    last_success_run_id = fields.Many2one("plcs.ativ.run", "Letzter erfolgreicher Lauf", readonly=True, copy=False)
    last_success_date = fields.Datetime(related="last_success_run_id.finished", string="Letzter erfolgreicher Lauf am")
    current_run_id = fields.Many2one("plcs.ativ.run", compute="_compute_run_info", string="Letzter Lauf")
    run_state = fields.Selection([
        ("running", "Läuft"), ("done", "Erfolgreich"), ("partial", "Mit Fehlern"), ("failed", "Fehlgeschlagen"),
    ], string="Laufstatus", compute="_compute_run_info")
    queue_pending = fields.Integer("Offene Schritte", compute="_compute_run_info")
    queue_failed = fields.Integer("Fehlgeschlagene Schritte", compute="_compute_run_info")
    tracking_readable = fields.Boolean("Statushistorie lesbar", default=True, readonly=True, copy=False,
                                       help="Ob der API-Benutzer die Feldänderungen (Statuswechsel) bei ATIV lesen darf. "
                                            "Sonst erfasst PLCS Statuswechsel beim Abruf selbst.")
    webhook_enabled = fields.Boolean("Webhook annehmen", default=False)
    webhook_token_hash = fields.Char(groups="stock.group_stock_manager", copy=False, readonly=True)
    webhook_url = fields.Char("Webhook-Adresse (ohne Schlüssel)", compute="_compute_webhook_url")
    credit_note_count = fields.Integer("Gutschriften", compute="_compute_counts")

    # ------------------------------------------------------------------
    def _compute_run_info(self):
        Run = self.env["plcs.ativ.run"]
        Queue = self.env["plcs.ativ.queue"]
        for rec in self:
            rec.current_run_id = Run.search([("connector_id", "=", rec.id)], limit=1) if rec.id else Run
            rec.run_state = rec.current_run_id.state
            rec.queue_pending = Queue.search_count([("connector_id", "=", rec.id), ("state", "=", "pending")]) if rec.id else 0
            rec.queue_failed = Queue.search_count([("connector_id", "=", rec.id), ("state", "=", "failed")]) if rec.id else 0

    def _compute_counts(self):
        CN = self.env["plcs.ativ.credit.note"]
        for rec in self:
            rec.credit_note_count = CN.search_count([("connector_id", "=", rec.id)]) if rec.id else 0

    def _compute_webhook_url(self):
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url") or ""
        for rec in self:
            rec.webhook_url = "%s/plcs_greenrx/ativ/webhook?token=…" % base.rstrip("/")

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
    def _ativ_client(self):
        self.ensure_one()
        try:
            return AtivClient(self.url, self.db, self.login, self.sudo().api_key, protocol=self.protocol,
                              timeout=self.timeout or 60)
        except AtivError as e:
            raise UserError(self.env._("Schnittstelle %(n)s: %(e)s", n=self.name, e=e)) from e

    def _ativ_source_code(self):
        return (self.source_code or self.db or "ativ").strip()

    def _ativ_key(self, model, rec_id):
        return "%s:%s:%s" % (self._ativ_source_code(), model, rec_id)

    def _ativ_base_domain(self):
        try:
            domain = ast.literal_eval(self.ativ_base_domain or "[]")
        except (ValueError, SyntaxError) as e:
            raise UserError(self.env._("Filter Gutschriften ist ungültig: %s", e)) from e
        if not isinstance(domain, list):
            raise UserError(self.env._("Filter Gutschriften muss eine Liste sein."))
        return domain

    def _ativ_fields(self, client, model):
        """Felder, die bei ATIV vorhanden sind; Pflichtfelder fehlen → Fehler (Verbindungstest)."""
        cache = client.__dict__.setdefault("_fields_cache", {})
        if model not in cache:
            info = client.call(model, "fields_get", attributes=["type", "string", "selection"])
            required, optional = FIELDS[model]
            missing = [f for f in required if f not in info]
            if missing:
                raise AtivError("Pflichtfelder fehlen bei ATIV in %s: %s" % (model, ", ".join(missing)))
            cache[model] = {
                "fields": [f for f in required + optional if f in info],
                "missing": [f for f in optional if f not in info],
                "info": info,
            }
        return cache[model]

    def action_ativ_test(self):
        """Verbindung, Rechte und Felder prüfen (nur lesend)."""
        self.ensure_one()
        client = self._ativ_client()
        notes = []
        try:
            for model in ("purchase.order", "purchase.order.line", "helpdesk.ticket", "x_vorgang", "res.partner",
                          "product.product", "ir.attachment", "mail.message"):
                spec = self._ativ_fields(client, model)
                if spec["missing"]:
                    notes.append("%s: %s fehlt" % (model, ", ".join(spec["missing"])))
            count = client.call("purchase.order", "search_count", domain=self._ativ_base_domain())
            tracking = True
            try:
                client.call("mail.tracking.value", "search", domain=[], limit=1)
            except AtivError:
                tracking = False
        except AtivError as e:
            raise UserError(self.env._("Verbindungstest fehlgeschlagen: %s", e)) from e
        self.sudo().tracking_readable = tracking
        message = self.env._("%s Gutschriften entsprechen dem Filter.", count)
        if not tracking:
            message += " " + self.env._("Statushistorie bei ATIV nicht lesbar – PLCS erfasst Statuswechsel beim Abruf.")
        if notes:
            message += " " + self.env._("Fehlende optionale Felder: %s", "; ".join(notes))
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("Verbindung in Ordnung"), "message": message,
                       "type": "warning" if notes else "success", "sticky": bool(notes)},
        }

    def action_test(self):
        if self.mode == "ativ":
            return self.action_ativ_test()
        return super().action_test()

    def action_sync(self, incremental=False):
        if self.mode == "ativ":
            return self.action_ativ_sync()
        return super().action_sync(incremental=incremental)

    @api.model
    def _cron_sync(self):
        # Alte 15-Minuten-Abfrage nur noch für die alte Feldzuordnung
        for connector in self.search([("sync_enabled", "=", True), ("mode", "=", "legacy")]):
            try:
                with self.env.cr.savepoint():
                    connector.action_sync(incremental=True)
            except Exception as e:  # noqa: BLE001
                _logger.exception("GreenRX-Abruf %s fehlgeschlagen", connector.name)
                connector.last_result = self.env._("Fehler: %s", e)

    # ------------------------------------------------------------------
    # Knöpfe
    # ------------------------------------------------------------------
    def _check_manager(self):
        if not (self.env.su or self.env.user.has_group("stock.group_stock_manager")):
            raise AccessError(self.env._("Nur die Lagerleitung darf die ATIV-Schnittstelle bedienen."))

    def action_ativ_sync(self):
        """„ATIV-Daten synchronisieren“: Änderungen seit dem letzten Stand (beim ersten Mal alles)."""
        self.ensure_one()
        self._check_manager()
        run, message = self._ativ_plan("incremental", "manual")
        return self._notify(message, run)

    def action_ativ_full(self):
        """Vollabgleich: alle Gutschriften des Filters erneut prüfen, fehlende kennzeichnen."""
        self.ensure_one()
        self._check_manager()
        run, message = self._ativ_plan("full", "manual")
        return self._notify(message, run)

    def action_ativ_retry(self):
        self.ensure_one()
        self._check_manager()
        failed = self.env["plcs.ativ.queue"].search([("connector_id", "=", self.id), ("state", "=", "failed")])
        failed.run_id.filtered(lambda r: r.state in ("partial", "failed")).write({"state": "running", "finished": False})
        failed.action_retry()
        return self._notify(self.env._("%s fehlgeschlagene Schritte werden wiederholt.", len(failed)), False)

    def action_ativ_disable(self):
        self.ensure_one()
        self._check_manager()
        self.write({"sync_enabled": False, "webhook_enabled": False})
        return self._notify(self.env._("Automatischer Abruf und Webhook sind ausgeschaltet."), False)

    def action_generate_webhook_token(self):
        """Neuen Webhook-Schlüssel erzeugen. Er wird einmal angezeigt und nur als Prüfsumme gespeichert."""
        self.ensure_one()
        self._check_manager()
        token = secrets.token_urlsafe(32)
        self.sudo().write({"webhook_token_hash": hashlib.sha256(token.encode()).hexdigest(), "webhook_enabled": True})
        base = self.env["ir.config_parameter"].sudo().get_str("web.base.url") or ""
        url = "%s/plcs_greenrx/ativ/webhook?token=%s" % (base.rstrip("/"), token)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": self.env._("Webhook-Adresse (nur jetzt sichtbar)"),
                "message": self.env._("In ATIV als Webhook-URL eintragen: %s", url),
                "type": "warning", "sticky": True,
            },
        }

    def action_open_runs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("ATIV-Läufe"), "res_model": "plcs.ativ.run",
            "view_mode": "list,form", "domain": [("connector_id", "=", self.id)],
        }

    def action_open_queue(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("ATIV-Warteschlange"), "res_model": "plcs.ativ.queue",
            "view_mode": "list,form", "domain": [("connector_id", "=", self.id)],
            "context": {"search_default_not_done": 1},
        }

    def action_open_credit_notes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("ATIV-Gutschriften"),
            "res_model": "plcs.ativ.credit.note", "view_mode": "list,form",
            "domain": [("connector_id", "=", self.id)],
        }

    def _notify(self, message, run):
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("ATIV-Schnittstelle"), "message": message, "type": "info",
                       "sticky": False, "next": {"type": "ir.actions.client", "tag": "soft_reload"}},
        }

    # ------------------------------------------------------------------
    # Planen
    # ------------------------------------------------------------------
    def _ativ_try_lock(self):
        try:
            self.lock_for_update(allow_referencing=True)
        except Exception as e:  # noqa: BLE001 – LockError
            raise UserError(self.env._("Ein anderer Lauf wird gerade geplant. Bitte kurz warten.")) from e

    def _ativ_plan(self, mode, trigger):
        """Arbeitsschritte für einen Lauf anlegen. Rückgabe: (Lauf, Meldung)."""
        self.ensure_one()
        if self.mode != "ativ":
            raise UserError(self.env._("Die Schnittstelle ist nicht auf ATIV-Gutschriften eingestellt."))
        self._ativ_try_lock()
        Run = self.env["plcs.ativ.run"].sudo()
        running = Run.search([("connector_id", "=", self.id), ("state", "=", "running")], limit=1)
        if running and mode != "full":
            self._ativ_trigger_worker()
            return running, self.env._("Ein Lauf ist schon in Arbeit (begonnen %s). Er wird fortgesetzt.",
                                       fields.Datetime.to_string(running.started))
        if mode == "incremental" and not self.ativ_cursor:
            mode = "full"
        client = self._ativ_client()
        start = fields.Datetime.now()
        try:
            if mode == "full":
                po_ids = self._ativ_search_ids(client, "purchase.order", self._ativ_base_domain())
            else:
                po_ids = self._ativ_changed_po_ids(client, self.ativ_cursor - OVERLAP)
        except AtivError as e:
            run = Run.create({
                "connector_id": self.id, "mode": mode, "trigger": trigger, "user_id": self.env.uid,
                "state": "failed", "finished": fields.Datetime.now(), "cursor_from": self.ativ_cursor,
                "message": self.env._("Planung fehlgeschlagen: %s", e),
            })
            self._ativ_commit()
            raise UserError(self.env._("ATIV-Abruf fehlgeschlagen: %s", e)) from e
        run = Run.create({
            "connector_id": self.id, "mode": mode, "trigger": trigger, "user_id": self.env.uid,
            "cursor_from": self.ativ_cursor if mode == "incremental" else False,
            "cursor_candidate": start, "count_planned": len(po_ids),
        })
        if mode == "full":
            known = self.env["plcs.ativ.credit.note"].sudo().search([("connector_id", "=", self.id)])
            present = set(po_ids)
            missing = known.filtered(lambda cn: cn.source_id not in present and not cn.source_missing)
            if missing:
                missing.write({"source_missing": True})
                run.count_missing = len(missing)
        self._ativ_enqueue(run, "po", "purchase.order", po_ids)
        if running:
            # Vollabgleich übernimmt die offenen Schritte des alten Laufs
            self.env["plcs.ativ.queue"].sudo().search([("run_id", "=", running.id), ("state", "=", "pending")]).write(
                {"run_id": run.id})
            running.write({"state": "partial", "finished": fields.Datetime.now(),
                           "message": self.env._("Durch Vollabgleich %s ersetzt.", run.id)})
        self._ativ_finalize_runs()
        self._ativ_trigger_worker()
        message = self.env._("%(n)s Gutschriften zum Abgleich vorgemerkt (%(m)s). Der Abgleich läuft im Hintergrund.",
                             n=len(po_ids), m=dict(Run._fields["mode"].selection)[mode])
        return run, message

    def _ativ_enqueue(self, run, kind, res_model, ids):
        Queue = self.env["plcs.ativ.queue"].sudo()
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return Queue
        pending = set()
        for chunk in _chunks(ids, 5000):
            pending.update(Queue.search([
                ("connector_id", "=", self.id), ("kind", "=", kind), ("res_model", "=", res_model),
                ("res_id", "in", chunk), ("state", "=", "pending")]).mapped("res_id"))
        return Queue.create([{
            "connector_id": self.id, "run_id": run.id if run else False, "kind": kind,
            "res_model": res_model, "res_id": i,
        } for i in ids if i not in pending])

    def _ativ_search_ids(self, client, model, domain, page=2000):
        ids, offset = [], 0
        while True:
            chunk = client.call(model, "search", domain=domain, offset=offset, limit=page, order="id")
            ids.extend(chunk)
            if len(chunk) < page:
                return ids
            offset += page

    def _ativ_changed_po_ids(self, client, since):
        """Gutschriften, an denen sich seit ``since`` etwas geändert hat – auch über Positionen,
        Tickets, Vorgänge, Kunden, Anhänge und Nachrichten."""
        s = fields.Datetime.to_string(since)
        changed = {m: set() for m in ("purchase.order", "helpdesk.ticket", "x_vorgang", "res.partner")}
        changed["purchase.order"].update(self._ativ_search_ids(client, "purchase.order", [("write_date", ">=", s)]))
        for row in client.call("purchase.order.line", "search_read", domain=[("write_date", ">=", s)],
                               fields=["order_id"]):
            changed["purchase.order"].add(_m2o_id(row["order_id"]))
        changed["helpdesk.ticket"].update(self._ativ_search_ids(client, "helpdesk.ticket", [("write_date", ">=", s)]))
        changed["x_vorgang"].update(self._ativ_search_ids(client, "x_vorgang", [("write_date", ">=", s)]))
        changed["res.partner"].update(self._ativ_search_ids(client, "res.partner", [("write_date", ">=", s)]))
        for row in client.call("ir.attachment", "search_read", domain=[
                ("res_model", "in", ["purchase.order", "helpdesk.ticket", "x_vorgang"]),
                "|", ("create_date", ">=", s), ("write_date", ">=", s)], fields=["res_model", "res_id"]):
            if row.get("res_id"):
                changed[row["res_model"]].add(row["res_id"])
        for row in client.call("mail.message", "search_read", domain=[
                ("model", "in", ["purchase.order", "helpdesk.ticket", "x_vorgang"]),
                "|", ("date", ">=", s), ("write_date", ">=", s)], fields=["model", "res_id"]):
            if row.get("res_id"):
                changed[row["model"]].add(row["res_id"])
        return self._ativ_resolve_po_ids(client, changed)

    def _ativ_resolve_po_ids(self, client, changed):
        """Geänderte ATIV-Datensätze → betroffene Gutschriften (nur innerhalb des Filters)."""
        po_ids = set(changed.get("purchase.order") or ())
        tickets = list(changed.get("helpdesk.ticket") or ())
        for chunk in _chunks(tickets, 1000):
            for row in client.call("purchase.order.line", "search_read", domain=[("x_studio_ticket", "in", chunk)],
                                   fields=["order_id"]):
                po_ids.add(_m2o_id(row["order_id"]))
            po_ids.update(client.call("purchase.order", "search", domain=[("x_studio_ticket_1", "in", chunk)]))
        for chunk in _chunks(changed.get("x_vorgang") or (), 1000):
            po_ids.update(client.call("purchase.order", "search", domain=[("x_studio_vorgang", "in", chunk)]))
        for chunk in _chunks(changed.get("res.partner") or (), 1000):
            po_ids.update(client.call("purchase.order", "search", domain=[("partner_id", "in", chunk)]))
        for chunk in _chunks(changed.get("purchase.order.line") or (), 1000):
            for row in client.call("purchase.order.line", "read", ids=chunk, fields=["order_id"]):
                po_ids.add(_m2o_id(row["order_id"]))
        po_ids.discard(False)
        result = []
        base = self._ativ_base_domain()
        for chunk in _chunks(sorted(po_ids), 1000):
            result.extend(client.call("purchase.order", "search", domain=base + [("id", "in", chunk)], order="id"))
        return result

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------
    @api.model
    def _ativ_webhook_connector(self, token):
        if not token:
            return self.browse()
        digest = hashlib.sha256(token.encode()).hexdigest()
        candidates = self.sudo().search([("mode", "=", "ativ"), ("webhook_enabled", "=", True), ("active", "=", True)])
        for connector in candidates:
            stored = connector.webhook_token_hash or ""
            if stored and secrets.compare_digest(stored, digest):
                return connector
        return self.browse()

    def _ativ_webhook_receive(self, payload):
        """Webhook aus ATIV: nur vormerken, Daten werden über die API gelesen."""
        self.ensure_one()
        model = payload.get("_model") or payload.get("model")
        rec_id = payload.get("_id") or payload.get("id")
        if model == "ir.attachment":
            model, rec_id = payload.get("res_model"), payload.get("res_id")
        if model not in SOURCE_MODELS or not isinstance(rec_id, int) or isinstance(rec_id, bool):
            return False
        self._ativ_enqueue(False, "ref", model, [rec_id])
        self._ativ_trigger_worker()
        return True

    # ------------------------------------------------------------------
    # Abarbeiten
    # ------------------------------------------------------------------
    def _ativ_trigger_worker(self):
        cron = self.env.ref("plcs_greenrx.cron_ativ_worker", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    def _ativ_commit(self, processed=0, remaining=None):
        """Zwischenstand speichern. Rückgabe: verbleibende Zeit des Cron-Laufs in Sekunden.

        ``remaining`` (offene Schritte) meldet dem Cron-Rahmen, dass noch Arbeit ansteht. Odoo ruft
        den Job dann im selben Lauf erneut auf, statt auf den nächsten Anstoß zu warten.
        """
        if odoo_module.current_test:
            return float("inf")
        return self.env["ir.cron"]._commit_progress(processed, remaining=remaining)

    def _ativ_pending_count(self):
        return self.env["plcs.ativ.queue"].sudo().search_count([
            ("connector_id", "=", self.id), ("state", "=", "pending"),
            "|", ("next_try", "=", False), ("next_try", "<=", fields.Datetime.now())])

    @api.model
    def _cron_ativ_schedule(self):
        """Zeitplan (alle 12 Stunden): Änderungen planen."""
        for connector in self.search([("mode", "=", "ativ"), ("sync_enabled", "=", True)]):
            try:
                connector._ativ_plan("incremental", "cron")
                connector._ativ_commit()
            except Exception as e:  # noqa: BLE001 – ein Fehler darf andere Schnittstellen nicht stoppen
                _logger.warning("ATIV-Planung %s fehlgeschlagen: %s", connector.name, e)
                if not odoo_module.current_test:
                    self.env.cr.rollback()

    @api.model
    def _cron_ativ_worker(self):
        """Hintergrund-Job: offene Schritte abarbeiten."""
        for connector in self.search([("mode", "=", "ativ")]):
            connector._ativ_work()

    def _ativ_claim(self, kind, limit):
        self.env["plcs.ativ.queue"].flush_model()
        now = fields.Datetime.now()
        rows = self.env.execute_query(SQL(
            """SELECT id FROM plcs_ativ_queue
               WHERE connector_id = %s AND kind = %s AND state = 'pending'
                 AND (next_try IS NULL OR next_try <= %s)
               ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""",
            self.id, kind, now, limit))
        return self.env["plcs.ativ.queue"].sudo().browse([r[0] for r in rows])

    def _ativ_work(self, max_batches=None):
        self.ensure_one()
        client = None
        batches = 0
        for kind in ("ref", "po", "document"):
            while max_batches is None or batches < max_batches:
                limit = max(1, self.batch_size or 25) if kind != "document" else 5
                items = self._ativ_claim(kind, limit)
                if not items:
                    break
                if client is None:
                    try:
                        client = self._ativ_client()
                    except UserError as e:
                        self._ativ_fail(items, str(e), transient=False)
                        self._ativ_commit()
                        return
                handler = getattr(self, "_ativ_process_%s" % kind)
                handler(client, items)
                batches += 1
                remaining = self._ativ_commit(len(items), remaining=self._ativ_pending_count())
                if remaining < 20:
                    # Zeitlimit des Cron-Laufs naht: Stand sichern und gleich weitermachen
                    self._ativ_finalize_runs()
                    self._ativ_commit()
                    self._ativ_trigger_worker()
                    return
        self._ativ_finalize_runs()
        self._ativ_commit()

    def _ativ_fail(self, items, error, transient=True):
        for item in items:
            attempts = item.attempts + 1
            if transient and attempts < (self.max_attempts or 5):
                delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
                item.write({"attempts": attempts, "error": error,
                            "next_try": fields.Datetime.now() + timedelta(minutes=delay)})
            else:
                item.write({"attempts": attempts, "error": error, "state": "failed"})

    def _ativ_done(self, items):
        items.write({"state": "done", "error": False, "done_at": fields.Datetime.now()})

    def _ativ_process_ref(self, client, items):
        changed = {}
        for item in items:
            changed.setdefault(item.res_model, set()).add(item.res_id)
        try:
            po_ids = self._ativ_resolve_po_ids(client, changed)
        except AtivError as e:
            self._ativ_fail(items, str(e), transient=e.transient)
            return
        self._ativ_enqueue(False, "po", "purchase.order", po_ids)
        self._ativ_done(items)

    def _ativ_process_po(self, client, items):
        try:
            payloads = self._ativ_fetch_payloads(client, items.mapped("res_id"))
        except AtivError as e:
            self._ativ_fail(items, str(e), transient=e.transient)
            return
        for item in items:
            payload = payloads.get(item.res_id)
            run = item.run_id
            if payload is None:
                # Nicht (mehr) vorhanden oder außerhalb des Filters → nichts löschen, nur kennzeichnen
                cn = self.env["plcs.ativ.credit.note"].sudo().search([
                    ("connector_id", "=", self.id), ("source_id", "=", item.res_id)], limit=1)
                if cn and not cn.source_missing:
                    cn.source_missing = True
                self._ativ_done(item)
                continue
            try:
                with self.env.cr.savepoint():
                    stats = self._ativ_apply_payload(payload)
            except Exception as e:  # noqa: BLE001 – Fehler je Gutschrift protokollieren, Rest weiter
                _logger.warning("ATIV-Gutschrift %s nicht übernommen: %s", item.res_id, e)
                self._ativ_fail(item, "%s: %s" % (type(e).__name__, e), transient=False)
                if run:
                    run.count_failed += 1
                continue
            self._ativ_done(item)
            if run:
                run.write({
                    "count_cn_created": run.count_cn_created + stats["cn_created"],
                    "count_cn_updated": run.count_cn_updated + stats["cn_updated"],
                    "count_unchanged": run.count_unchanged + stats["unchanged"],
                    "count_created": run.count_created + stats["grx_created"],
                    "count_updated": run.count_updated + stats["grx_updated"],
                })

    def _ativ_process_document(self, client, items):
        docs = items.mapped("document_id")
        try:
            self._ativ_fetch_documents(docs, client=client)
        except AtivError as e:
            self._ativ_fail(items, str(e), transient=e.transient)
            return
        ok = items.filtered(lambda q: q.document_id.state == "copied")
        self._ativ_done(ok)
        for item in items - ok:
            self._ativ_fail(item, item.document_id.error or "Datei nicht übernommen", transient=False)
        for run in ok.run_id:
            run.count_documents += len(ok.filtered(lambda q: q.run_id == run))

    def _ativ_finalize_runs(self):
        Queue = self.env["plcs.ativ.queue"].sudo()
        for run in self.env["plcs.ativ.run"].sudo().search([("connector_id", "=", self.id), ("state", "=", "running")]):
            if Queue.search_count([("run_id", "=", run.id), ("state", "=", "pending")]):
                continue
            failed = Queue.search_count([("run_id", "=", run.id), ("state", "=", "failed")])
            vals = {"finished": fields.Datetime.now(), "state": "partial" if failed else "done"}
            summary = self.env._(
                "%(cn)s Gutschriften neu, %(cu)s aktualisiert, %(un)s unverändert; Vorgänge: %(gc)s neu, "
                "%(gu)s aktualisiert; %(f)s Fehler; %(m)s bei ATIV nicht mehr gefunden.",
                cn=run.count_cn_created, cu=run.count_cn_updated, un=run.count_unchanged,
                gc=run.count_created, gu=run.count_updated, f=failed, m=run.count_missing)
            vals["message"] = summary
            run.write(vals)
            if not failed and run.cursor_candidate:
                connector = run.connector_id
                if not connector.ativ_cursor or connector.ativ_cursor < run.cursor_candidate:
                    connector.ativ_cursor = run.cursor_candidate
                connector.last_success_run_id = run
                connector.last_sync = run.finished
                connector.last_result = summary

    # ------------------------------------------------------------------
    # Lesen und Datenvertrag aufbauen
    # ------------------------------------------------------------------
    def _ativ_read(self, client, model, ids, extra=()):
        ids = [i for i in dict.fromkeys(ids) if i]
        if not ids:
            return {}
        spec = self._ativ_fields(client, model)
        result = {}
        for chunk in _chunks(ids, 200):
            for row in client.call(model, "read", ids=chunk, fields=spec["fields"] + list(extra)):
                result[row["id"]] = row
        return result

    def _ativ_stages(self, client):
        cache = client.__dict__.setdefault("_stage_cache", None)
        if cache is None:
            en = client.call("helpdesk.stage", "search_read", domain=[], fields=["name"], context={"lang": "en_US"})
            de = {r["id"]: r["name"] for r in client.call("helpdesk.stage", "search_read", domain=[], fields=["name"],
                                                          context={"lang": "de_DE"})}
            cache = {r["id"]: {"key": r["name"], "name": de.get(r["id"]) or r["name"]} for r in en}
            client._stage_cache = cache
        return cache

    def _ativ_selection_label(self, client, model, fname, value):
        info = self._ativ_fields(client, model)["info"].get(fname) or {}
        for key, label in info.get("selection") or []:
            if key == value:
                return label
        return value or False

    def _ativ_fetch_payloads(self, client, po_ids):
        """Gutschriften mit allem Zubehör lesen → {ATIV-ID: Datenvertrag}."""
        base = self._ativ_base_domain()
        allowed = set(client.call("purchase.order", "search", domain=base + [("id", "in", list(po_ids))]))
        pos = self._ativ_read(client, "purchase.order", [i for i in po_ids if i in allowed])
        line_ids = [lid for po in pos.values() for lid in po.get("order_line") or []]
        lines = self._ativ_read(client, "purchase.order.line", line_ids)
        ticket_ids = {_m2o_id(l.get("x_studio_ticket")) for l in lines.values()}
        ticket_ids |= {_m2o_id(po.get("x_studio_ticket_1")) for po in pos.values()}
        ticket_ids.discard(False)
        tickets = self._ativ_read(client, "helpdesk.ticket", ticket_ids)
        vorgang_ids = {_m2o_id(po.get("x_studio_vorgang")) for po in pos.values()} - {False}
        vorgaenge = self._ativ_read(client, "x_vorgang", vorgang_ids)
        partner_ids = {_m2o_id(po.get("partner_id")) for po in pos.values()}
        partner_ids |= {_m2o_id(t.get("x_studio_contact_address_name")) for t in tickets.values()}
        partner_ids.discard(False)
        partners = self._ativ_read(client, "res.partner", partner_ids)
        product_ids = {_m2o_id(l.get("product_id")) for l in lines.values()} - {False}
        products = self._ativ_read(client, "product.product", product_ids)
        stages = self._ativ_stages(client)
        taxes = {}
        tax_ids = {t for l in lines.values() for t in (l.get("tax_ids") or [])}
        if tax_ids:
            try:
                taxes = {r["id"]: r["name"] for r in client.call("account.tax", "read", ids=sorted(tax_ids),
                                                                 fields=["name"])}
            except AtivError:
                taxes = {}
        # Anhänge (nur Angaben, keine Dateiinhalte)
        att_spec = self._ativ_fields(client, "ir.attachment")
        attachments = []
        owners = [("helpdesk.ticket", sorted(ticket_ids)), ("purchase.order", sorted(pos)),
                  ("x_vorgang", sorted(vorgang_ids))]
        for res_model, ids in owners:
            for chunk in _chunks(ids, 500):
                attachments.extend(client.call("ir.attachment", "search_read", domain=[
                    ("res_model", "=", res_model), ("res_id", "in", chunk)], fields=att_spec["fields"], order="id"))
        # Verlauf der Tickets
        msg_spec = self._ativ_fields(client, "mail.message")
        messages = []
        for chunk in _chunks(sorted(ticket_ids), 200):
            messages.extend(client.call("mail.message", "search_read", domain=[
                ("model", "=", "helpdesk.ticket"), ("res_id", "in", chunk),
                ("message_type", "!=", "user_notification")], fields=msg_spec["fields"], order="id"))
        tracking = {}
        tv_ids = [tv for m in messages for tv in (m.get("tracking_value_ids") or [])]
        if tv_ids and self.tracking_readable:
            try:
                for chunk in _chunks(tv_ids, 500):
                    for tv in client.call("mail.tracking.value", "read", ids=chunk, fields=[
                            "field_id", "old_value_char", "new_value_char", "old_value_integer",
                            "new_value_integer", "old_value_float", "new_value_float", "old_value_datetime",
                            "new_value_datetime"]):
                        tracking[tv["id"]] = tv
            except AtivError as e:
                if e.status in (403, None):
                    self.sudo().tracking_readable = False
                else:
                    raise
        att_names = {a["id"]: a.get("name") for a in attachments}

        def address(p):
            if not p:
                return False
            parts = [p.get("street"), p.get("street2"), " ".join(x for x in (p.get("zip"), p.get("city")) if x),
                     _m2o_name(p.get("country_id"))]
            return "\n".join(x for x in parts if x) or False

        def partner_block(pid):
            p = partners.get(pid) or {}
            if not p:
                return None
            number = p.get("x_studio_vendor_number")
            return {
                "key": self._ativ_key("res.partner", pid), "id": pid,
                "name": p.get("display_name") or p.get("name"),
                "customer_number": str(number) if number not in (False, None, 0) else (p.get("ref") or False),
                "email": p.get("email") or False, "phone": p.get("phone") or False,
                "address": address(p),
                "city": p.get("city") or False,
            }

        def tv_text(tv):
            name = _m2o_name(tv.get("field_id")) or ""
            old = tv.get("old_value_char") or tv.get("old_value_datetime") or tv.get("old_value_integer") or \
                tv.get("old_value_float") or ""
            new = tv.get("new_value_char") or tv.get("new_value_datetime") or tv.get("new_value_integer") or \
                tv.get("new_value_float") or ""
            return "%s: %s → %s" % (name, old, new)

        msgs_by_ticket = {}
        for m in messages:
            kind = {"comment": "comment", "email": "comment", "notification": "notification",
                    "auto_comment": "notification"}.get(m.get("message_type"), "notification")
            subtype = _m2o_name(m.get("subtype_id")) or False
            if kind == "comment" and subtype and subtype.lower() in ("note", "notiz"):
                kind = "note"
            msgs_by_ticket.setdefault(m["res_id"], []).append({
                "key": self._ativ_key("mail.message", m["id"]),
                "date": m.get("date"), "author": _m2o_name(m.get("author_id")) or m.get("email_from") or False,
                "kind": kind, "subtype": subtype, "body": m.get("body") or False,
                "tracking": "\n".join(tv_text(tracking[t]) for t in m.get("tracking_value_ids") or [] if t in tracking)
                or False,
                "attachments": ", ".join(att_names.get(a) or str(a) for a in m.get("attachment_ids") or []) or False,
            })

        def ticket_block(tid):
            t = tickets.get(tid)
            if not t:
                return None
            stage = stages.get(_m2o_id(t.get("stage_id"))) or {"key": _m2o_name(t.get("stage_id")),
                                                                 "name": _m2o_name(t.get("stage_id"))}
            alt = partners.get(_m2o_id(t.get("x_studio_contact_address_name")))
            addr_parts = [t.get("x_studio_strae_hausnummer"), t.get("x_studio_strae2"),
                          " ".join(x for x in (t.get("x_studio_plz"), t.get("x_studio_stadt")) if x),
                          t.get("x_studio_land")]
            gdp = t.get("x_studio_confirmation_of_gdp_compliant_storage_1")
            return {
                "key": self._ativ_key("helpdesk.ticket", tid), "id": tid,
                "number": t.get("ticket_ref") or str(tid), "title": t.get("name") or False,
                "description": t.get("description") or False,
                "stage": stage, "complaint_number": t.get("x_studio_complaint_number") or False,
                "vorgang": _m2o_name(t.get("x_studio_vorgang")),
                "partner": {"name": t.get("partner_name") or _m2o_name(t.get("partner_id")),
                            "email": t.get("partner_email") or False, "phone": t.get("partner_phone") or False},
                "address": "\n".join(str(x).strip(" ,") for x in addr_parts if x) or False,
                "alt_pickup_address": (("%s\n%s" % (alt.get("display_name") or alt.get("name"), address(alt) or ""))
                                       .strip() if alt and not t.get("x_studio_abholadresse_kontaktadresse") else False),
                "product": {"name": _m2o_name(t.get("x_studio_product")), "pzn": t.get("x_studio_pzn") or False},
                "qty": _num(t.get("x_studio_product_number")), "price": _num(t.get("x_studio_product_price")),
                "batch": t.get("x_studio_batch_number") or t.get("name") or False,
                "expiry_date": t.get("x_studio_expiration_date") or False,
                "storage_conditions": t.get("x_studio_storage_conditions") or False,
                "return_reason": _m2o_name(t.get("x_studio_reason_for_product_return")),
                "gdp_confirmation": self._ativ_selection_label(client, "helpdesk.ticket",
                                                               "x_studio_confirmation_of_gdp_compliant_storage_1",
                                                               gdp) if gdp else False,
                "rejection_reason": _m2o_name(t.get("x_studio_reason_for_rejection")) or (
                    self._ativ_selection_label(client, "helpdesk.ticket", "x_studio_rejection_reason",
                                               t.get("x_studio_rejection_reason"))
                    if t.get("x_studio_rejection_reason") else False),
                "pickup_done": bool(t.get("x_studio_pick_up_done")),
                "assigned_user": _m2o_name(t.get("user_id")), "created_by": _m2o_name(t.get("create_uid")),
                "create_date": t.get("create_date") or False, "write_date": t.get("write_date") or False,
                "close_date": t.get("close_date") or False,
                "messages": msgs_by_ticket.get(tid, []),
            }

        result = {}
        for po_id, po in pos.items():
            vorgang_id = _m2o_id(po.get("x_studio_vorgang"))
            vg = vorgaenge.get(vorgang_id) or {}
            po_tickets = []
            lines_out = []
            for lid in po.get("order_line") or []:
                line = lines.get(lid)
                if not line or line.get("display_type"):
                    continue
                tid = _m2o_id(line.get("x_studio_ticket")) or _m2o_id(po.get("x_studio_ticket_1"))
                if tid and tid not in po_tickets:
                    po_tickets.append(tid)
                product = products.get(_m2o_id(line.get("product_id"))) or {}
                lines_out.append({
                    "key": self._ativ_key("purchase.order.line", lid), "id": lid,
                    "sequence": line.get("sequence") or 0,
                    "ticket_key": self._ativ_key("helpdesk.ticket", tid) if tid else False,
                    "product": {
                        "code": product.get("default_code") or False, "barcode": product.get("barcode") or False,
                        "name": product.get("name") or product.get("display_name") or _m2o_name(line.get("product_id")),
                        "storage_conditions": _m2o_name(product.get("x_studio_storage_conditions")),
                    },
                    "description": line.get("name") or False,
                    "qty": _num(line.get("product_qty")), "uom": _m2o_name(line.get("product_uom_id")),
                    "price_unit": _num(line.get("price_unit")),
                    "taxes": ", ".join(taxes.get(t) or str(t) for t in line.get("tax_ids") or []) or False,
                    "price_subtotal": _num(line.get("price_subtotal")), "price_tax": _num(line.get("price_tax")),
                    "price_total": _num(line.get("price_total")),
                    "qty_sellable": _num(line.get("x_studio_sellable_qty")),
                    "qty_quarantine": _num(line.get("x_studio_quarantine_qty")),
                    "qty_blocked": _num(line.get("x_studio_blocked_qty")),
                    "qty_destroyed": _num(line.get("x_studio_destroyed_qty")),
                    "quarantine_reason": line.get("x_studio_quarantne_grund") or False,
                })
            if _m2o_id(po.get("x_studio_ticket_1")) and _m2o_id(po["x_studio_ticket_1"]) not in po_tickets:
                po_tickets.append(_m2o_id(po["x_studio_ticket_1"]))
            ticket_blocks = [b for b in (ticket_block(t) for t in po_tickets) if b]
            docs = []
            for att in attachments:
                owner = (att.get("res_model"), att.get("res_id"))
                if owner == ("purchase.order", po_id) or owner == ("x_vorgang", vorgang_id) or (
                        owner[0] == "helpdesk.ticket" and owner[1] in po_tickets):
                    docs.append({
                        "key": self._ativ_key("ir.attachment", att["id"]), "id": att["id"],
                        "name": att.get("name"), "mimetype": att.get("mimetype") or False,
                        "size": att.get("file_size") or 0, "checksum": att.get("checksum") or False,
                        "owner": {"model": owner[0], "id": owner[1], "key": self._ativ_key(owner[0], owner[1])},
                        "create_date": att.get("create_date") or False,
                        "author": _m2o_name(att.get("create_uid")),
                        "type": att.get("type") or "binary",
                    })
            result[po_id] = {
                "contract": CONTRACT_VERSION,
                "source": {"system": self._ativ_source_code(), "model": "purchase.order", "id": po_id,
                           "key": self._ativ_key("purchase.order", po_id)},
                "credit_note": {
                    "name": po.get("name"), "state": po.get("state"),
                    "state_label": self._ativ_selection_label(client, "purchase.order", "state", po.get("state")),
                    "date_order": po.get("date_order") or False, "date_approve": po.get("date_approve") or False,
                    "date_planned": po.get("date_planned") or False,
                    "pickup_done": bool(po.get("x_studio_pick_up_done")),
                    "receipt_status": self._ativ_selection_label(client, "purchase.order", "receipt_status",
                                                                 po.get("receipt_status"))
                    if po.get("receipt_status") else False,
                    "ativ_receipt_done": bool(po.get("x_wareneingang_done")),
                    "complaint_number": po.get("x_studio_complaint_number") or vg.get("x_complaint_number") or False,
                    "quarantine_reason": po.get("x_studio_quarantne_grund") or False,
                    "amount_untaxed": _num(po.get("amount_untaxed")), "amount_tax": _num(po.get("amount_tax")),
                    "amount_total": _num(po.get("amount_total")), "currency": _m2o_name(po.get("currency_id")),
                    "created_by": _m2o_name(po.get("create_uid")), "buyer": _m2o_name(po.get("user_id")),
                    "create_date": po.get("create_date") or False, "write_date": po.get("write_date") or False,
                },
                "vorgang": {
                    "key": self._ativ_key("x_vorgang", vorgang_id), "id": vorgang_id, "name": vg.get("x_name"),
                    "complaint_number": vg.get("x_complaint_number") or False,
                    "source": vg.get("x_source") or False, "create_date": vg.get("create_date") or False,
                } if vg else None,
                "customer": partner_block(_m2o_id(po.get("partner_id"))),
                "lines": lines_out,
                "tickets": ticket_blocks,
                "documents": docs,
            }
        return result

    # ------------------------------------------------------------------
    # Datenvertrag einspielen
    # ------------------------------------------------------------------
    def _ativ_category_for_stage(self, stage_key):
        key = (stage_key or "").strip().lower()
        if key in _names(self.stages_expected):
            return "expected"
        if key in _names(self.stages_cancel):
            return "cancel"
        if key in _names(self.stages_closed):
            return "closed"
        if key in _names(self.stages_announced):
            return "announced"
        return "unknown"

    def _ativ_cn_category(self, payload):
        cn = payload["credit_note"]
        stage_names = ", ".join(sorted({t["stage"]["name"] for t in payload["tickets"] if t["stage"].get("name")}))
        if cn.get("ativ_receipt_done"):
            return "closed", self.env._("Wareneingang bei ATIV abgeschlossen")
        cats = [self._ativ_category_for_stage(t["stage"].get("key")) for t in payload["tickets"]]
        known = [c for c in cats if c != "unknown"]
        if "expected" in cats or not known:
            category = "expected" if "expected" in cats else "unknown"
            if self.expected_since and cn.get("date_order") and \
                    fields.Datetime.to_datetime(cn["date_order"]).date() < self.expected_since:
                return "closed", self.env._("Gutschrift vor dem Stichtag %s", self.expected_since.strftime("%d.%m.%Y"))
            return category, self.env._("Ticketstand: %s", stage_names or "-")
        if all(c == "cancel" for c in known):
            return "cancel", self.env._("Ticketstand: %s", stage_names)
        if "closed" in known:
            return "closed", self.env._("Ticketstand: %s", stage_names)
        return "announced", self.env._("Ticketstand: %s", stage_names)

    @staticmethod
    def _ativ_hash(payload):
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _ativ_apply_payload(self, payload):
        """Einen Datenvertrag einspielen. Rückgabe: Zähler."""
        self.ensure_one()
        if payload.get("contract") != CONTRACT_VERSION:
            raise UserError(self.env._("Unbekannter Datenvertrag: %s", payload.get("contract")))
        stats = {"cn_created": 0, "cn_updated": 0, "unchanged": 0, "grx_created": 0, "grx_updated": 0}
        CN = self.env["plcs.ativ.credit.note"].sudo()
        key = payload["source"]["key"]
        digest = self._ativ_hash(payload)
        cn = CN.search([("connector_id", "=", self.id), ("source_key", "=", key)], limit=1)
        now = fields.Datetime.now()
        category, reason = self._ativ_cn_category(payload)
        if cn and cn.payload_hash == digest and cn.grx_ticket_id and \
                (cn.status_category, cn.status_reason) == (category, reason) and not cn.source_missing:
            cn.last_sync = now
            stats["unchanged"] += 1
            return stats
        head = payload["credit_note"]
        customer = payload.get("customer") or {}
        vorgang = payload.get("vorgang") or {}
        tickets = self._ativ_upsert_tickets(payload["tickets"])
        pickup = False
        for t in payload["tickets"]:
            pickup = t.get("alt_pickup_address") or pickup
        vals = {
            "name": head.get("name"), "source_id": payload["source"]["id"],
            "vorgang_name": vorgang.get("name") or False, "vorgang_key": vorgang.get("key") or False,
            "vorgang_source": vorgang.get("source") or False,
            "complaint_number": head.get("complaint_number") or vorgang.get("complaint_number") or False,
            "partner_name": customer.get("name") or False, "customer_number": customer.get("customer_number") or False,
            "partner_email": customer.get("email") or False, "partner_phone": customer.get("phone") or False,
            "partner_address": customer.get("address") or False,
            "partner_city": customer.get("city") or False,
            "pickup_address": pickup or customer.get("address") or False,
            "pickup_date": head.get("date_planned") or False, "pickup_done": head.get("pickup_done"),
            "source_state": head.get("state"), "source_state_label": head.get("state_label"),
            "date_order": head.get("date_order") or False, "date_approve": head.get("date_approve") or False,
            "receipt_status": head.get("receipt_status") or False, "ativ_receipt_done": head.get("ativ_receipt_done"),
            "quarantine_reason": head.get("quarantine_reason") or False,
            "amount_untaxed": head.get("amount_untaxed"), "amount_tax": head.get("amount_tax"),
            "amount_total": head.get("amount_total"), "currency_name": head.get("currency") or False,
            "created_by": head.get("created_by") or False, "buyer": head.get("buyer") or False,
            "source_create_date": head.get("create_date") or False,
            "source_write_date": head.get("write_date") or False,
            "status_category": category, "status_reason": reason, "source_missing": False,
            "contract_version": payload["contract"], "payload_hash": digest,
            "payload_json": json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
            "last_sync": now, "ativ_ticket_ids": [(6, 0, list(tickets.values()))],
        }
        if cn:
            cn.write(vals)
            stats["cn_updated"] += 1
        else:
            vals.update({"connector_id": self.id, "source_key": key})
            cn = CN.create(vals)
            stats["cn_created"] += 1
        self._ativ_upsert_lines(cn, payload, tickets)
        grx, created = self._ativ_grx_ticket(cn, payload)
        if cn.grx_ticket_id != grx:
            cn.grx_ticket_id = grx
        self._ativ_upsert_documents(cn, payload, tickets, grx)
        changed = grx._ativ_refresh_from_mirror()
        if created:
            stats["grx_created"] += 1
        elif changed:
            stats["grx_updated"] += 1
        return stats

    def _ativ_upsert_tickets(self, ticket_payloads):
        Ticket = self.env["plcs.ativ.ticket"].sudo()
        Message = self.env["plcs.ativ.message"].sudo()
        result = {}
        for tp in ticket_payloads:
            category = self._ativ_category_for_stage(tp["stage"].get("key"))
            vals = {
                "source_id": tp["id"], "ticket_number": tp.get("number"), "title": tp.get("title"),
                "description": tp.get("description"), "stage_name": tp["stage"].get("name"),
                "stage_key": tp["stage"].get("key"), "stage_category": category,
                "vorgang_name": tp.get("vorgang") or False, "complaint_number": tp.get("complaint_number"),
                "partner_name": tp["partner"].get("name") or False, "partner_email": tp["partner"].get("email"),
                "partner_phone": tp["partner"].get("phone"), "address": tp.get("address"),
                "alt_pickup_address": tp.get("alt_pickup_address"),
                "product_name": tp["product"].get("name") or False, "pzn": tp["product"].get("pzn"),
                "qty": tp.get("qty"), "price": tp.get("price"), "batch": tp.get("batch"),
                "expiry_date": tp.get("expiry_date") or False, "storage_conditions": tp.get("storage_conditions"),
                "return_reason": tp.get("return_reason"), "gdp_confirmation": tp.get("gdp_confirmation"),
                "rejection_reason": tp.get("rejection_reason"), "pickup_done": tp.get("pickup_done"),
                "assigned_user": tp.get("assigned_user"), "created_by": tp.get("created_by"),
                "source_create_date": tp.get("create_date") or False,
                "source_write_date": tp.get("write_date") or False, "close_date": tp.get("close_date") or False,
            }
            ticket = Ticket.search([("connector_id", "=", self.id), ("source_key", "=", tp["key"])], limit=1)
            if ticket:
                observed_key = "%s:observed:%s>%s@%s" % (tp["key"], ticket.stage_key, vals["stage_key"],
                                                          tp.get("write_date") or "")
                if ticket.stage_key and ticket.stage_key != vals["stage_key"] and not Message.search_count([
                        ("connector_id", "=", self.id), ("source_key", "=", observed_key)]):
                    Message.create({
                        "connector_id": self.id, "ativ_ticket_id": ticket.id, "kind": "observed",
                        "source_key": observed_key,
                        "date": tp.get("write_date") or fields.Datetime.now(), "author": "PLCS-Abruf",
                        "tracking": "%s → %s" % (ticket.stage_name, vals["stage_name"]),
                    })
                ticket.write(vals)
            else:
                vals.update({"connector_id": self.id, "source_key": tp["key"]})
                ticket = Ticket.create(vals)
            existing = {m.source_key: m for m in Message.search([("ativ_ticket_id", "=", ticket.id),
                                                                ("kind", "!=", "observed")])}
            to_create = []
            for mp in tp.get("messages") or []:
                mvals = {"date": mp.get("date"), "author": mp.get("author"), "kind": mp.get("kind"),
                         "subtype": mp.get("subtype"), "body": mp.get("body"), "tracking": mp.get("tracking"),
                         "attachment_names": mp.get("attachments")}
                msg = existing.get(mp["key"])
                if msg:
                    if any((msg[k] or False) != (v or False) for k, v in mvals.items() if k not in ("date", "body")):
                        msg.write(mvals)
                else:
                    mvals.update({"connector_id": self.id, "ativ_ticket_id": ticket.id, "source_key": mp["key"]})
                    to_create.append(mvals)
            if to_create:
                Message.create(to_create)
            result[tp["key"]] = ticket.id
        return result

    def _ativ_upsert_lines(self, cn, payload, tickets):
        Line = self.env["plcs.ativ.credit.note.line"].sudo()
        Ticket = self.env["plcs.ativ.ticket"].sudo()
        existing = {l.source_key: l for l in cn.line_ids}
        seen = set()
        for lp in payload["lines"]:
            ticket = Ticket.browse(tickets.get(lp.get("ticket_key"))) if lp.get("ticket_key") in tickets else Ticket
            vals = {
                "source_id": lp["id"], "sequence": lp.get("sequence") or 0, "ativ_ticket_id": ticket.id or False,
                "ticket_number": ticket.ticket_number or False,
                "product_code": lp["product"].get("code"), "product_barcode": lp["product"].get("barcode"),
                "product_name": lp["product"].get("name"), "description": lp.get("description"),
                "qty": lp.get("qty"), "uom": lp.get("uom"), "price_unit": lp.get("price_unit"),
                "taxes": lp.get("taxes"), "price_subtotal": lp.get("price_subtotal"),
                "price_tax": lp.get("price_tax"), "price_total": lp.get("price_total"),
                "batch": ticket.batch or False, "expiry_date": ticket.expiry_date or False,
                "storage_conditions": lp["product"].get("storage_conditions") or ticket.storage_conditions or False,
                "condition_note": lp.get("quarantine_reason") or False,
                "qty_sellable": lp.get("qty_sellable"), "qty_quarantine": lp.get("qty_quarantine"),
                "qty_blocked": lp.get("qty_blocked"), "qty_destroyed": lp.get("qty_destroyed"), "removed": False,
            }
            line = existing.get(lp["key"])
            if line:
                line.write(vals)
            else:
                vals.update({"credit_note_id": cn.id, "source_key": lp["key"]})
                Line.create(vals)
            seen.add(lp["key"])
        for key, line in existing.items():
            if key not in seen and not line.removed:
                line.removed = True

    def _ativ_grx_ticket(self, cn, payload):
        """PLCS-Vorgang zum ATIV-Vorgang finden oder anlegen (Rückgabe: Vorgang, neu angelegt)."""
        Grx = self.env["plcs.greenrx.ticket"].sudo().with_company(self.company_id)
        vorgang = payload.get("vorgang") or {}
        key = vorgang.get("key") or payload["source"]["key"]
        if vorgang:
            name = (vorgang.get("name") or "").strip()
            if not name:
                raise UserError(self.env._("Vorgang %(v)s der Gutschrift %(g)s hat bei ATIV keine Vorgangsnummer.",
                                           v=vorgang.get("id"), g=payload["credit_note"].get("name")))
        else:
            # Gutschrift ohne Vorgang: die Gutschriftnummer dient als Vorgang (eigener Schlüssel)
            name = (payload["credit_note"].get("name") or "").strip()
        if not name:
            raise UserError(self.env._("Gutschrift %s ohne Vorgangs- und Gutschriftnummer.", payload["source"]["id"]))
        grx = Grx.search([("external_id", "=", key), ("company_id", "=", self.company_id.id)], limit=1)
        if not grx:
            grx = Grx.search([("name", "=", name), ("company_id", "=", self.company_id.id)], limit=1)
            if grx and grx.external_id and grx.external_id != key:
                raise UserError(self.env._(
                    "Vorgang %(n)s ist in PLCS schon mit einer anderen Quelle verknüpft (%(k)s).",
                    n=name, k=grx.external_id))
            if grx:
                grx.write({"external_id": key})
                return grx, False
        if grx:
            return grx, False
        grx = Grx.with_context(mail_create_nolog=True, mail_create_nosubscribe=True,
                               tracking_disable=True).create({
            "name": name, "source": "api", "external_id": key, "company_id": self.company_id.id,
            "ativ_connector_id": self.id,
        })
        return grx, True

    def _ativ_upsert_documents(self, cn, payload, tickets, grx):
        Doc = self.env["plcs.ativ.document"].sudo()
        Ticket = self.env["plcs.ativ.ticket"].sudo()
        expected = cn.status_category in ("expected", "unknown")
        copy = self.doc_copy == "all" or (self.doc_copy == "expected" and expected)
        for dp in payload["documents"]:
            owner = dp["owner"]
            vals = {
                "source_id": dp["id"], "name": dp.get("name"), "mimetype": dp.get("mimetype"),
                "file_size": dp.get("size") or 0, "checksum": dp.get("checksum"),
                "source_model": owner["model"], "source_res_id": owner["id"],
                "source_create_date": dp.get("create_date") or False, "author": dp.get("author"),
                "ativ_ticket_id": tickets.get(owner["key"]) or Ticket.search([
                    ("connector_id", "=", self.id), ("source_key", "=", owner["key"])], limit=1).id or False,
                "credit_note_id": cn.id if owner["model"] == "purchase.order" else False,
                "grx_ticket_id": grx.id,
            }
            doc = Doc.search([("connector_id", "=", self.id), ("source_key", "=", dp["key"])], limit=1)
            if doc:
                if doc.credit_note_id and not vals["credit_note_id"]:
                    vals.pop("credit_note_id")
                changed_file = doc.checksum != vals["checksum"]
                doc.write(vals)
                if changed_file and doc.state == "copied":
                    doc.state = "meta"
            else:
                vals.update({"connector_id": self.id, "source_key": dp["key"]})
                doc = Doc.create(vals)
            if copy and dp.get("type", "binary") == "binary" and doc.state in ("meta", "error"):
                doc.write({"state": "queued", "error": False})
                self.env["plcs.ativ.queue"].sudo().create({
                    "connector_id": self.id, "kind": "document", "res_model": "ir.attachment",
                    "res_id": dp["id"], "document_id": doc.id,
                })

    def _ativ_fetch_documents(self, docs, client=None):
        """Dateiinhalte von ATIV lesen und als Anhang am PLCS-Vorgang speichern."""
        self.ensure_one()
        if not docs:
            return
        client = client or self._ativ_client()
        Attachment = self.env["ir.attachment"].sudo()
        for chunk in _chunks(docs, 5):
            rows = {r["id"]: r for r in client.call("ir.attachment", "read", ids=[d.source_id for d in chunk],
                                                    fields=["datas", "checksum", "name", "mimetype"])}
            for doc in chunk:
                row = rows.get(doc.source_id)
                content = binary_content(row.get("datas")) if row else False
                if not content:
                    doc.write({"state": "error", "error": self.env._("Datei bei ATIV nicht lesbar.")})
                    continue
                raw = base64.b64decode(content)
                if row.get("checksum") and hashlib.sha1(raw).hexdigest() != row["checksum"]:
                    doc.write({"state": "error", "error": self.env._("Prüfsumme stimmt nicht.")})
                    continue
                res_model, res_id = ("plcs.greenrx.ticket", doc.grx_ticket_id.id) if doc.grx_ticket_id else \
                    ("plcs.ativ.document", doc.id)
                old = doc.attachment_id
                if old and old.checksum == hashlib.sha1(raw).hexdigest():
                    doc.write({"state": "copied", "error": False})
                    continue
                if old:
                    old.name = "%s (alte Fassung)" % old.name
                attachment = Attachment.create({
                    "name": row.get("name") or doc.name, "raw": raw, "mimetype": row.get("mimetype") or doc.mimetype,
                    "res_model": res_model, "res_id": res_id,
                    "description": self.env._("Aus ATIV übernommen (%s)", doc.source_key),
                })
                doc.write({"attachment_id": attachment.id, "state": "copied", "error": False,
                           "checksum": row.get("checksum") or doc.checksum})
