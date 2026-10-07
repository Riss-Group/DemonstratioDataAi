from odoo import fields, models


class WizardCreateCompany(models.TransientModel):
    _name = 'demo.data.wizard'
    _description = "Wizard to create company"

    name = fields.Char(string="Company Name", required=True,
                       help="The name of the company which you want to generate data for")
    email = fields.Char(string="Email", help="The email that will be defined on creation of the company")
    city = fields.Char(string="City", required=True,
                       help="The company made will be based in this city. Its customers and suppliers may also be in this city or around")
    logo = fields.Binary(string="Company Logo", readonly=False)
    country_id = fields.Many2one('res.country', string='Country',
                                 help="The company created will be based in this country and its customers will most likely be based there as well",
                                 required=True)
    demo_data_id = fields.Many2one('demo.data', required=True)
    def create_company(self):
        company_id = self.env['res.company'].create({
            "name": self.name,
            "country_id": self.country_id.id,
            "email": self.email,
            "logo": self.logo,
            "active": True
        }).id
        self.env.user.company_id = company_id
        self.env.context.get('active_id')
        self.demo_data_id.is_company_created = True
        if self.country_id.code == "US" or not self.env['ir.module.module'].search([('name', '=', 'l10n_'+ self.country_id.code.lower())]):
            self.env["account.chart.template"].try_loading(
                "generic_coa", company=company_id
            )
            
        for var in ['name', 'city', 'email', 'logo']:
            if self[var] !=  self.demo_data_id[var]:
                self.demo_data_id[var] = self[var]
        self.demo_data_id._compute_data_path()
