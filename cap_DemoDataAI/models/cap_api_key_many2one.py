from odoo import fields, models

class ApiKey(models.Model):
    _name = "api.key"
    _description = "Mistral api keys"

    name = fields.Char(string="API Key")
