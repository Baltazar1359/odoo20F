from odoo import api, fields, models

OPEN_STATES = ("scan", "check", "print")


class StockPackage(models.Model):
    _inherit = "stock.package"

    plcs_is_grx_box = fields.Boolean("GreenRX-Box", index=True, copy=False)
    plcs_grx_box_ids = fields.One2many("plcs.greenrx.box", "package_id", "Box-Erfassungen")
    plcs_grx_current_box_id = fields.Many2one(
        "plcs.greenrx.box", "Aktuelle Erfassung", compute="_compute_plcs_grx_current_box")
    plcs_grx_is_empty = fields.Boolean("Box leer", compute="_compute_plcs_grx_is_empty")

    @api.depends("plcs_grx_box_ids.state")
    def _compute_plcs_grx_current_box(self):
        for package in self:
            package.plcs_grx_current_box_id = package.plcs_grx_box_ids.filtered(
                lambda b: b.state != "cancel").sorted("id", reverse=True)[:1]

    @api.depends("quant_ids.quantity", "quant_ids.location_id", "plcs_grx_box_ids.state")
    def _compute_plcs_grx_is_empty(self):
        for package in self:
            package.plcs_grx_is_empty = not package._plcs_grx_occupied_reason()

    def _plcs_grx_occupied_reason(self, ignore_box=None):
        """Warum die Box nicht leer ist (Text) oder False."""
        self.ensure_one()
        quants = self.sudo().quant_ids.filtered(
            lambda q: q.location_id.usage == "internal" and q.quantity > 0)
        if quants:
            return self.env._("In der Box liegen noch %(n)s Packungen (%(loc)s).",
                              n=int(sum(quants.mapped("quantity"))),
                              loc=", ".join(quants.location_id.mapped("display_name")))
        boxes = self.sudo().plcs_grx_box_ids.filtered(lambda b: b != ignore_box)
        open_box = boxes.filtered(lambda b: b.state in OPEN_STATES)
        if open_box:
            return self.env._("Die Box wird gerade in %(box)s befüllt.", box=open_box[0].display_name)
        # abgeschlossen, aber der Eingang ist noch nicht gebucht
        pending = boxes.filtered(
            lambda b: b.state in ("to_release", "blocked") and b.picking_id.state not in ("done", "cancel"))
        if pending:
            return self.env._("Die Box gehört zum noch offenen Eingang %(p)s.", p=pending[0].picking_id.name)
        return False

    @api.model
    def plcs_grx_ensure_boxes(self, company=None):
        """Boxen GX001..GXnnn anlegen, falls sie fehlen."""
        company = company or self.env.company
        prefix = company.plcs_grx_box_prefix or "GX"
        maximum = company.plcs_grx_box_max or 200
        names = ["%s%03d" % (prefix, i) for i in range(1, maximum + 1)]
        existing = self.search([("name", "in", names)])
        existing.filtered(lambda p: not p.plcs_is_grx_box).write({"plcs_is_grx_box": True})
        missing = sorted(set(names) - set(existing.mapped("name")))
        if missing:
            self.create([{"name": n, "plcs_is_grx_box": True, "company_id": company.id} for n in missing])
        return len(missing)
