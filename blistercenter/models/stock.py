from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..tools import bc_auth

BC_PICKING_KINDS = [
    ("receipt", "Wareneingang (Quarantäne)"),
    ("release", "Freigabe (VP)"),
    ("delivery", "Lieferung an Blistercenter"),
]
# Felder einer Lagerzeile, deren Änderung eine abgeschlossene Kommissionierung ungültig macht
PICK_RELEVANT_FIELDS = {
    "quantity", "lot_id", "lot_name", "product_id", "location_id", "location_dest_id",
    "package_id", "result_package_id", "owner_id",
}
# Nachweisfelder: nur über die geprüften Aktionen (Prüfbericht 19.10.2026 R01, R03)
PICKING_PROTECTED_FIELDS = (
    "bc_validated_by", "bc_validated_date", "bc_picked_by", "bc_picked_date",
    "bc_short_approved_lot_ids", "bc_short_approved_by", "bc_short_approved_date", "bc_short_reason",
    # Wareneingangsprüfung und Freigabeentscheidung (1.3.0)
    "bc_check_packaging", "bc_check_temperature", "bc_check_documents", "bc_check_quality",
    "bc_check_note", "bc_checked_by", "bc_checked_date",
    "bc_receipt_id", "bc_release_mode", "bc_release_rule", "bc_release_result", "bc_release_evaluated_date",
)
LOT_PROTECTED_FIELDS = ("bc_blocked_by", "bc_blocked_date")

# Wareneingangsprüfung: jede Frage muss ausdrücklich beantwortet sein; leer gilt als nicht geprüft
CHECK_ANSWERS = [("ok", "In Ordnung"), ("deviation", "Abweichung")]
RECEIPT_CHECKS = [
    ("bc_check_packaging", "Ware unbeschädigt und ordnungsgemäß verpackt"),
    ("bc_check_temperature", "Keine Temperaturabweichung bekannt (Transport nach Herstellerangaben)"),
    ("bc_check_documents", "Lieferung entspricht Bestellung und Lieferschein (Artikel, Menge, Charge, Verfall)"),
    ("bc_check_quality", "Keine Qualitätsbeanstandung, kein Rückruf- oder Fälschungsverdacht bekannt"),
]
RELEASE_MODES = [("auto", "Automatisch (Regel BC-AUTO-1)"), ("vp", "Durch die VP")]
# Kennung der Freigaberegel; bei jeder fachlichen Änderung der Kriterien hochzählen
AUTO_RELEASE_RULE = "BC-AUTO-1"


class StockLot(models.Model):
    _inherit = "stock.lot"

    bc_blocked = fields.Boolean("Charge gesperrt", tracking=True, copy=False)
    bc_block_reason = fields.Char("Sperrgrund", tracking=True, copy=False)
    bc_blocked_by = fields.Many2one("res.users", "Gesperrt von", readonly=True, copy=False)
    bc_blocked_date = fields.Datetime("Gesperrt am", readonly=True, copy=False)
    bc_can_unblock = fields.Boolean(compute="_compute_bc_can_unblock")

    @api.depends_context("uid")
    def _compute_bc_can_unblock(self):
        allowed = self.env.user.has_group("blistercenter.group_bc_approver")
        for lot in self:
            lot.bc_can_unblock = allowed

    def write(self, vals):
        bc_auth.check_protected(self, vals, LOT_PROTECTED_FIELDS)
        if "bc_blocked" in vals:
            vals = dict(vals)
            if vals["bc_blocked"]:
                # Sperren darf jeder; ein Grund ist Pflicht
                reasons = [vals.get("bc_block_reason")] if "bc_block_reason" in vals else self.mapped("bc_block_reason")
                if not all((r or "").strip() for r in reasons or [False]):
                    raise UserError(self.env._("Für die Sperre einer Charge ist ein Grund Pflicht."))
                to_block = self.filtered(lambda lot: not lot.bc_blocked)
                res = super().write(vals)
                bc_auth.grant(to_block, "lot_block").write({
                    "bc_blocked_by": self.env.uid, "bc_blocked_date": fields.Datetime.now()})
                return res
            # Entsperren nur durch die Rolle Freigabe
            if any(lot.bc_blocked for lot in self) and not (
                self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")
            ):
                raise AccessError(self.env._("Eine gesperrte Charge darf nur die Rolle „Blistercenter Freigabe“ entsperren."))
            vals.update({"bc_block_reason": False, "bc_blocked_by": False, "bc_blocked_date": False})
            return super(StockLot, bc_auth.grant(self, "lot_block")).write(vals)
        return super().write(vals)


class StockRule(models.Model):
    _inherit = "stock.rule"

    def _get_stock_move_values(self, product_id, product_qty, uom_id, location_dest_id, name, origin, company_id, values):
        """Feste Zuordnung: Lieferungen an Blistercenter warten auf die Ware ihrer Sammelbestellung.

        Die erste Bewegung aus dem Bestand wird „auf Auftrag“ angelegt, aber ohne eigene
        Beschaffung (siehe StockMove._action_confirm). Sie reserviert später nur die Ware,
        die über die Freigabe der Sammelbestellung kommt.
        """
        vals = super()._get_stock_move_values(
            product_id, product_qty, uom_id, location_dest_id, name, origin, company_id, values)
        sale_line_id = values.get("sale_line_id")
        if not sale_line_id or self.procure_method != "make_to_stock":
            return vals
        sale_line = self.env["sale.order.line"].browse(sale_line_id)
        if not sale_line.bc_is_blistercenter or sale_line.bc_from_stock:
            return vals
        config = self.env["blistercenter.config"]._get(company_id)
        warehouse = self.picking_type_id.warehouse_id or self.warehouse_id
        if config.rule_fixed_allocation and warehouse and self.location_src_id \
                and self.location_src_id._child_of(warehouse.lot_stock_id):
            # procure_method setzt StockMove.create (stock.rule._run_pull überschreibt den Wert danach)
            vals["bc_wait_collective"] = True
        return vals


