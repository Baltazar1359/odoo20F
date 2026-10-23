"""Artikelstamm ATIV → PLCS (1.10.0).

Übernimmt alle Artikel aus dem GreenRX-ERP von ATIV (Filter einstellbar): Bezeichnung, PZN, GTIN,
Lagerbedingungen und Produktbild.

Regel „nur leere Felder füllen“
-------------------------------
* Neuer Artikel (PZN in PLCS unbekannt): wird mit allen ATIV-Werten angelegt.
* Vorhandener Artikel: PLCS-Werte bleiben. Gefüllt werden nur leere Felder (PZN, interne Referenz,
  Barcode, Produktbild). Bezeichnung und Lagertemperatur werden nie überschrieben.
* Die ATIV-Werte stehen am Artikel daneben (Bezeichnung/Lagerbedingungen „ATIV“). Weicht PLCS ab,
  steht das in „Abweichung zu ATIV“ und einmalig als Notiz im Verlauf des Artikels.

Ablauf wie bei den Gutschriften: planen (12-Stunden-Zeitplan, Knopf, Webhook) → Arbeitsschritte
„Artikel abgleichen“ → Fortschrittsstand erst nach fehlerfreiem Lauf. Ein Webhook auf
``product.template`` oder ``product.product`` stößt nur den Abruf an; gelesen wird über die API.
"""
import ast
import logging
import re

from odoo import fields, models
from odoo.exceptions import UserError

from ..tools import gs1
from ..tools.ativ_client import AtivError
from .plcs_ativ_sync import OVERLAP, PRODUCT_RUN_MODES, _chunks, _m2o_name, _text
from .product import storage_is_cool

_logger = logging.getLogger(__name__)

DEFAULT_PRODUCT_DOMAIN = "[('default_code', '!=', False)]"
IMAGE_CHUNK = 10
TEMP_LABELS = {"cool": "kühl (2–8 °C)", "ambient": "ambient (15–25 °C)"}


def _norm_pzn(code):
    code = str(code or "").strip()
    code = re.sub(r"^PZN[\s:\-]*", "", code, flags=re.IGNORECASE)
    code = re.sub(r"[\s\-]", "", code)
    if code.isdigit() and len(code) <= 8:
        return code.zfill(8)
    return code or False


def _norm_gtin(barcode, pzn):
    """ATIV-Barcode → GTIN-14 oder False. Rückgabe: (gtin, Hinweis)."""
    raw = re.sub(r"\s", "", str(barcode or ""))
    if not raw:
        return False, False
    if not raw.isdigit() or len(raw) not in (8, 12, 13, 14):
        return False, "Barcode %s aus ATIV ist keine GTIN" % raw
    gtin = raw.zfill(14)
    if not gs1.gtin_check_digit_ok(gtin):
        return False, "Barcode %s aus ATIV hat eine falsche Prüfziffer" % raw
    ntin_pzn = gs1.gtin_to_pzn(gtin)
    if ntin_pzn and pzn and ntin_pzn != pzn:
        return False, "Barcode %s aus ATIV passt nicht zur PZN %s" % (raw, pzn)
    return gtin, False


