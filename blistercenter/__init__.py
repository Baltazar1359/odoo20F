from . import controllers
from . import models
from . import tools
from . import wizard


def _bc_post_init(env):
    env["blistercenter.config"]._bc_apply_website_branding()
