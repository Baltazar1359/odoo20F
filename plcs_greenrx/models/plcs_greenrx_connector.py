import ast
import logging
import xmlrpc.client
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PlcsGreenrxConnector(models.Model):
    """Abruf der Tickets aus dem GreenRX-ERP (Odoo von ATIV) über die externe API.

    Die Feldzuordnung ist einstellbar, weil sich das Datenmodell im GreenRX-ERP
    ändern kann (z. B. Sammelretouren mit mehreren Positionen).
    """

    _name = "plcs.greenrx.connector"
    _description = "GreenRX-ERP: Schnittstelle"

    name = fields.Char("Bezeichnung", required=True, default="GreenRX-ERP")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    url = fields.Char("URL", help="z. B. https://greenrx.example.de")
    db = fields.Char("Datenbank")
    login = fields.Char("Benutzer")
    api_key = fields.Char("API-Schlüssel", groups="stock.group_stock_manager", copy=False)
    sync_enabled = fields.Boolean("Automatisch abrufen", default=False,
                                  help="Alle 15 Minuten neue und geänderte Tickets abrufen.")
    last_sync = fields.Datetime("Letzter Abruf", readonly=True)
    last_result = fields.Text("Ergebnis letzter Abruf", readonly=True)

    remote_model = fields.Char("Ticket-Modell", default="helpdesk.ticket", required=True)
    remote_domain = fields.Char("Filter (Domain)", default="[]",
                                help="Welche Tickets abgerufen werden, z. B. [('stage_id.name', '=', 'Genehmigt')]")
    f_name = fields.Char("Feld Ticketnummer", default="ticket_ref", required=True)
    f_reference = fields.Char("Feld Referenznummer (Vorgang)", default="",
                              help="Tickets mit gleicher Referenznummer werden zu einem Vorgang (Paket) zusammengefasst. "
                                   "Leer = jedes Ticket ist ein eigener Vorgang.")
    f_complaint = fields.Char("Feld Reklamationsnummer", default="")
    f_batch = fields.Char("Feld Charge (Ticket)", default="x_studio_batch_number")
    f_credit_note = fields.Char("Feld Gutschriftnummer", default="x_studio_credit_note")
    f_partner = fields.Char("Feld Absender", default="partner_id")
    f_customer_number = fields.Char("Feld Kundennummer", default="",
                                    help="Leer = Referenz (ref) des Absender-Kontakts im GreenRX-ERP")
    f_product = fields.Char("Feld Artikel (Ticket)", default="x_studio_product",
                            help="Artikel direkt am Ticket (ein Artikel je Ticket)")
    f_qty = fields.Char("Feld Packungsanzahl (Ticket)", default="x_studio_product_number")
    line_model = fields.Char("Positions-Modell", help="Nur bei Tickets mit mehreren Positionen (Sammelretoure)")
    line_link_field = fields.Char("Positions-Feld zum Ticket")
    line_product_field = fields.Char("Positions-Feld Artikel")
    line_qty_field = fields.Char("Positions-Feld Packungen")
    product_code_fields = fields.Char("Artikel-Codefelder im GreenRX-ERP", default="barcode,default_code",
                                      help="Aus diesen Feldern des Artikels werden GTIN bzw. PZN gelesen.")

    # ------------------------------------------------------------------
    def _proxy(self, endpoint):
        return xmlrpc.client.ServerProxy("%s/xmlrpc/2/%s" % (self.url.rstrip("/"), endpoint), allow_none=True)

    def _connect(self):
        self.ensure_one()
        if not (self.url and self.db and self.login and self.sudo().api_key):
            raise UserError(self.env._("Schnittstelle %s: URL, Datenbank, Benutzer und API-Schlüssel ausfüllen.", self.name))
        uid = self._proxy("common").authenticate(self.db, self.login, self.sudo().api_key, {})
        if not uid:
            raise UserError(self.env._("Anmeldung am GreenRX-ERP fehlgeschlagen. Benutzer/API-Schlüssel prüfen."))
        models_proxy = self._proxy("object")

        def call(model, method, args, kw=None):
            return models_proxy.execute_kw(self.db, uid, self.sudo().api_key, model, method, args, kw or {})
        return call

    def action_test(self):
        self.ensure_one()
        call = self._connect()
        count = call(self.remote_model, "search_count", [self._domain()])
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("Verbindung in Ordnung"),
                       "message": self.env._("%s Tickets entsprechen dem Filter.", count),
                       "type": "success", "sticky": False},
        }

    def _domain(self, incremental=False):
        try:
            domain = ast.literal_eval(self.remote_domain or "[]")
        except (ValueError, SyntaxError) as e:
            raise UserError(self.env._("Filter (Domain) ist ungültig: %s", e)) from e
        if incremental and self.last_sync:
            since = fields.Datetime.to_string(self.last_sync - timedelta(minutes=30))
            domain = domain + [("write_date", ">=", since)]
        return domain

    @staticmethod
    def _m2o_name(value):
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return value[1]
        return value or False

    @staticmethod
    def _m2o_id(value):
        if isinstance(value, (list, tuple)) and value:
            return value[0]
        if isinstance(value, int):
            return value
        return False

    def _codes_for_products(self, call, product_ids):
        code_fields = [f.strip() for f in (self.product_code_fields or "").split(",") if f.strip()]
        if not product_ids or not code_fields:
            return {}
        rows = call("product.product", "read", [list(product_ids)], {"fields": code_fields + ["display_name"]})
        result = {}
        for row in rows:
            gtin = pzn = False
            for fname in code_fields:
                value = (row.get(fname) or "").strip() if isinstance(row.get(fname), str) else ""
                if not value or not value.isdigit():
                    continue
                if len(value) in (13, 14) and not gtin:
                    gtin = value.zfill(14)
                elif len(value) in (7, 8) and not pzn:
                    pzn = value.zfill(8)
            result[row["id"]] = {"gtin": gtin, "pzn": pzn, "name": row.get("display_name")}
        return result

    def action_sync(self, incremental=False):
        self.ensure_one()
        call = self._connect()
        fnames = [f for f in (self.f_name, self.f_reference, self.f_complaint, self.f_credit_note, self.f_partner,
                              self.f_customer_number, self.f_product, self.f_qty, self.f_batch) if f]
        rows = call(self.remote_model, "search_read", [self._domain(incremental)],
                    {"fields": sorted(set(fnames)), "limit": 2000})
        # Positionen (Sammelretoure mit mehreren Produkten je Ticket)
        lines_by_ticket = {}
        if self.line_model and self.line_link_field and self.line_product_field:
            line_rows = call(self.line_model, "search_read",
                             [[(self.line_link_field, "in", [r["id"] for r in rows])]],
                             {"fields": [self.line_link_field, self.line_product_field, self.line_qty_field or "id"]})
            for lr in line_rows:
                lines_by_ticket.setdefault(self._m2o_id(lr[self.line_link_field]), []).append(lr)
        product_ids = set()
        for r in rows:
            if self.f_product and self._m2o_id(r.get(self.f_product)):
                product_ids.add(self._m2o_id(r[self.f_product]))
        for lrs in lines_by_ticket.values():
            for lr in lrs:
                if self._m2o_id(lr.get(self.line_product_field)):
                    product_ids.add(self._m2o_id(lr[self.line_product_field]))
        codes = self._codes_for_products(call, product_ids)
        partner_refs = {}
        if self.f_partner and not self.f_customer_number:
            partner_ids = {self._m2o_id(r.get(self.f_partner)) for r in rows} - {False}
            if partner_ids:
                for p in call("res.partner", "read", [list(partner_ids)], {"fields": ["ref"]}):
                    partner_refs[p["id"]] = p.get("ref") or False

        def text(row, fname):
            return (self._m2o_name(row.get(fname)) or False) if fname else False

        def line_vals(row, product_value, qty):
            pid = self._m2o_id(product_value)
            info = codes.get(pid, {})
            return {"ticket_number": text(row, self.f_name) or str(row["id"]),
                    "gtin": info.get("gtin"), "pzn": info.get("pzn"),
                    "product_name": info.get("name") or self._m2o_name(product_value),
                    "batch": text(row, self.f_batch), "credit_note": text(row, self.f_credit_note),
                    "qty": qty or 0}

        vorgaenge = {}
        for r in rows:
            reference = text(r, self.f_reference) or text(r, self.f_name) or str(r["id"])
            item = vorgaenge.get(reference)
            if not item:
                item = vorgaenge[reference] = {
                    "external_id": reference,
                    "name": reference,
                    "partner_name": text(r, self.f_partner),
                    "lines": [],
                }
                if self.f_complaint:
                    item["complaint_number"] = text(r, self.f_complaint)
                if self.f_customer_number:
                    item["customer_number"] = text(r, self.f_customer_number)
                elif self.f_partner:
                    item["customer_number"] = partner_refs.get(self._m2o_id(r.get(self.f_partner)))
            if r["id"] in lines_by_ticket:
                for lr in lines_by_ticket[r["id"]]:
                    item["lines"].append(line_vals(r, lr.get(self.line_product_field),
                                                   lr.get(self.line_qty_field) if self.line_qty_field else 1))
            elif self.f_product and r.get(self.f_product):
                item["lines"].append(line_vals(r, r.get(self.f_product), r.get(self.f_qty) if self.f_qty else 1))
        stats = self.env["plcs.greenrx.ticket"].with_company(self.company_id).api_upsert(
            list(vorgaenge.values()), source="api")
        summary = self.env._("%(c)s neu, %(u)s aktualisiert, %(s)s übersprungen, %(e)s Fehler",
                             c=stats["created"], u=stats["updated"], s=stats["skipped"], e=len(stats["errors"]))
        if stats["errors"]:
            summary += "\n" + "\n".join(stats["errors"][:50])
        self.write({"last_sync": fields.Datetime.now(), "last_result": summary})
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": self.env._("Vorgänge abgerufen"), "message": summary.split("\n")[0],
                       "type": "warning" if stats["errors"] else "success", "sticky": False},
        }

    @api.model
    def _cron_sync(self):
        for connector in self.search([("sync_enabled", "=", True)]):
            try:
                with self.env.cr.savepoint():
                    connector.action_sync(incremental=True)
            except Exception as e:  # noqa: BLE001 – Abruf darf den Cron nicht abbrechen
                _logger.exception("GreenRX-Abruf %s fehlgeschlagen", connector.name)
                connector.last_result = self.env._("Fehler: %s", e)
