"""1.3.0: Arbeitsbereiche Bestellannahme / Einkauf / Lager.

Bisherige Blistercenter-Benutzer sahen alle Menüs. Damit sich für sie nichts ändert, bekommen
sie alle drei Arbeitsbereiche. Die Administration nimmt danach überflüssige Bereiche weg.
Freigabe und Administrator sehen ohnehin alles.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    user_group = env.ref("blistercenter.group_bc_user")
    approver = env.ref("blistercenter.group_bc_approver")
    roles = env.ref("blistercenter.group_bc_sales") | env.ref("blistercenter.group_bc_purchase") \
        | env.ref("blistercenter.group_bc_stock")
    users = env["res.users"].with_context(active_test=False).search([("all_group_ids", "in", user_group.ids)])
    users = users.filtered(lambda u: approver not in u.all_group_ids and not (roles & u.group_ids))
    for user in users:
        user.group_ids |= roles
    _logger.info("Blistercenter 1.3.0: %s Benutzer bekommen alle drei Arbeitsbereiche: %s",
                 len(users), ", ".join(users.mapped("login")))
