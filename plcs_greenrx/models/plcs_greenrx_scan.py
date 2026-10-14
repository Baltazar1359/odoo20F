from odoo import fields, models


class PlcsGreenrxScan(models.Model):
    """Protokoll jedes Scans im GreenRX-Eingang – auch abgelehnte Scans."""

    _name = "plcs.greenrx.scan"
    _description = "GreenRX: Scan-Protokoll"
    _order = "id desc"

    box_id = fields.Many2one("plcs.greenrx.box", "Box-Erfassung", required=True, ondelete="cascade", index=True)
    ticket_id = fields.Many2one(related="box_id.ticket_id", store=True, string="Ticket")
    user_id = fields.Many2one("res.users", "Benutzer", default=lambda self: self.env.user, readonly=True)
    raw = fields.Char("Rohdaten", readonly=True)
    code_format = fields.Selection([("gs1", "GS1"), ("ifa", "IFA"), ("manual", "Handeingabe")], "Format", readonly=True)
    manual = fields.Boolean("Von Hand eingegeben", readonly=True, index=True,
                            help="Packungsdaten getippt, weil der Code nicht lesbar war.")
    gtin = fields.Char("GTIN", readonly=True)
    pzn = fields.Char("PZN", readonly=True)
    serial = fields.Char("Seriennummer", readonly=True, index=True)
    batch = fields.Char("Charge", readonly=True)
    expiry_date = fields.Date("Verfall", readonly=True)
    product_id = fields.Many2one("product.product", "Artikel", readonly=True, index=True)
    lot_id = fields.Many2one("stock.lot", "Seriennummer (Odoo)", readonly=True)
    accepted = fields.Boolean("In der Box", readonly=True, index=True)
    result = fields.Selection([
        ("ok", "Erfasst"),
        ("duplicate_box", "Doppelscan"),
        ("known_serial", "Seriennummer schon im System"),
        ("wrong_product", "Anderer Artikel"),
        ("wrong_batch", "Andere Charge / anderer Verfall"),
        ("unknown_product", "Artikel unbekannt"),
        ("incomplete", "Code unvollständig"),
        ("parse_error", "Code nicht lesbar"),
        ("box_code", "Box-Code statt Packung"),
        ("removed", "Wieder entnommen"),
    ], "Ergebnis", readonly=True, index=True)
    message = fields.Char("Meldung", readonly=True)