class StockMove(models.Model):
    _inherit = "stock.move"

    bc_wait_collective = fields.Boolean(
        "Wartet auf Sammelbestellung", index="btree_not_null",
        help="Lieferung an ein Blistercenter, die nur Ware aus seiner Sammelbestellung reserviert.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("bc_wait_collective"):
                vals["procure_method"] = "make_to_order"
        return super().create(vals_list)

    def write(self, vals):
        # Feste Zuordnung nur über „Aus Lagerbestand liefern“ aufheben (Prüfbericht 19.10.2026)
        bc_auth.check_protected(self, vals, ("bc_wait_collective",))
        return super().write(vals)

    def _action_confirm(self, merge=True, merge_into=False, create_proc=True):
        # Bewegungen, die auf eine Sammelbestellung warten, lösen keine eigene Beschaffung aus
        waiting = self.filtered(lambda m: m.bc_wait_collective and m.state == "draft" and not m.move_orig_ids)
        if not waiting or not create_proc:
            return super()._action_confirm(merge=merge, merge_into=merge_into, create_proc=create_proc)
        moves = super(StockMove, self - waiting)._action_confirm(merge=merge, merge_into=merge_into, create_proc=True)
        moves |= super(StockMove, waiting)._action_confirm(merge=merge, merge_into=merge_into, create_proc=False)
        return moves


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    bc_expiration_date = fields.Datetime(related="lot_id.expiration_date", string="Verfall")
    bc_scanned_expiry = fields.Date(
        "Verfall laut Packung", readonly=True, copy=False,
        help="Verfallsdatum aus dem DataMatrix-Scan. Bleibt erhalten, auch wenn Odoo die Zeile auf eine "
             "vorhandene Charge mit anderem Datum bucht (Prüfbericht 21.10.2026).")

    def _bc_typed_expiry(self):
        """Verfall, den der Wareneingang für diese Zeile erfasst hat (Scan oder Eingabe), als Datum."""
        self.ensure_one()
        if self.bc_scanned_expiry:
            return self.bc_scanned_expiry
        return self.expiration_date.date() if self.expiration_date else False

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.picking_id._bc_reset_picked()
        lines.picking_id._bc_reset_receipt_check()
        return lines

    def write(self, vals):
        # Nachweis aus dem Scan nur über den Scan-Assistenten ändern
        bc_auth.check_protected(self, vals, ("bc_scanned_expiry",), name="scan")
        pickings = self.picking_id if PICK_RELEVANT_FIELDS & set(vals) else self.env["stock.picking"]
        res = super().write(vals)
        pickings._bc_reset_picked()
        pickings._bc_reset_receipt_check()
        return res

    def unlink(self):
        pickings = self.picking_id
        res = super().unlink()
        pickings.exists()._bc_reset_picked()
        pickings.exists()._bc_reset_receipt_check()
        return res

    def _action_done(self):
        self._bc_check_quarantine_exit()
        return super()._action_done()

    def _bc_check_quarantine_exit(self):
        """Ware verlässt die Blistercenter-Quarantäne nur durch die Rolle Freigabe (VP).

        Gilt für alle Wege (Transfers, Barcode-App, Inventur, Ausschuss, Rücksendung).
        In den Bestand nur über den Vorgangstyp „Blistercenter: Freigabe (VP)“.
        """
        Config = self.env["blistercenter.config"]
        auto = bc_auth.has(self.env, "auto_release")
        for company in self.company_id:
            quarantine = Config._get(company).quarantine_location_id
            if not quarantine:
                continue
            for line in self.filtered(lambda ml: ml.company_id == company and ml.quantity > 0):
                if not line.location_id._child_of(quarantine) or line.location_dest_id._child_of(quarantine):
                    continue
                # Automatische Freigabe nach Regel BC-AUTO-1 (Freigabe nur aus dem Modulcode, nicht per RPC)
                auto_ok = auto and line.picking_id.bc_kind == "release" and line.picking_id.bc_release_mode == "auto"
                if not (self.env.su or auto_ok or self.env.user.has_group("blistercenter.group_bc_approver")):
                    raise UserError(self.env._(
                        "%(product)s (%(lot)s) liegt in der Blistercenter-Quarantäne. Nur die Rolle "
                        "„Blistercenter Freigabe“ (VP) darf die Ware dort herausnehmen.",
                        product=line.product_id.display_name, lot=line.lot_id.name or "-"))
                if line.location_dest_id.usage == "internal" and line.picking_id.bc_kind != "release":
                    raise UserError(self.env._(
                        "%(product)s (%(lot)s): Aus der Quarantäne in den Bestand nur über die "
                        "Blistercenter-Freigabe (VP).",
                        product=line.product_id.display_name, lot=line.lot_id.name or "-"))


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bc_is_blistercenter = fields.Boolean(
        "Blistercenter", compute="_compute_bc_is_blistercenter", store=True, index=True,
    )
    bc_kind = fields.Selection(
        BC_PICKING_KINDS, string="Blistercenter-Schritt", compute="_compute_bc_kind", store=True, index=True,
    )
    bc_validated_by = fields.Many2one("res.users", "Gebucht von", readonly=True, copy=False)
    bc_validated_date = fields.Datetime("Gebucht am", readonly=True, copy=False)
    bc_picked_by = fields.Many2one(
        "res.users", "Kommissioniert von", readonly=True, copy=False,
        help="Wer die Kommissionierung abgeschlossen hat. Die Endkontrolle macht eine andere Person.",
    )
    bc_picked_date = fields.Datetime("Kommissioniert am", readonly=True, copy=False)
    bc_short_lot_ids = fields.Many2many(
        "stock.lot", string="Kurzläufer", compute="_compute_bc_short_lots",
        help="Chargen in diesem Vorgang mit Restlaufzeit unter der Grenze aus den Einstellungen.",
    )
    bc_short_open = fields.Boolean("Kurzläufer-Freigabe offen", compute="_compute_bc_short_lots")
    bc_short_approved_lot_ids = fields.Many2many(
        "stock.lot", "bc_picking_short_lot_rel", "picking_id", "lot_id",
        string="Kurzläufer freigegeben", readonly=True, copy=False,
    )
    bc_short_approved_by = fields.Many2one("res.users", "Kurzläufer freigegeben von", readonly=True, copy=False)
    bc_lot_warning = fields.Text(
        "Hinweise zu Chargen", compute="_compute_bc_lot_warning",
        help="Wareneingang: bekannte Charge mit anderem Verfallsdatum oder gesperrte Charge. Nur Hinweis, "
             "das Buchen bleibt möglich (Entscheidung 21.10.2026).")
    bc_short_approved_date = fields.Datetime("Kurzläufer freigegeben am", readonly=True, copy=False)
    bc_short_reason = fields.Char("Begründung Kurzläufer", readonly=True, copy=False)
    bc_partial_allowed = fields.Boolean(related="sale_id.bc_partial_allowed", string="Teillieferung erlaubt")
    bc_can_release = fields.Boolean(compute="_compute_bc_can_release")
    bc_invoice_ids = fields.One2many("account.move", "bc_picking_id", string="Rechnungen zur Lieferung", readonly=True)
    bc_overdue_amount = fields.Monetary(
        "Überfällig beim Kunden", compute="_compute_bc_overdue", currency_field="bc_currency_id",
    )
    bc_overdue_count = fields.Integer("Überfällige Rechnungen", compute="_compute_bc_overdue")
    bc_currency_id = fields.Many2one(related="company_id.currency_id", string="Währung")

    # Wareneingangsprüfung (nur Eingang in die Quarantäne, nur über den Assistenten)
    bc_check_packaging = fields.Selection(CHECK_ANSWERS, "Verpackung und Zustand", readonly=True, copy=False, tracking=True)
    bc_check_temperature = fields.Selection(CHECK_ANSWERS, "Temperatur", readonly=True, copy=False, tracking=True)
    bc_check_documents = fields.Selection(CHECK_ANSWERS, "Abgleich Bestellung/Lieferschein", readonly=True, copy=False, tracking=True)
    bc_check_quality = fields.Selection(CHECK_ANSWERS, "Qualität/Rückruf", readonly=True, copy=False, tracking=True)
    bc_check_note = fields.Char("Bemerkung Wareneingangsprüfung", readonly=True, copy=False, tracking=True)
    bc_checked_by = fields.Many2one("res.users", "Geprüft von", readonly=True, copy=False)
    bc_checked_date = fields.Datetime("Geprüft am", readonly=True, copy=False)
    bc_check_state = fields.Selection(
        [("open", "Nicht geprüft"), ("ok", "Ohne Abweichung"), ("deviation", "Abweichung")],
        "Wareneingangsprüfung", compute="_compute_bc_check_state", store=True, index=True,
    )

    # Freigabeentscheidung (Freigabe aus der Quarantäne)
    bc_receipt_id = fields.Many2one(
        "stock.picking", "Wareneingang", readonly=True, copy=False, index="btree_not_null",
        help="Wareneingang, aus dem diese Freigabe entstanden ist.",
    )
    bc_release_mode = fields.Selection(
        RELEASE_MODES, "Freigabeweg", readonly=True, copy=False, index=True, tracking=True,
        help="Automatisch: alle Kriterien der Regel BC-AUTO-1 erfüllt, Verfahren von der VP freigegeben. "
             "Durch die VP: mindestens ein Kriterium nicht erfüllt oder nicht geprüft.",
    )
    bc_release_rule = fields.Char("Angewendete Freigaberegel", readonly=True, copy=False)
    bc_release_result = fields.Text("Prüfergebnis Freigaberegel", readonly=True, copy=False)
    bc_release_evaluated_date = fields.Datetime("Regel geprüft am", readonly=True, copy=False)

    @api.depends("bc_kind", *(f for f, _label in RECEIPT_CHECKS))
    def _compute_bc_check_state(self):
        for picking in self:
            answers = [picking[f] for f, _label in RECEIPT_CHECKS]
            if picking.bc_kind != "receipt":
                picking.bc_check_state = False
            elif "deviation" in answers:
                picking.bc_check_state = "deviation"
            elif all(a == "ok" for a in answers):
                picking.bc_check_state = "ok"
            else:
                picking.bc_check_state = "open"

    @api.depends_context("uid")
    def _compute_bc_overdue(self):
        """Nur Hinweis auf überfällige Rechnungen des Kunden; der Versand bleibt möglich."""
        for picking in self:
            partner = (picking.sale_id.partner_id or picking.partner_id).commercial_partner_id.with_company(
                picking.company_id or self.env.company).with_context(bc_force_overdue=True)
            if picking.bc_kind == "delivery":
                picking.bc_overdue_count = partner.bc_overdue_count
                picking.bc_overdue_amount = partner.bc_overdue_amount
            else:
                picking.bc_overdue_count = 0
                picking.bc_overdue_amount = 0.0

    @api.depends("partner_id.commercial_partner_id.bc_is_customer", "sale_id.bc_is_blistercenter")
    def _compute_bc_is_blistercenter(self):
        # 1.6.3: Mit Auftrag zählt allein die (bei Bestätigung festgehaltene) Zuordnung des Auftrags,
        # nicht das aktuelle Kennzeichen der Lieferadresse.
        for picking in self:
            if picking.sale_id:
                picking.bc_is_blistercenter = picking.sale_id.bc_is_blistercenter
            else:
                picking.bc_is_blistercenter = picking.partner_id.commercial_partner_id.bc_is_customer

    @api.depends("picking_type_id", "picking_type_code", "bc_is_blistercenter")
    def _compute_bc_kind(self):
        Config = self.env["blistercenter.config"]
        for picking in self:
            config = Config._get(picking.company_id or self.env.company)
            if picking.picking_type_id and picking.picking_type_id == config.receipt_type_id:
                picking.bc_kind = "receipt"
            elif picking.picking_type_id and picking.picking_type_id == config.release_type_id:
                picking.bc_kind = "release"
            elif picking.picking_type_code == "outgoing" and picking.bc_is_blistercenter:
                picking.bc_kind = "delivery"
            else:
                picking.bc_kind = False

    @api.depends("move_line_ids.lot_id.expiration_date", "move_line_ids.quantity", "bc_short_approved_lot_ids")
    def _compute_bc_short_lots(self):
        for picking in self:
            lots = picking._bc_short_lots() if picking.bc_kind in ("release", "delivery") else self.env["stock.lot"]
            picking.bc_short_lot_ids = lots
            picking.bc_short_open = bool(lots - picking.bc_short_approved_lot_ids)

    @api.depends_context("uid")
    def _compute_bc_can_release(self):
        allowed = self.env.user.has_group("blistercenter.group_bc_approver")
        for picking in self:
            picking.bc_can_release = allowed

    @api.depends("bc_kind", "state", "move_line_ids.lot_id", "move_line_ids.lot_name",
                 "move_line_ids.expiration_date", "move_line_ids.bc_scanned_expiry", "move_line_ids.product_id")
    def _compute_bc_lot_warning(self):
        for picking in self:
            open_receipt = picking.bc_kind == "receipt" and picking.state not in ("done", "cancel")
            picking.bc_lot_warning = "\n".join(picking._bc_lot_warnings()) if open_receipt else False

    def _bc_lot_warnings(self):
        """Hinweise zu Chargen im Wareneingang (nur Hinweis, keine Sperre).

        Dieselbe Charge kommt oft mehrfach vom Hersteller; Odoo bucht sie auf die vorhandene Charge und
        behält deren Verfallsdatum. Ein abweichend eingegebenes Datum wird sonst stillschweigend übergangen.
        """
        self.ensure_one()
        notes = []
        seen = set()
        Lot = self.env["stock.lot"].sudo()
        for line in self.move_line_ids:
            name = line.lot_id.name or line.lot_name
            if not (name and line.product_id):
                continue
            lot = line.lot_id or Lot.search([
                ("product_id", "=", line.product_id.id), ("name", "=", name),
                ("company_id", "in", [line.company_id.id, False])], limit=1)
            if not lot:
                continue
            typed = line._bc_typed_expiry()
            known = lot.expiration_date and lot.expiration_date.date()
            if lot.bc_blocked and ("blocked", lot.id) not in seen:
                seen.add(("blocked", lot.id))
                notes.append(self.env._(
                    "Charge %(lot)s (%(product)s) ist gesperrt (%(reason)s). Nach dem Eingang bleibt sie in der "
                    "Quarantäne; Freigabe und Versand sind gesperrt.",
                    lot=lot.name, product=line.product_id.display_name, reason=lot.bc_block_reason or "-"))
            if typed and known and typed != known and ("date", lot.id, typed) not in seen:
                seen.add(("date", lot.id, typed))
                notes.append(self.env._(
                    "Charge %(lot)s (%(product)s): Verfall eingegeben %(typed)s, die vorhandene Charge hat "
                    "%(known)s. Odoo behält %(known)s – bitte mit der Packung vergleichen.",
                    lot=lot.name, product=line.product_id.display_name,
                    typed=typed.strftime("%d.%m.%Y"), known=known.strftime("%d.%m.%Y")))
        return notes

    def _bc_post_lot_notes(self, notes):
        """Hinweise nach dem Buchen im Verlauf festhalten; die VP bekommt eine Aufgabe zur Prüfung."""
        self.ensure_one()
        body = Markup("<p><strong>%s</strong></p><ul>%s</ul>") % (
            self.env._("Hinweise zu Chargen beim Wareneingang"),
            Markup("").join(Markup("<li>%s</li>") % note for note in notes))
        self.sudo().message_post(body=body)
        vp = self._bc_config().vp_user_id
        if vp:
            self.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                "mail.mail_activity_data_todo", user_id=vp.id,
                summary=self.env._("Chargen-Hinweis im Wareneingang %(picking)s prüfen", picking=self.name))

    def action_bc_scan(self):
        """Packungen erfassen: DataMatrix scannen oder von Hand eintragen (Artikel, Charge, Verfall, Menge)."""
        self.ensure_one()
        if self.bc_kind != "receipt" or self.state in ("done", "cancel"):
            raise UserError(self.env._("Scannen ist nur in offenen Blistercenter-Wareneingängen möglich."))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Packungen erfassen"),
            "res_model": "blistercenter.receipt.scan",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id, "dialog_size": "large"},
        }

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------
    def _bc_config(self):
        self.ensure_one()
        return self.env["blistercenter.config"]._get(self.company_id)

    def _bc_short_limit(self):
        months = self._bc_config().short_shelf_months
        return fields.Datetime.now() + relativedelta(months=months)

    def _bc_expired_lots(self):
        """Chargen mit Menge, deren Verfall erreicht ist (Verfallsdatum der Charge bis jetzt)."""
        self.ensure_one()
        now = fields.Datetime.now()
        return self.move_line_ids.filtered(
            lambda ml: ml.quantity > 0 and ml.lot_id.expiration_date and ml.lot_id.expiration_date <= now).lot_id

    def _bc_short_lots(self):
        """Chargen mit Restlaufzeit unter der Grenze (nur Zeilen mit Menge)."""
        self.ensure_one()
        config = self._bc_config()
        if not config.short_shelf_months:
            return self.env["stock.lot"]
        limit = self._bc_short_limit()
        lines = self.move_line_ids.filtered(
            lambda ml: ml.quantity > 0 and ml.lot_id.expiration_date and ml.lot_id.expiration_date < limit)
        return lines.lot_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            bc_auth.check_protected(self, vals, PICKING_PROTECTED_FIELDS)
        return super().create(vals_list)

    def write(self, vals):
        bc_auth.check_protected(self, vals, PICKING_PROTECTED_FIELDS)
        return super().write(vals)

    def _bc_reset_picked(self):
        """Abgeschlossene Kommissionierung zurücksetzen, wenn sich die Lagerzeilen ändern.

        Ausnahme nur während der Buchung selbst; die Freigabe dafür kann ein RPC-Aufruf nicht
        mitschicken (Prüfbericht 19.10.2026 R02, früher Kontextwert „bc_in_done“).
        """
        if bc_auth.has(self.env, "in_done"):
            return
        for picking in self.filtered(lambda p: p.bc_picked_by and p.state not in ("done", "cancel")):
            picking.sudo().write({"bc_picked_by": False, "bc_picked_date": False})
            picking.message_post(body=self.env._(
                "Kommissionierung geändert – bitte erneut als kommissioniert melden."))

    def _bc_reset_receipt_check(self):
        """Wareneingangsprüfung zurücksetzen, wenn sich Menge, Charge oder Artikel danach ändern.

        Die Prüfung bezieht sich auf die erfassten Daten; nach einer Änderung muss sie neu erfolgen.
        """
        if bc_auth.has(self.env, "in_done"):
            return
        for picking in self.filtered(lambda p: p.bc_checked_by and p.state not in ("done", "cancel")):
            bc_auth.grant(picking.sudo(), "receipt_check").write(
                {**{f: False for f, _label in RECEIPT_CHECKS},
                 "bc_check_note": False, "bc_checked_by": False, "bc_checked_date": False})
            picking.message_post(body=self.env._(
                "Wareneingang geändert – bitte die Wareneingangsprüfung erneut erfassen."))

    def _bc_receipt_check_passed(self):
        """Wareneingangsprüfung vollständig erfasst und ohne jede Abweichung."""
        self.ensure_one()
        return bool(self.bc_checked_by and self.bc_checked_date and self.bc_check_state == "ok")

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
    def action_bc_receipt_check(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Wareneingangsprüfung"),
            "res_model": "blistercenter.receipt.check.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }

    def _bc_record_receipt_check(self, answers, note=False):
        """Ergebnis der Wareneingangsprüfung festhalten (wer, wann, Antworten).

        Nur vor der Buchung eines Blistercenter-Eingangs. Jede Frage muss mit „In Ordnung“ oder
        „Abweichung“ beantwortet sein; eine Abweichung braucht eine Bemerkung.
        """
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group("blistercenter.group_bc_user")):
            raise AccessError(self.env._("Die Wareneingangsprüfung erfassen nur Blistercenter-Benutzer."))
        if self.bc_kind != "receipt" or self.state in ("done", "cancel"):
            raise UserError(self.env._(
                "Die Wareneingangsprüfung wird vor der Buchung eines Blistercenter-Wareneingangs erfasst."))
        valid = {key for key, _label in CHECK_ANSWERS}
        missing = [label for field, label in RECEIPT_CHECKS if answers.get(field) not in valid]
        if missing:
            raise UserError(self.env._("Bitte jede Prüffrage beantworten: %(missing)s", missing="; ".join(missing)))
        deviations = [label for field, label in RECEIPT_CHECKS if answers[field] == "deviation"]
        if deviations and not (note or "").strip():
            raise UserError(self.env._("Bitte die Abweichung beschreiben."))
        bc_auth.grant(self.sudo(), "receipt_check").write({
            **{field: answers[field] for field, _label in RECEIPT_CHECKS},
            "bc_check_note": (note or "").strip() or False,
            "bc_checked_by": self.env.uid,
            "bc_checked_date": fields.Datetime.now(),
        })
        if deviations:
            body = self.env._("Wareneingangsprüfung mit Abweichung: %(items)s. %(note)s",
                              items="; ".join(deviations), note=note.strip())
        else:
            body = self.env._("Wareneingangsprüfung ohne Abweichung erfasst.")
        self.message_post(body=body)
        return True
    def action_bc_mark_picked(self):
        """Kommissionierung abgeschlossen melden. Die Endkontrolle macht eine andere Person."""
        for picking in self:
            if picking.bc_kind != "delivery" or picking.state in ("done", "cancel", "draft"):
                raise UserError(self.env._("Nur offene Lieferungen an Blistercenter."))
            lines = picking.move_line_ids.filtered(lambda ml: ml.quantity > 0)
            if not lines:
                raise UserError(self.env._("%(picking)s: Es ist noch nichts kommissioniert.", picking=picking.name))
            picking.move_ids.filtered(lambda m: m.quantity > 0).picked = True
            picking.sudo().write({"bc_picked_by": self.env.uid, "bc_picked_date": fields.Datetime.now()})
            picking.message_post(body=self.env._("Kommissionierung abgeschlossen. Endkontrolle durch eine zweite Person."))
        return True

    def action_bc_short_approve(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Kurzläufer freigeben"),
            "res_model": "blistercenter.short.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }

    def _bc_approve_short(self, reason):
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")):
            raise AccessError(self.env._("Kurzläufer gibt nur die Rolle „Blistercenter Freigabe“ (VP) frei."))
        if not (reason or "").strip():
            raise UserError(self.env._("Bitte die Freigabe der Kurzläufer begründen."))
        expired = self._bc_expired_lots()
        if expired:
            raise UserError(self.env._(
                "%(lots)s ist verfallen. Verfallene Ware kann nicht als Kurzläufer freigegeben werden.",
                lots=", ".join(expired.mapped("display_name"))))
        lots = self._bc_short_lots()
        if not lots:
            raise UserError(self.env._("%(picking)s enthält keine Kurzläufer.", picking=self.name))
        self.sudo().write({
            "bc_short_approved_lot_ids": [fields.Command.set(lots.ids)],
            "bc_short_approved_by": self.env.uid,
            "bc_short_approved_date": fields.Datetime.now(),
            "bc_short_reason": reason,
        })
        self.message_post(body=self.env._(
            "Kurzläufer freigegeben (%(lots)s): %(reason)s", lots=", ".join(lots.mapped("name")), reason=reason))
        return True

    # ------------------------------------------------------------------
    # Prüfungen beim Buchen
    # ------------------------------------------------------------------
    def _bc_check_lots(self, config):
        """Gesperrte Chargen, fehlendes Verfallsdatum und Kurzläufer."""
        self.ensure_one()
        lines = self.move_line_ids.filtered(lambda ml: ml.quantity > 0)
        blocked = lines.lot_id.filtered("bc_blocked")
        if blocked:
            raise UserError(self.env._(
                "%(picking)s enthält gesperrte Chargen: %(lots)s.",
                picking=self.name, lots=", ".join(blocked.mapped("name"))))
        # 1.6.3 (GPT-Prüfbericht 23.10.2026): Verfallene Ware nie freigeben oder versenden – auch nicht mit
        # Kurzläufer-Freigabe. Gilt unabhängig von den abschaltbaren Regeln.
        expired = self._bc_expired_lots()
        if expired:
            raise UserError(self.env._(
                "%(picking)s enthält verfallene Chargen: %(lots)s. Verfallene Ware wird nicht freigegeben und "
                "nicht versendet; bitte sperren und aussondern.",
                picking=self.name, lots=", ".join(expired.mapped("display_name"))))
        if config.rule_require_lot_tracking:
            missing = lines.filtered(
                lambda ml: ml.product_id.tracking != "none" and (not ml.lot_id or (
                    ml.product_id.use_expiration_date and not ml.lot_id.expiration_date)))
            if missing:
                raise UserError(self.env._(
                    "%(picking)s: Charge oder Verfallsdatum fehlt bei %(products)s.",
                    picking=self.name, products=", ".join(missing.product_id.mapped("display_name"))))
        needs_short = (self.bc_kind == "release" and config.rule_short_release) or (
            self.bc_kind == "delivery" and config.rule_short_delivery)
        if needs_short:
            open_lots = self._bc_short_lots() - self.bc_short_approved_lot_ids
            if open_lots:
                raise UserError(self.env._(
                    "%(picking)s enthält Kurzläufer (Restlaufzeit unter %(months)s Monaten): %(lots)s. "
                    "Die VP muss sie zusätzlich freigeben („Kurzläufer freigeben“).",
                    picking=self.name, months=config.short_shelf_months,
                    lots=", ".join(open_lots.mapped("name"))))

    def _bc_check_release(self, config):
        self.ensure_one()
        if bc_auth.has(self.env, "auto_release"):
            # Automatische Freigabe: Kriterien der Regel unmittelbar vorher geprüft (_bc_decide_release)
            if self.bc_release_mode != "auto":
                raise UserError(self.env._("%(picking)s: keine automatische Freigabe.", picking=self.name))
            self._bc_check_lots(config)
            return
        user = self.env.user
        if not (self.env.su or user.has_group("blistercenter.group_bc_approver")):
            raise UserError(self.env._(
                "%(picking)s: Die Freigabe aus der Quarantäne bucht nur die Rolle „Blistercenter Freigabe“ (VP).",
                picking=self.name))
        if config.rule_release_four_eyes and not self.env.su:
            receivers = self.move_ids.move_orig_ids.picking_id.bc_validated_by
            if user in receivers:
                raise UserError(self.env._(
                    "%(picking)s: Sie haben den Wareneingang selbst gebucht. Die Freigabe macht eine andere "
                    "Person mit der Rolle „Blistercenter Freigabe“ (Vier-Augen-Prinzip).",
                    picking=self.name))
        self._bc_check_lots(config)

    def _bc_check_delivery(self, config):
        self.ensure_one()
        self._bc_check_lots(config)
        partner = (self.sale_id.partner_id or self.partner_id).commercial_partner_id
        if config.rule_block_delivery and partner.bc_payment_block:
            raise UserError(self.env._(
                "%(customer)s hat eine Zahlungssperre (%(reason)s). %(picking)s kann nicht versendet werden.",
                customer=partner.display_name, reason=partner.bc_payment_block_reason or "-", picking=self.name,
            ))
        sale = self.sale_id
        partial_ok = bool(sale and sale.bc_partial_allowed and (sale.bc_partial_note or "").strip()
                          and sale.bc_partial_by)
        if config.rule_partial_needs_approval and not partial_ok:
            open_moves = self.move_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if any(m.uom_id.compare(m.quantity, m.product_uom_qty) < 0 for m in open_moves):
                raise UserError(self.env._(
                    "%(picking)s ist nicht vollständig. Eine Teillieferung ist nur nach Rücksprache mit dem "
                    "Kunden erlaubt („Teillieferung erlauben“ in der Bestellung).",
                    picking=self.name))
        if config.rule_check_four_eyes and not self.env.su:
            if not self.bc_picked_by:
                raise UserError(self.env._(
                    "%(picking)s: Bitte zuerst „Kommissionierung abgeschlossen“ melden. Danach macht eine "
                    "zweite Person die Endkontrolle und bestätigt die Lieferung.",
                    picking=self.name))
            if self.bc_picked_by == self.env.user:
                raise UserError(self.env._(
                    "%(picking)s: Sie haben selbst kommissioniert. Die Endkontrolle macht eine andere Person "
                    "(Vier-Augen-Prinzip).",
                    picking=self.name))

    def _bc_check_before_done(self):
        for picking in self:
            config = picking._bc_config()
            if picking.bc_kind == "release":
                picking._bc_check_release(config)
            elif picking.bc_kind == "delivery":
                picking._bc_check_delivery(config)
            elif picking.picking_type_code == "outgoing":
                # Gesperrte Chargen werden nie versendet, auch nicht an andere Kunden
                blocked = picking.move_line_ids.filtered(lambda ml: ml.quantity and ml.lot_id.bc_blocked).lot_id
                if blocked:
                    raise UserError(self.env._(
                        "%(picking)s enthält gesperrte Chargen: %(lots)s. Versand nicht möglich.",
                        picking=picking.name, lots=", ".join(blocked.mapped("name")),
                    ))

    def _action_done(self):
        self._bc_check_before_done()
        lot_notes = {p.id: p._bc_lot_warnings() for p in self if p.bc_kind == "receipt"}
        res = super(StockPicking, bc_auth.grant(self, "in_done"))._action_done()
        done = self.filtered(lambda p: p.state == "done")
        bc_done = done.filtered("bc_kind")
        if bc_done:
            # Automatische Freigabe: gebucht vom System (OdooBot), nicht von der Person am Wareneingang
            validator = self.env.ref("base.user_root").id if bc_auth.has(self.env, "auto_release") else self.env.uid
            bc_auth.grant(bc_done.sudo(), "in_done").write({
                "bc_validated_by": validator, "bc_validated_date": fields.Datetime.now(),
            })
        # Mehrstufiger Versand: wer den Vorschritt gebucht hat, hat kommissioniert
        for picking in done.filtered(lambda p: not p.bc_kind and p.picking_type_code == "internal"):
            outgoing = picking.move_ids.move_dest_ids.picking_id.filtered(
                lambda p: p.bc_kind == "delivery" and not p.bc_picked_by and p.state not in ("done", "cancel"))
            if outgoing:
                bc_auth.grant(outgoing.sudo(), "in_done").write({
                    "bc_picked_by": self.env.uid, "bc_picked_date": fields.Datetime.now()})
        done.filtered(lambda p: p.bc_kind == "receipt")._bc_create_release(lot_notes)
        for picking in done.filtered(lambda p: lot_notes.get(p.id)):
            picking._bc_post_lot_notes(lot_notes[picking.id])
        self.purchase_id.bc_collective_ids._bc_update_done()
        done.filtered(lambda p: p.bc_kind == "delivery" and p.sale_id)._bc_invoice_after_delivery()
        return res

    def _bc_invoice_after_delivery(self):
        """Rechnung je Lieferung: nach dem Versand die gelieferte Menge als Rechnungsentwurf anlegen."""
        for picking in self:
            config = picking._bc_config()
            if config.rule_invoice_per_delivery and picking.sale_id.bc_is_blistercenter:
                picking.sale_id._bc_invoice_delivery(picking)

    def _bc_create_release(self, lot_notes=None):
        """Nach dem Wareneingang: Freigabe-Vorgang Quarantäne → Bestand für die VP anlegen.

        Die Freigabe ist mit den Lieferungen der Kunden aus der Sammelbestellung verkettet
        (feste Zuordnung). Nach der Freigabe reservieren diese Lieferungen genau diese Ware.
        `lot_notes`: Chargen-Hinweise je Eingang, vor der Buchung ermittelt (danach ist ein von Hand
        eingegebenes Verfallsdatum durch das der vorhandenen Charge ersetzt).
        """
        Move = self.env["stock.move"].sudo()
        CollectiveLine = self.env["blistercenter.collective.order.line"].sudo()
        for receipt in self:
            config = receipt._bc_config()._bc_check_setup()
            moves = receipt.move_ids.filtered(
                lambda m: m.state == "done" and m.quantity > 0
                and m.location_dest_id._child_of(config.quarantine_location_id)
                and not m.move_dest_ids.filtered(lambda d: d.picking_type_id == config.release_type_id))
            if not moves:
                continue
            release = self.env["stock.picking"].sudo().create({
                "picking_type_id": config.release_type_id.id,
                "location_id": config.quarantine_location_id.id,
                "location_dest_id": config.warehouse_id.lot_stock_id.id,
                "origin": receipt.name,
                "partner_id": receipt.partner_id.id,
                "company_id": receipt.company_id.id,
                "move_type": "direct",
            })
            for move in moves:
                dest = Move.browse()
                if config.rule_fixed_allocation and move.purchase_line_id:
                    lines = CollectiveLine.search([("purchase_line_id", "=", move.purchase_line_id.id)])
                    dest = lines.sale_line_id._bc_wait_moves()
                Move.create({
                    "product_id": move.product_id.id,
                    "product_uom_qty": move.quantity,
                    "uom_id": move.uom_id.id,
                    "location_id": config.quarantine_location_id.id,
                    "location_dest_id": config.warehouse_id.lot_stock_id.id,
                    "picking_id": release.id,
                    "picking_type_id": config.release_type_id.id,
                    "company_id": receipt.company_id.id,
                    "origin": receipt.name,
                    "procure_method": "make_to_order",
                    "move_orig_ids": [fields.Command.set(move.ids)],
                    "move_dest_ids": [fields.Command.set(dest.ids)],
                })
            release.move_ids._action_confirm(merge=False)
            release.action_assign()
            notes = lot_notes.get(receipt.id) if lot_notes is not None else receipt._bc_lot_warnings()
            release._bc_decide_release(receipt, config, lot_notes=notes or [])

    # ------------------------------------------------------------------
    # Freigabeentscheidung: automatisch (Regel BC-AUTO-1) oder durch die VP
    # ------------------------------------------------------------------
    def _bc_auto_release_checks(self, receipt, config, lot_notes=()):
        """Kriterien der Regel BC-AUTO-1 als Liste (erfüllt, Text).

        Jedes Kriterium prüft tatsächlich erfasste Daten. Fehlt ein Wert oder ist er unklar, gilt das
        Kriterium als nicht erfüllt – die Ware bleibt dann in der Quarantäne bei der VP.
        """
        self.ensure_one()
        _ = self.env._
        checks = []
        purchase = receipt.purchase_id
        supplier = purchase.partner_id.commercial_partner_id
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()

        checks.append((bool(
            config.auto_release_enabled and config.auto_release_approved_by and (config.auto_release_sop or "").strip()),
            _("Verfahren von der VP freigegeben und eingeschaltet")))
        returned = receipt.return_id or receipt.move_ids.origin_returned_move_id
        checks.append((bool(
            receipt.picking_type_id == config.receipt_type_id and purchase.bc_is_collective and not returned),
            _("Regulärer Wareneingang aus einer Sammelbestellung (keine Retoure, kein Sonderfall)")))
        checks.append((bool(
            supplier.bc_is_supplier and supplier.bc_supplier_qualified
            and (not supplier.bc_supplier_qualified_until or supplier.bc_supplier_qualified_until >= today)),
            _("Hersteller %(supplier)s GDP-qualifiziert und freigegeben", supplier=supplier.display_name or "-")))
        checks.append((bool(supplier and receipt.partner_id.commercial_partner_id == supplier),
                       _("Lieferung direkt vom Hersteller der Bestellung")))
        moves = receipt.move_ids.filtered(lambda m: m.state == "done" and m.quantity > 0)
        checks.append((bool(moves) and all(
            m.purchase_line_id.order_id == purchase and m.product_id == m.purchase_line_id.product_id
            and m.purchase_line_id.uom_id.compare(m.purchase_line_id.qty_received, m.purchase_line_id.product_qty) <= 0
            for m in moves),
            _("Lieferung entspricht der Bestellung (nur bestellte Artikel, keine Mehrmenge)")))
        checks.append((receipt._bc_receipt_check_passed(),
                       _("Wareneingangsprüfung erfasst, alle Fragen ohne Abweichung")))
        lines = receipt.move_line_ids.filtered(lambda ml: ml.quantity > 0)
        lots = lines.lot_id
        checks.append((bool(lines) and all(
            ml.lot_id and ml.lot_id.product_id == ml.product_id and ml.lot_id.expiration_date
            and ml.product_id.tracking != "none" and ml.product_id.use_expiration_date
            for ml in lines),
            _("Charge und Verfallsdatum je Position vorhanden")))
        checks.append((bool(lots) and not lots.filtered("bc_blocked"),
                       _("Keine gesperrte oder zurückgerufene Charge")))
        checks.append((bool(lots) and all(lot.expiration_date and lot.expiration_date > now for lot in lots),
                       _("Keine abgelaufene Charge")))
        # Zusammenführung 1.7.0: Ein Chargen-Hinweis (R49/R50, z. B. Verfall laut Packung ≠ Verfall der
        # vorhandenen Charge) geht an die VP und darf nicht automatisch freigegeben werden.
        checks.append((not lot_notes,
                       _("Kein Chargen-Hinweis beim Wareneingang (Verfall laut Packung = Verfall der Charge)")))
        checks.append((not self._bc_short_lots(),
                       _("Kein Kurzläufer (Restlaufzeit unter %(months)s Monaten)", months=config.short_shelf_months)))
        release_lines = self.move_line_ids.filtered(lambda ml: ml.quantity > 0)
        checks.append((bool(
            self.state == "assigned" and release_lines and release_lines.lot_id <= lots
            and all(m.uom_id.compare(m.quantity, m.product_uom_qty) == 0 for m in self.move_ids)),
            _("Freigabe umfasst genau die eingegangene Ware")))
        return checks

    def _bc_decide_release(self, receipt, config, lot_notes=()):
        """Regel BC-AUTO-1 prüfen, Ergebnis dokumentieren und automatisch oder über die VP freigeben."""
        self.ensure_one()
        checks = self._bc_auto_release_checks(receipt, config, lot_notes=lot_notes)
        passed = all(ok for ok, _text in checks)
        rule = "%s – %s" % (AUTO_RELEASE_RULE, self.env._(
            "Verfahren %(sop)s, freigegeben von %(vp)s am %(date)s",
            sop=config.auto_release_sop or "-", vp=config.auto_release_approved_by.name or "-",
            date=fields.Datetime.to_string(config.auto_release_approved_date) or "-"))
        result = "\n".join("%s %s" % ("✓" if ok else "✗", text) for ok, text in checks)
        receipt_info = ""
        if receipt.bc_checked_by:
            receipt_info = "\n" + self.env._(
                "Wareneingangsprüfung: %(user)s, %(date)s", user=receipt.bc_checked_by.name,
                date=fields.Datetime.to_string(receipt.bc_checked_date))
        vals = {
            "bc_receipt_id": receipt.id,
            "bc_release_rule": rule,
            "bc_release_result": result + receipt_info,
            "bc_release_evaluated_date": fields.Datetime.now(),
        }
        if passed and self._bc_auto_release(vals, config):
            return True
        bc_auth.grant(self.sudo(), "release_decision").write({**vals, "bc_release_mode": "vp"})
        failed = [text for ok, text in checks if not ok]
        self.message_post(body=self.env._(
            "Freigabe aus der Quarantäne für Wareneingang %(receipt)s durch die VP. Nicht erfüllt: %(failed)s",
            receipt=receipt.name, failed="; ".join(failed) or "-"))
        if config.notify_release and config.vp_user_id:
            summary = self.env._("Wareneingang %(receipt)s aus der Quarantäne freigeben", receipt=receipt.name)
            if receipt.bc_check_state == "deviation":
                summary = self.env._("Abweichung beim Wareneingang %(receipt)s prüfen und entscheiden",
                                     receipt=receipt.name)
            self.with_context(mail_activity_quick_update=True).activity_schedule(
                "mail.mail_activity_data_todo", summary=summary, user_id=config.vp_user_id.id)
        return False

    def _bc_auto_release(self, vals, config):
        """Freigabe ohne VP-Einzelentscheidung buchen. Scheitert die Buchung, bleibt die Ware bei der VP.

        Läuft mit den Rechten der Person am Wareneingang (kein Superuser); die Quarantäne verlässt die Ware
        nur mit der internen Freigabe „auto_release“, die ein RPC-Aufruf nicht erzeugen kann.
        """
        self.ensure_one()
        release = bc_auth.grant(self.sudo(False), "auto_release")
        try:
            with self.env.cr.savepoint():
                bc_auth.grant(self.sudo(), "release_decision").write({**vals, "bc_release_mode": "auto"})
                release.move_ids.picked = True
                release._action_done()
                if release.state != "done":
                    raise UserError(self.env._("Freigabe nicht vollständig gebucht."))
        except (AccessError, UserError, ValidationError) as error:
            vals["bc_release_result"] += "\n" + self.env._("Automatische Buchung abgebrochen: %(error)s", error=error)
            return False
        message = self.env._(
            "Automatisch freigegeben nach Regel %(rule)s (Verfahren %(sop)s). Keine VP-Einzelentscheidung nötig.",
            rule=AUTO_RELEASE_RULE, sop=config.auto_release_sop)
        self.message_post(body=message)
        self.bc_receipt_id.message_post(body=message)
        return True
