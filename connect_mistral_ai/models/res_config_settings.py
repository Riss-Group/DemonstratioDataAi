from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    mistral_key_enabled = fields.Boolean(
        string="Enable custom Mistral API key",
        compute='_compute_mistral_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    mistral_key = fields.Char(
        string="Mistral API key",
        config_parameter='connect_mistral_ai.mistral_key',
        readonly=False,
        groups='base.group_system',
    )

    def _compute_mistral_key_enabled(self):
        for record in self:
            record.mistral_key_enabled = bool(record.mistral_key)