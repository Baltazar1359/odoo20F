from odoo import fields, models
from odoo.exceptions import UserError

from ..tools import grx_auth


class StockPicking(models.Model):
    _inherit = "stock.picking"

    plcs_grx_ticket_id = fields.Many2one("plcs.greenrx.ticket", "GreenRX-Ticket", index="btree_not_null", copy=False, readonly=True)
    plcs_grx_reference = fields.Char(related="plcs_grx_ticket_id.name", string="Referenznummer")
    plcs_grx_ticket_number = fields.Char(related="plcs_grx_ticket_id.ticket_numbers", string="Ticketnummern")
    plcs_grx_complaint_number = fields.Char(related="plcs_grx_ticket_id.complaint_number", string="Reklamationsnummer")
    plcs_grx_credit_note = fields.Char(related="plcs_grx_ticket_id.credit_note", string="Gutschriftnummer")
    plcs_grx_box_ids = fields.One2many("plcs.greenrx.box", "picking_id", "GreenRX-Boxen")
    plcs_grx_release_box_id = fields.Many2one("plcs.greenrx.box", "Freigabe/Sperrung für Box", copy=False, readonly=True)

    PLCS_GRX_LOCKED_FIELDS = ("plcs_grx_ticket_id", "plcs_grx_release_box_id")

    def write(self, vals):
        # Die Verknüpfung mit Vorgang/Box setzt nur der Modulcode beim Anlegen. Sonst ließe sich
        # ein GreenRX-Eingang durch Lösen der Verknüpfung an „Paket fertig“ vorbei buchen.
        if not self.env.su and any(f in vals for f in self.PLCS_GRX_LOCKED_FIELDS):
            for picking in self:
                for fname in self.PLCS_GRX_LOCKED_FIELDS:
                    if fname in vals and picking[fname].id != (vals[fname] or False):
                        raise UserError(self.env._(
                            "Die GreenRX-Verknüpfung von %s kann nicht geändert werden.", picking.name))
        return super().write(vals)

    def _action_done(self):
        self._plcs_grx_check_receipt_not_booked_early()
        return super()._action_done()

    def _plcs_grx_check_receipt_not_booked_early(self):
        """Ein GreenRX-Eingang wird nur über „Paket fertig“ gebucht.

        Sonst kann der Eingang gebucht sein, während noch Boxen erfasst werden
        (Transfers › Bestätigen, Barcode-App, direkte RPC-Aufrufe). Ausgenommen ist nur die
        Freigabe aus grx_finish (grx_auth.Finish) für genau diesen Vorgang; ein Kontextwert
        aus einem RPC-Aufruf reicht nicht (Code-Review R01).
        """
        auth = grx_auth.finish(self.env)
        for picking in self:
            ticket = picking.plcs_grx_ticket_id
            if auth and ticket.id == auth.ticket_id:
                continue
            if ticket and ticket.state != "done" and picking.picking_type_id == ticket.company_id.plcs_grx_receipt_type_id:
                raise UserError(self.env._(
                    "Der GreenRX-Eingang %(p)s wird nicht hier gebucht, sondern in GreenRX › Wareneingang scannen "
                    "mit „Paket fertig“ (Vorgang %(t)s). So sind alle Boxen vorher abgeschlossen.",
                    p=picking.name, t=ticket.name))
