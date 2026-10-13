from odoo import fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    plcs_grx_ticket_id = fields.Many2one("plcs.greenrx.ticket", "GreenRX-Ticket", index="btree_not_null", copy=False, readonly=True)
    plcs_grx_reference = fields.Char(related="plcs_grx_ticket_id.name", string="Referenznummer")
    plcs_grx_ticket_number = fields.Char(related="plcs_grx_ticket_id.ticket_numbers", string="Ticketnummern")
    plcs_grx_complaint_number = fields.Char(related="plcs_grx_ticket_id.complaint_number", string="Reklamationsnummer")
    plcs_grx_credit_note = fields.Char(related="plcs_grx_ticket_id.credit_note", string="Gutschriftnummer")
    plcs_grx_box_ids = fields.One2many("plcs.greenrx.box", "picking_id", "GreenRX-Boxen")
    plcs_grx_release_box_id = fields.Many2one("plcs.greenrx.box", "Freigabe/Sperrung für Box", copy=False, readonly=True)

    def _action_done(self):
        if not self.env.context.get("plcs_grx_finish"):
            self._plcs_grx_check_receipt_not_booked_early()
        return super()._action_done()

    def _plcs_grx_check_receipt_not_booked_early(self):
        """Ein GreenRX-Eingang wird nur über „Paket fertig“ gebucht.

        Sonst kann der Eingang gebucht sein, während noch Boxen erfasst werden
        (Transfers › Bestätigen, Barcode-App).
        """
        for picking in self:
            ticket = picking.plcs_grx_ticket_id
            if ticket and ticket.state != "done" and picking.picking_type_id == ticket.company_id.plcs_grx_receipt_type_id:
                raise UserError(self.env._(
                    "Der GreenRX-Eingang %(p)s wird nicht hier gebucht, sondern in GreenRX › Wareneingang scannen "
                    "mit „Paket fertig“ (Vorgang %(t)s). So sind alle Boxen vorher abgeschlossen.",
                    p=picking.name, t=ticket.name))
