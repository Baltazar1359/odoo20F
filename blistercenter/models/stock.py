from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

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
        if "bc_blocked" in vals:
            vals = dict(vals)
            if vals["bc_blocked"]:
                # Sperren darf jeder; ein Grund ist Pflicht
                reasons = [vals.get("bc_block_reason")] if "bc_block_reason" in vals else self.mapped("bc_block_reason")
                if not all((r or "").strip() for r in reasons or [False]):
                    raise UserError(self.env._("Für die Sperre einer Charge ist ein Grund Pflicht."))
                to_block = self.filtered(lambda lot: not lot.bc_blocked)
                res = super().write(vals)
                to_block.write({"bc_blocked_by": self.env.uid, "bc_blocked_date": fields.Datetime.now()})
                return res
            # Entsperren nur durch die Rolle Freigabe
            if any(lot.bc_blocked for lot in self) and not (
                self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")
            ):
                raise AccessError(self.env._("Eine gesperrte Charge darf nur die Rolle „Blistercenter Freigabe“ entsperren."))
            vals.update({"bc_block_reason": False, "bc_blocked_by": False, "bc_blocked_date": False})
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

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.picking_id._bc_reset_picked()
        return lines

    def write(self, vals):
        pickings = self.picking_id if PICK_RELEVANT_FIELDS & set(vals) else self.env["stock.picking"]
        res = super().write(vals)
        pickings._bc_reset_picked()
        return res

    def unlink(self):
        pickings = self.picking_id
        res = super().unlink()
        pickings.exists()._bc_reset_picked()
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
        for company in self.company_id:
            quarantine = Config._get(company).quarantine_location_id
            if not quarantine:
                continue
            for line in self.filtered(lambda ml: ml.company_id == company and ml.quantity > 0):
                if not line.location_id._child_of(quarantine) or line.location_dest_id._child_of(quarantine):
                    continue
                if not (self.env.su or self.env.user.has_group("blistercenter.group_bc_approver")):
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
    bc_short_approved_date = fields.Datetime("Kurzläufer freigegeben am", readonly=True, copy=False)
    bc_short_reason = fields.Char("Begründung Kurzläufer", readonly=True, copy=False)
    bc_partial_allowed = fields.Boolean(related="sale_id.bc_partial_allowed", string="Teillieferung erlaubt")
    bc_can_release = fields.Boolean(compute="_compute_bc_can_release")

    @api.depends("partner_id.commercial_partner_id.bc_is_customer", "sale_id.bc_is_blistercenter")
    def _compute_bc_is_blistercenter(self):
        for picking in self:
            picking.bc_is_blistercenter = bool(
                picking.sale_id.bc_is_blistercenter or picking.partner_id.commercial_partner_id.bc_is_customer
            )

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

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------
    def _bc_config(self):
        self.ensure_one()
        return self.env["blistercenter.config"]._get(self.company_id)

    def _bc_short_limit(self):
        months = self._bc_config().short_shelf_months
        return fields.Datetime.now() + relativedelta(months=months)

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

    def _bc_reset_picked(self):
        """Abgeschlossene Kommissionierung zurücksetzen, wenn sich die Lagerzeilen ändern."""
        if self.env.context.get("bc_in_done"):
            return
        for picking in self.filtered(lambda p: p.bc_picked_by and p.state not in ("done", "cancel")):
            picking.sudo().write({"bc_picked_by": False, "bc_picked_date": False})
            picking.message_post(body=self.env._(
                "Kommissionierung geändert – bitte erneut als kommissioniert melden."))

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------
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
        if sale and config.rule_prepayment_before_delivery and sale._bc_needs_prepayment():
            raise UserError(self.env._(
                "%(order)s ist Vorkasse und noch nicht vollständig bezahlt. %(picking)s kann nicht versendet werden.",
                order=sale.name, picking=self.name,
            ))
        if config.rule_partial_needs_approval and not (sale and sale.bc_partial_allowed):
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
        res = super(StockPicking, self.with_context(bc_in_done=True))._action_done()
        done = self.filtered(lambda p: p.state == "done")
        bc_done = done.filtered("bc_kind")
        if bc_done:
            bc_done.sudo().with_context(bc_in_done=True).write({
                "bc_validated_by": self.env.uid, "bc_validated_date": fields.Datetime.now(),
            })
        # Mehrstufiger Versand: wer den Vorschritt gebucht hat, hat kommissioniert
        for picking in done.filtered(lambda p: not p.bc_kind and p.picking_type_code == "internal"):
            outgoing = picking.move_ids.move_dest_ids.picking_id.filtered(
                lambda p: p.bc_kind == "delivery" and not p.bc_picked_by and p.state not in ("done", "cancel"))
            if outgoing:
                outgoing.sudo().with_context(bc_in_done=True).write({
                    "bc_picked_by": self.env.uid, "bc_picked_date": fields.Datetime.now()})
        done.filtered(lambda p: p.bc_kind == "receipt")._bc_create_release()
        self.purchase_id.bc_collective_ids._bc_update_done()
        return res

    def _bc_create_release(self):
        """Nach dem Wareneingang: Freigabe-Vorgang Quarantäne → Bestand für die VP anlegen.

        Die Freigabe ist mit den Lieferungen der Kunden aus der Sammelbestellung verkettet
        (feste Zuordnung). Nach der Freigabe reservieren diese Lieferungen genau diese Ware.
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
            release.message_post(body=self.env._(
                "Freigabe aus der Quarantäne für Wareneingang %(receipt)s. Bitte Ware prüfen und freigeben.",
                receipt=receipt.name))
            if config.notify_release and config.vp_user_id:
                release.with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=self.env._("Wareneingang %(receipt)s aus der Quarantäne freigeben", receipt=receipt.name),
                    user_id=config.vp_user_id.id,
                )
