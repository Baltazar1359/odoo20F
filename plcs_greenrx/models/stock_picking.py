from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    plcs_grx_ticket_id = fields.Many2one("plcs.greenrx.ticket", "GreenRX-Ticket", index="btree_not_null", copy=False, readonly=True)
    plcs_grx_reference = fields.Char(related="plcs_grx_ticket_id.name", string="Referenznummer")
    plcs_grx_ticket_number = fields.Char(related="plcs_grx_ticket_id.ticket_numbers", string="Ticketnummern")
    plcs_grx_complaint_number = fields.Char(related="plcs_grx_ticket_id.complaint_number", string="Reklamationsnummer")
    plcs_grx_credit_note = fields.Char(related="plcs_grx_ticket_id.credit_note", string="Gutschriftnummer")
    plcs_grx_box_ids = fields.One2many("plcs.greenrx.box", "picking_id", "GreenRX-Boxen")
    plcs_grx_release_box_id = fields.Many2one("plcs.greenrx.box", "Freigabe/Sperrung für Box", copy=False, readonly=True)
