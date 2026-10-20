from . import models


def _plcs_layout_post_init(env):
    """„PLCS“ wird das Dokumentlayout aller Firmen (Entscheidung Mehrdad 20.10.2026: alle Belege)."""
    env["res.company"].search([])._plcs_apply_layout()