def _norm_text(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


class PlcsGreenrxConnector(models.Model):
    _inherit = "plcs.greenrx.connector"

    ativ_product_sync = fields.Boolean(
        "Artikelstamm aus ATIV übernehmen", default=False,
        help="Alle ATIV-Artikel des Filters mit Bezeichnung, PZN, GTIN, Lagerbedingungen und Produktbild "
             "übernehmen (alle 12 Stunden, per Knopf oder Webhook). Vorhandene PLCS-Artikel: nur leere "
             "Felder werden gefüllt.")
    ativ_product_domain = fields.Char(
        "Filter Artikel", default=DEFAULT_PRODUCT_DOMAIN,
        help="Odoo-Filter auf product.product bei ATIV. Artikel ohne gültige PZN werden ohnehin übersprungen.")
    ativ_product_cursor = fields.Datetime(
        "Fortschrittsstand Artikel", readonly=True, copy=False,
        help="Artikeländerungen bis zu diesem Zeitpunkt sind vollständig übernommen.")
    ativ_product_run_id = fields.Many2one("plcs.ativ.run", "Letzter erfolgreicher Artikel-Lauf", readonly=True,
                                          copy=False)
    ativ_product_count = fields.Integer("Artikel aus ATIV", compute="_compute_ativ_product_count")

    def _compute_ativ_product_count(self):
        Template = self.env["product.template"].with_context(active_test=False)
        for rec in self:
            rec.ativ_product_count = Template.search_count(
                [("plcs_ativ_key", "=like", "%s:%%" % rec._ativ_source_code())]) if rec.id else 0

    # ------------------------------------------------------------------
    # Knöpfe
    # ------------------------------------------------------------------
    def action_ativ_products_sync(self):
        """„Artikel synchronisieren“: Änderungen seit dem letzten Stand (beim ersten Mal alle)."""
        self.ensure_one()
        self._check_manager()
        run, message = self._ativ_plan_products("incremental", "manual")
        return self._notify(message, run)

    def action_ativ_products_full(self):
        self.ensure_one()
        self._check_manager()
        run, message = self._ativ_plan_products("full", "manual")
        return self._notify(message, run)

    def action_open_ativ_products(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": self.env._("Artikel aus ATIV"), "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [("plcs_ativ_key", "=like", "%s:%%" % self._ativ_source_code())],
            "context": {"active_test": False},
        }

    # ------------------------------------------------------------------
    # Planen
    # ------------------------------------------------------------------
    def _ativ_product_domain(self):
        try:
            domain = ast.literal_eval(self.ativ_product_domain or "[]")
        except (ValueError, SyntaxError) as e:
            raise UserError(self.env._("Filter Artikel ist ungültig: %s", e)) from e
        if not isinstance(domain, list):
            raise UserError(self.env._("Filter Artikel muss eine Liste sein."))
        return domain

    def _ativ_plan_products(self, mode, trigger):
        """Arbeitsschritte „Artikel abgleichen“ anlegen. Rückgabe: (Lauf, Meldung)."""
        self.ensure_one()
        if self.mode != "ativ":
            raise UserError(self.env._("Die Schnittstelle ist nicht auf ATIV-Gutschriften eingestellt."))
        self._ativ_try_lock()
        Run = self.env["plcs.ativ.run"].sudo()
        running = Run.search([("connector_id", "=", self.id), ("state", "=", "running"),
                              ("mode", "in", PRODUCT_RUN_MODES)], limit=1)
        if running and mode != "full":
            self._ativ_trigger_worker()
            return running, self.env._("Ein Artikel-Lauf ist schon in Arbeit (begonnen %s). Er wird fortgesetzt.",
                                       fields.Datetime.to_string(running.started))
        if mode == "incremental" and not self.ativ_product_cursor:
            mode = "full"
        run_mode = "products_full" if mode == "full" else "products"
        client = self._ativ_client()
        start = fields.Datetime.now()
        try:
            if mode == "full":
                ids = self._ativ_search_ids(client, "product.product", self._ativ_product_domain())
            else:
                ids = self._ativ_changed_product_ids(client, self.ativ_product_cursor - OVERLAP)
        except AtivError as e:
            Run.create({
                "connector_id": self.id, "mode": run_mode, "trigger": trigger, "user_id": self.env.uid,
                "state": "failed", "finished": fields.Datetime.now(), "cursor_from": self.ativ_product_cursor,
                "message": self.env._("Planung fehlgeschlagen: %s", e),
            })
            self._ativ_commit()
            raise UserError(self.env._("ATIV-Artikelabruf fehlgeschlagen: %s", e)) from e
        run = Run.create({
            "connector_id": self.id, "mode": run_mode, "trigger": trigger, "user_id": self.env.uid,
            "cursor_from": self.ativ_product_cursor if mode == "incremental" else False,
            "cursor_candidate": start, "count_planned": len(ids),
        })
        self._ativ_enqueue(run, "product", "product.product", ids)
        if running:
            self.env["plcs.ativ.queue"].sudo().search([("run_id", "=", running.id), ("state", "=", "pending")]).write(
                {"run_id": run.id})
            running.write({"state": "partial", "finished": fields.Datetime.now(),
                           "message": self.env._("Durch Artikel-Lauf %s ersetzt.", run.id)})
        self._ativ_finalize_runs()
        self._ativ_trigger_worker()
        return run, self.env._("%s Artikel zum Abgleich vorgemerkt. Der Abgleich läuft im Hintergrund.", len(ids))

    def _ativ_changed_product_ids(self, client, since):
        """Artikel, die sich seit ``since`` geändert haben – auch über die Produktvorlage (z. B. Bild)."""
        s = fields.Datetime.to_string(since)
        changed = {
            "product.product": set(self._ativ_search_ids(client, "product.product", [("write_date", ">=", s)])),
            "product.template": set(self._ativ_search_ids(client, "product.template", [("write_date", ">=", s)])),
        }
        return self._ativ_resolve_product_ids(client, changed)

    def _ativ_resolve_product_ids(self, client, changed):
        """Geänderte ATIV-Artikel/Vorlagen → Artikel-IDs (nur innerhalb des Filters)."""
        ids = set(changed.get("product.product") or ())
        for chunk in _chunks(sorted(changed.get("product.template") or ()), 1000):
            ids.update(client.call("product.product", "search", domain=[("product_tmpl_id", "in", chunk)]))
        ids.discard(False)
        result = []
        domain = self._ativ_product_domain()
        for chunk in _chunks(sorted(ids), 1000):
            result.extend(client.call("product.product", "search", domain=domain + [("id", "in", chunk)], order="id"))
        return result

    # ------------------------------------------------------------------
    # Abarbeiten
    # ------------------------------------------------------------------
    def _ativ_process_product(self, client, items):
        try:
            rows = self._ativ_read(client, "product.product", items.mapped("res_id"))
        except AtivError as e:
            self._ativ_fail(items, str(e), transient=e.transient)
            return
        results = {}     # Arbeitsschritt → (Status, Abweichung, Hinweis)
        need_image = {}  # ATIV-ID → (Arbeitsschritt, PLCS-Vorlage)
        for item in items:
            row = rows.get(item.res_id)
            if row is None:
                # Bei ATIV nicht (mehr) vorhanden oder archiviert → in PLCS nichts löschen
                self._ativ_done(item)
                continue
            try:
                with self.env.cr.savepoint():
                    status, template, differs, note = self._ativ_apply_product(row)
            except Exception as e:  # noqa: BLE001 – Fehler je Artikel protokollieren, Rest weiter
                _logger.warning("ATIV-Artikel %s nicht übernommen: %s", item.res_id, e)
                self._ativ_fail(item, "%s: %s" % (type(e).__name__, e), transient=False)
                if item.run_id:
                    item.run_id.count_failed += 1
                continue
            results[item] = [status, differs, note]
            if template and not template.image_1920:
                need_image[item.res_id] = (item, template)

        images = set()
        if need_image:
            try:
                for chunk in _chunks(sorted(need_image), IMAGE_CHUNK):
                    for row in client.call("product.product", "read", ids=chunk, fields=["image_1920"]):
                        data = row.get("image_1920")
                        if not data or row["id"] not in need_image:
                            continue
                        item, template = need_image[row["id"]]
                        try:
                            with self.env.cr.savepoint():
                                template.sudo().write({"image_1920": data})
                            images.add(item)
                        except Exception as e:  # noqa: BLE001 – kaputtes Bild darf den Artikel nicht blockieren
                            _logger.info("Produktbild ATIV-Artikel %s ungültig: %s", row["id"], e)
                            results[item][2] = "; ".join(filter(None, [results[item][2],
                                                                       "Produktbild aus ATIV ungültig"]))
            except AtivError as e:
                # Bilder fehlen noch: diese Artikel später erneut versuchen (Daten sind schon übernommen)
                retry = self.env["plcs.ativ.queue"].browse(
                    [it.id for it, _t in need_image.values() if it not in images])
                self._ativ_fail(retry, str(e), transient=e.transient)
                for it in retry:
                    results.pop(it, None)

        for item, (status, differs, note) in results.items():
            if item in images and status == "unchanged":
                status = "updated"
            self._ativ_done(item)
            if note:
                item.error = note
            run = item.run_id
            if run:
                run.write({
                    "count_products_created": run.count_products_created + (status == "created"),
                    "count_products_updated": run.count_products_updated + (status == "updated"),
                    "count_unchanged": run.count_unchanged + (status == "unchanged"),
                    "count_products_skipped": run.count_products_skipped + (status == "skipped"),
                    "count_products_differ": run.count_products_differ + bool(differs),
                    "count_images": run.count_images + (item in images),
                })

    def _ativ_apply_product(self, row):
        """Einen ATIV-Artikel in PLCS übernehmen.

        Rückgabe: (Status, PLCS-Vorlage oder None, Abweichung ja/nein, Hinweis) mit Status in
        "created" | "updated" | "unchanged" | "skipped".
        """
        Product = self.env["product.product"].sudo().with_context(active_test=False)
        Template = self.env["product.template"].sudo().with_context(active_test=False)
        key = self._ativ_key("product.product", row["id"])
        name = _text(row.get("name")) or _text(row.get("display_name"))
        storage = _m2o_name(row.get("x_studio_storage_conditions")) or (
            _text(row.get("x_studio_storage_conditions")) if isinstance(row.get("x_studio_storage_conditions"), str)
            else False)
        pzn = _norm_pzn(row.get("default_code"))
        pzn_ok = gs1._pzn_check_ok(pzn or "")
        gtin, note = _norm_gtin(row.get("barcode"), pzn if pzn_ok else False)

        template = Template.search([("plcs_ativ_key", "=", key)], limit=1)
        product = template.product_variant_ids[:1] or Product.search([("product_tmpl_id", "=", template.id)], limit=1) \
            if template else Product
        status = "unchanged"
        if not product:
            if not pzn_ok:
                return "skipped", None, False, self.env._("Keine gültige PZN (%s)", pzn or "leer")
            found = Product._plcs_find_by_code(gtin=gtin or gs1.pzn_to_gtin(pzn), pzn=pzn)
            if len(found) > 1:
                return "skipped", None, False, self.env._("PZN %s gibt es in PLCS mehrfach", pzn)
            if found:
                other = found.product_tmpl_id.plcs_ativ_key
                if other and other != key:
                    return "skipped", None, False, self.env._(
                        "PZN %(pzn)s ist schon dem ATIV-Artikel %(o)s zugeordnet", pzn=pzn, o=other)
                product = found
            else:
                product = Product._plcs_ensure_from_ativ(pzn, gtin, name, storage)
                if not product:
                    return "skipped", None, False, self.env._("Bezeichnung oder GTIN fehlt (PZN %s)", pzn)
                product.product_tmpl_id.description = self.env._(
                    "Aus ATIV-Artikelstamm angelegt (Lagerbedingungen: %s)", storage or "-")
                status = "created"
        template = product.product_tmpl_id

        # Nur leere Felder füllen
        tmpl_vals, prod_vals = {}, {}
        if pzn_ok and not template.plcs_pzn:
            tmpl_vals["plcs_pzn"] = pzn
        if pzn_ok and not product.default_code:
            prod_vals["default_code"] = pzn
        if gtin and not product.barcode:
            taken = Product.search([("barcode", "=", gtin), ("id", "!=", product.id)], limit=1)
            if taken:
                note = "; ".join(filter(None, [note, self.env._(
                    "Barcode %(g)s gehört in PLCS schon zu %(p)s", g=gtin, p=taken.display_name)]))
            else:
                prod_vals["barcode"] = gtin
        if prod_vals:
            product.write(prod_vals)
        if (tmpl_vals or prod_vals) and status == "unchanged":
            status = "updated"

        # Abweichungen nur anzeigen, nicht überschreiben
        diffs = []
        if name and _norm_text(template.name) != _norm_text(name):
            diffs.append(self.env._("Bezeichnung (ATIV: %s)", name))
        if storage:
            ativ_temp = "cool" if storage_is_cool(storage) else "ambient"
            if template.plcs_storage_temp != ativ_temp:
                diffs.append(self.env._("Lagertemperatur (ATIV: %s)", storage))
        if pzn_ok and (tmpl_vals.get("plcs_pzn") or template.plcs_pzn) != pzn:
            diffs.append(self.env._("PZN (ATIV: %s)", pzn))
        diff_text = "; ".join(diffs) or False
        if diff_text and diff_text != template.plcs_ativ_diff:
            template.message_post(body=self.env._(
                "Weicht von ATIV ab: %s. Die PLCS-Werte bleiben unverändert; bei Bedarf von Hand anpassen.",
                diff_text))
        tmpl_vals.update({
            "plcs_ativ_key": key, "plcs_ativ_name": name, "plcs_ativ_storage": storage,
            "plcs_ativ_sync_date": fields.Datetime.now(), "plcs_ativ_diff": diff_text,
        })
        template.write(tmpl_vals)
        return status, template, bool(diff_text), note or False

    def _ativ_finalize_product_run(self, run, vals, failed):
        vals["message"] = self.env._(
            "Artikel: %(c)s neu, %(u)s ergänzt, %(un)s unverändert, %(s)s übersprungen, %(d)s mit Abweichung; "
            "%(i)s Produktbilder; %(f)s Fehler.",
            c=run.count_products_created, u=run.count_products_updated, un=run.count_unchanged,
            s=run.count_products_skipped, d=run.count_products_differ, i=run.count_images, f=failed)
        run.write(vals)
        if not failed and run.cursor_candidate:
            connector = run.connector_id
            if not connector.ativ_product_cursor or connector.ativ_product_cursor < run.cursor_candidate:
                connector.ativ_product_cursor = run.cursor_candidate
            connector.ativ_product_run_id = run
