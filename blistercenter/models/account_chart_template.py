from odoo import models


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    def _post_load_data(self, template_code, company, template_data):
        res = super()._post_load_data(template_code, company, template_data)
        # Deutscher Kontenrahmen: Export automatisch 0 % (Entscheidung 21.10.2026)
        self.env["blistercenter.config"]._bc_setup_tax_positions(company or self.env.company)
        return res
