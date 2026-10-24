from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.stock import CHECK_ANSWERS, RECEIPT_CHECKS


class BlistercenterPartialWizard(models.TransientModel):
    """Teillieferung erlauben – nur nach Rücksprache mit dem Kunden, mit Vermerk."""

    _name = "blistercenter.partial.wizard"
    _description = "Teillieferung erlauben"

    order_id = fields.Many2one("sale.order", string="Bestellung", required=True, readonly=True)
    note = fields.Char(
        "Vermerk", required=True,
        help="Mit wem und wann die Teillieferung abgestimmt ist, z. B. „Telefonat mit Frau Muster, 15.10.“.",
    )

    def action_apply(self):
        self.ensure_one()
        if not self.order_id.bc_is_blistercenter:
            raise UserError(self.env._("Nur für Blistercenter-Bestellungen."))
        self.order_id._bc_set_partial(True, self.note)
        return {"type": "ir.actions.act_window_close"}


class BlistercenterShortWizard(models.TransientModel):
    """Zusätzliche VP-Freigabe für Kurzläufer (Restlaufzeit unter der Grenze)."""

    _name = "blistercenter.short.wizard"
    _description = "Kurzläufer freigeben"

    picking_id = fields.Many2one("stock.picking", string="Vorgang", required=True, readonly=True)
    lot_ids = fields.Many2many("stock.lot", string="Kurzläufer", related="picking_id.bc_short_lot_ids")
    reason = fields.Char("Begründung", required=True)

    def action_apply(self):
        self.ensure_one()
        self.picking_id._bc_approve_short(self.reason)
        return {"type": "ir.actions.act_window_close"}


class BlistercenterReceiptCheckWizard(models.TransientModel):
    """Wareneingangsprüfung: jede Frage ausdrücklich beantworten, Abweichungen beschreiben."""

    _name = "blistercenter.receipt.check.wizard"
    _description = "Wareneingangsprüfung"

    picking_id = fields.Many2one("stock.picking", string="Wareneingang", required=True, readonly=True)
    bc_check_packaging = fields.Selection(CHECK_ANSWERS, dict(RECEIPT_CHECKS)["bc_check_packaging"], required=True)
    bc_check_temperature = fields.Selection(CHECK_ANSWERS, dict(RECEIPT_CHECKS)["bc_check_temperature"], required=True)
    bc_check_documents = fields.Selection(CHECK_ANSWERS, dict(RECEIPT_CHECKS)["bc_check_documents"], required=True)
    bc_check_quality = fields.Selection(CHECK_ANSWERS, dict(RECEIPT_CHECKS)["bc_check_quality"], required=True)
    note = fields.Char("Bemerkung", help="Pflicht bei einer Abweichung: was, wie viele Packungen, Foto/Protokoll.")

    def action_apply(self):
        self.ensure_one()
        answers = {field: self[field] for field, _label in RECEIPT_CHECKS}
        self.picking_id._bc_record_receipt_check(answers, self.note)
        return {"type": "ir.actions.act_window_close"}


class BlistercenterAutoReleaseWizard(models.TransientModel):
    """Verfahren „automatische Freigabe“ (Regel BC-AUTO-1) durch die zuständige VP freigeben oder ausschalten."""

    _name = "blistercenter.auto.release.wizard"
    _description = "Automatische Freigabe (Verfahren)"

    config_id = fields.Many2one("blistercenter.config", string="Einstellungen", required=True, readonly=True)
    vp_user_id = fields.Many2one(related="config_id.vp_user_id")
    auto_release_enabled = fields.Boolean(related="config_id.auto_release_enabled")
    auto_release_approved_by = fields.Many2one(related="config_id.auto_release_approved_by")
    auto_release_approved_date = fields.Datetime(related="config_id.auto_release_approved_date")
    current_sop = fields.Char(related="config_id.auto_release_sop", string="Aktuelles Verfahren")
    sop = fields.Char(
        "Freigegebenes Verfahren (SOP, Version)",
        help="Verfahrensanweisung, in der die automatische Freigabe beschrieben und von der VP freigegeben ist.",
    )
    confirm = fields.Boolean(
        "Ich bestätige als zuständige VP, dass das Verfahren nach GDP bewertet und freigegeben ist. "
        "Meine übergeordnete Verantwortung bleibt bestehen.",
    )
    reason = fields.Char("Grund (Ausschalten)")

    @api.model
    def action_open(self):
        if not self.env.user.has_group("blistercenter.group_bc_approver"):
            raise AccessError(self.env._("Nur die Rolle „Blistercenter Freigabe“ (VP)."))
        config = self.env["blistercenter.config"]._get()
        wizard = self.create({"config_id": config.id, "sop": config.auto_release_sop})
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Automatische Freigabe (Verfahren)"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_enable(self):
        self.ensure_one()
        if not self.confirm:
            raise UserError(self.env._("Bitte die Freigabe des Verfahrens bestätigen."))
        self.config_id._bc_enable_auto_release(self.sop)
        return {"type": "ir.actions.act_window_close"}

    def action_disable(self):
        self.ensure_one()
        self.config_id._bc_disable_auto_release(self.reason)
        return {"type": "ir.actions.act_window_close"}
