from . import controllers
from . import models
from . import tools
from . import wizard


def _bc_post_init(env):
    env["blistercenter.config"]._bc_apply_website_branding()
    # Export 0 % automatisch, falls der deutsche Kontenrahmen schon geladen ist (sonst beim Laden)
    for company in env["res.company"].search([]):
        env["blistercenter.config"]._bc_setup_tax_positions(company)
