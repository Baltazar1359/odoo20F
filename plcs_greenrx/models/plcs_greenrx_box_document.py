import hashlib

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.binary import BinaryBytes

DOC_MODEL = "plcs.greenrx.box.document"
DOC_STATES = [
    ("open", "vor Abschluss"),
    ("valid", "gültig"),
    ("replaced", "ersetzt"),
    ("copy", "Nachdruck nach Abschluss"),
    ("void", "verworfen (Erfassung abgebrochen)"),
]
# Erlaubte Statuswechsel (nur Modulcode)
DOC_TRANSITIONS = {
    "open": {"valid", "replaced", "void"},
}


class PlcsGreenrxBoxDocument(models.Model):
    """Gespeichertes Formblatt (PDF) eines Drucks.

    Jeder Druck in grx_print erzeugt genau einen Datensatz. Das PDF ist die digitale Kopie
    des ausgedruckten Formblatts, das in die Box gelegt wird. Es hängt an der Belegung
    (plcs.greenrx.box), nicht an der physischen Box GX…, und ist für alle Benutzer
    unveränderbar und nicht löschbar.
    """

    _name = DOC_MODEL
    _description = "GreenRX: Formblatt (PDF eines Drucks)"
    _order = "box_id, sequence_valid, print_no desc"
    _rec_name = "file_name"

    box_id = fields.Many2one("plcs.greenrx.box", "Belegung", required=True, readonly=True, index=True,
                             ondelete="restrict")
    company_id = fields.Many2one(related="box_id.company_id", store=True)
    package_id = fields.Many2one(related="box_id.package_id", string="Box", store=True)
    ticket_id = fields.Many2one(related="box_id.ticket_id", string="Vorgang", store=True)
    print_no = fields.Integer("Druck-Nr.", required=True, readonly=True)
    reprint = fields.Boolean("Nachdruck", readonly=True, help="Jeder Druck nach dem ersten ist ein Nachdruck.")
    printed_at = fields.Datetime("Gedruckt am", required=True, readonly=True)
    printed_by = fields.Many2one("res.users", "Gedruckt von", required=True, readonly=True)
    printed_by_name = fields.Char("Name (beim Druck)", readonly=True,
                                  help="Name des Benutzers zum Zeitpunkt des Drucks.")
    content_rev = fields.Integer("Stand der Box", readonly=True,
                                 help="Änderungsstand der Box beim Druck. Gültig ist der Druck, dessen Stand "
                                      "beim Abschluss zum Inhalt passt.")
    after_close = fields.Boolean("Nach Abschluss gedruckt", readonly=True)
    state = fields.Selection(DOC_STATES, "Status", required=True, readonly=True, default="open", index=True)
    sequence_valid = fields.Integer(compute="_compute_sequence_valid", store=True)
    file_name = fields.Char("Dateiname", required=True, readonly=True)
    pdf_file = fields.Binary("Formblatt", attachment=True, readonly=True)
    mimetype = fields.Char("Dateityp", readonly=True)
    checksum = fields.Char("Prüfsumme (SHA-256)", readonly=True,
                           help="Prüfsumme der Datei beim Speichern. Zeigt, dass die Datei unverändert ist.")

    @api.depends("state")
    def _compute_sequence_valid(self):
        order = {"valid": 0, "open": 1, "copy": 2, "replaced": 3, "void": 4}
        for doc in self:
            doc.sequence_valid = order.get(doc.state, 9)

    # ------------------------------------------------------------------
    # Unveränderbar
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise UserError(self.env._("Formblätter entstehen nur beim Drucken und können nicht von Hand angelegt werden."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            raise UserError(self.env._("Gespeicherte Formblätter können nicht geändert werden."))
        # Auch der Modulcode darf nur den Status weiterschalten (vor Abschluss → gültig/ersetzt/verworfen)
        if set(vals) - {"state", "sequence_valid"}:
            raise UserError(self.env._("Gespeicherte Formblätter können nicht geändert werden."))
        new_state = vals.get("state")
        for doc in self:
            if new_state and new_state != doc.state and new_state not in DOC_TRANSITIONS.get(doc.state, ()):
                raise UserError(self.env._("Formblatt %(f)s: Status „%(a)s“ kann nicht mehr geändert werden.",
                                           f=doc.file_name, a=dict(DOC_STATES)[doc.state]))
            if doc.box_id.archived:
                raise UserError(self.env._("Die Belegung %s ist archiviert. Ihre Formblätter sind unveränderbar.",
                                           doc.box_id.name))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("force_delete"):
            raise UserError(self.env._("Gespeicherte Formblätter können nicht gelöscht werden."))
        return super().unlink()

    # ------------------------------------------------------------------
    @api.model
    def _grx_store(self, box, content, ftype, after_close):
        """PDF eines Drucks speichern (nur aus grx_print)."""
        tz_printed = fields.Datetime.context_timestamp(box, box.printed_at)
        ext = "pdf" if ftype == "pdf" else "html"
        file_name = "%s_%s_%s_Druck%s.%s" % (
            box.package_id.name, (box.ticket_id.name or "").replace("/", "-").replace(" ", ""),
            tz_printed.strftime("%Y-%m-%d_%H%M"), box.print_count, ext)
        if isinstance(content, str):
            content = content.encode()
        return self.sudo().create({
            "box_id": box.id,
            "print_no": box.print_count,
            "reprint": box.print_count > 1,
            "printed_at": box.printed_at,
            "printed_by": box.printed_by.id,
            "printed_by_name": box.printed_by.name,
            "content_rev": box.printed_rev,
            "after_close": after_close,
            "state": "copy" if after_close else "open",
            "file_name": file_name,
            "pdf_file": BinaryBytes(content, filename=file_name),
            "mimetype": "application/pdf" if ext == "pdf" else "text/html",
            "checksum": hashlib.sha256(content).hexdigest(),
        })

    def _grx_url(self, download=False):
        self.ensure_one()
        return "/web/content/%s/%s/pdf_file/%s?download=%s" % (
            self._name, self.id, self.file_name, "true" if download else "false")

    def action_open(self):
        self.ensure_one()
        self.check_access("read")
        return {"type": "ir.actions.act_url", "url": self._grx_url(), "target": "new"}

    def action_download(self):
        self.ensure_one()
        self.check_access("read")
        return {"type": "ir.actions.act_url", "url": self._grx_url(download=True), "target": "new"}


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _plcs_grx_protected(self):
        return self.sudo().filtered(lambda a: a.res_model == DOC_MODEL)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(v.get("res_model") == DOC_MODEL for v in vals_list):
            raise UserError(self.env._("An gespeicherte Formblätter können keine Dateien angehängt werden."))
        return super().create(vals_list)

    def write(self, vals):
        if self._plcs_grx_protected() and not self.env.context.get("force_delete"):
            raise UserError(self.env._("Gespeicherte Formblätter können nicht geändert oder ersetzt werden."))
        return super().write(vals)

    def unlink(self):
        if self._plcs_grx_protected() and not self.env.context.get("force_delete"):
            raise UserError(self.env._("Gespeicherte Formblätter können nicht gelöscht werden."))
        return super().unlink()
