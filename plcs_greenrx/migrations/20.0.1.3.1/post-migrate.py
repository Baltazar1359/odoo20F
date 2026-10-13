import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Ausdrücke offener Boxen aus der Zeit vor 1.3.0 ungültig machen: neu drucken ist Pflicht."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    boxes = env["plcs.greenrx.box"]._grx_invalidate_legacy_prints()
    _logger.info("plcs_greenrx 1.3.1: Ausdruck ungültig gemacht für %s", ", ".join(boxes.mapped("name")) or "keine Box")
