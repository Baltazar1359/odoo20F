from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    plcs_grx_min_shelf_months = fields.Integer(related="company_id.plcs_grx_min_shelf_months", readonly=False)
    plcs_grx_allow_heuristic = fields.Boolean(related="company_id.plcs_grx_allow_heuristic", readonly=False)
    plcs_grx_create_from_slip = fields.Boolean(related="company_id.plcs_grx_create_from_slip", readonly=False)
    plcs_grx_box_prefix = fields.Char(related="company_id.plcs_grx_box_prefix", readonly=False)
    plcs_grx_box_max = fields.Integer(related="company_id.plcs_grx_box_max", readonly=False)
    plcs_grx_quarantine_location_id = fields.Many2one(related="company_id.plcs_grx_quarantine_location_id", readonly=False)
    plcs_grx_stock_location_id = fields.Many2one(related="company_id.plcs_grx_stock_location_id", readonly=False)
    plcs_grx_blocked_location_id = fields.Many2one(related="company_id.plcs_grx_blocked_location_id", readonly=False)
    plcs_grx_receipt_type_id = fields.Many2one(related="company_id.plcs_grx_receipt_type_id", readonly=False)
    plcs_grx_release_type_id = fields.Many2one(related="company_id.plcs_grx_release_type_id", readonly=False)
    plcs_grx_block_type_id = fields.Many2one(related="company_id.plcs_grx_block_type_id", readonly=False)

    def action_plcs_grx_create_boxes(self):
        self.env["stock.package"].plcs_grx_ensure_boxes(self.company_id)
        return True
