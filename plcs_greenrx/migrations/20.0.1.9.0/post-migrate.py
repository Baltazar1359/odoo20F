import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Belegungen, von denen schon keine Packung mehr im Lager liegt, archivieren.

    Grundlage ist der tatsächliche Lagerbestand. Formblatt-PDFs werden für Boxen von vor 1.9.0
    nicht nachträglich erzeugt (keine Daten erfinden).
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    boxes = env["plcs.greenrx.box"].search([("state", "in", ("released", "blocked")), ("archived", "=", False)])
    archived = boxes._grx_try_archive()
    _logger.info("plcs_greenrx 1.9.0: archiviert %s", ", ".join(archived.mapped("name")) or "keine Belegung")
