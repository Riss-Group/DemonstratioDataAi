import odoo
from odoo import fields, models
from odoo.exceptions import UserError


class WizardCreateCompany(models.TransientModel):
    _name = 'demo.data.wizard'
    _description = "Wizard to create company"

    name = fields.Char(string="Company Name", required=True,
                       help="The name of the company which you want to generate data for")
    city = fields.Char(string="City", help="The city that will be defined on creation of the company")
    website_url = fields.Char(string="Website", help="The website that will be defined on creation of the company")
    email = fields.Char(string="Email", help="The email that will be defined on creation of the company")
    logo = fields.Binary(string="Company Logo", readonly=False)
    country_id = fields.Many2one('res.country', string='Country',
                                 help="The company created will be based in this country and its customers will most likely be based there as well",
                                 required=True)
    demo_data_id = fields.Many2one('demo.data', required=True)

    def create_company(self):
        company = self.env['res.company'].create({
            "name": self.name,
            "city": self.city,
            "country_id": self.country_id.id,
            "website": self.website_url,
            "email": self.email,
            "logo": self.logo,
            "active": True,
        })
        self.env.user.write({'company_id': company.id})

        country_code = self.country_id.code
        if country_code == "US" or not self.env['ir.module.module'].search(
                [('name', '=', 'l10n_' + country_code.lower())]):
            self.env["account.chart.template"].try_loading("generic_coa", company=company)

        demo_data_vals = {var: self[var] for var in ('name', 'website_url', 'city', 'email', 'logo') if
                          self[var] != self.demo_data_id[var]}
        demo_data_vals.update({'is_company_created': True, 'company_id': company.id})
        self.demo_data_id.write(demo_data_vals)

        if not self.demo_data_id.data_path:
            self.demo_data_id._compute_data_path()

        company.write({'external_report_layout_id': self.env.ref("web.external_layout_folder").id})

        mods = self.env['ir.module.module'].search([('state', 'in', ['to upgrade', 'to install'])])
        if mods:
            query = """
                SELECT d.name
                FROM ir_module_module m
                JOIN ir_module_module_dependency d ON (m.id = d.module_id)
                LEFT JOIN ir_module_module m2 ON (d.name = m2.name)
                WHERE m.id = any(%s) and (m2.state IS NULL or m2.state = %s)
            """
            self.env.cr.execute(query, (mods.ids, 'uninstalled'))
            unmet_packages = [row[0] for row in self.env.cr.fetchall()]
            if unmet_packages:
                raise UserError(self.env._('The following modules are not installed or unknown: %s',
                                           '\n\n' + '\n'.join(unmet_packages)))

        self.env.cr.commit()
        odoo.modules.registry.Registry.new(self.env.cr.dbname, update_module=True)
        self.env.cr.reset()
