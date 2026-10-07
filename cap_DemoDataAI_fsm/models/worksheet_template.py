from odoo import models, fields, api


class WorksheetTemplate(models.Model):
    _inherit = 'worksheet.template'

    @api.model
    def _create_demo_data_fsm(self, model_id=False):
        # create demo data in batch for performance reasons (avoid multiple calls to _setup_models__)
        if not model_id:
            model_id = self.env.ref('industry_fsm_report.fsm_worksheet_template2').model_id.id
        self.env['ir.model.fields'].with_context(_import_current_module='industry_fsm_report').create([{
            'id': 'fsm_template_field1',
            'name': 'x_service_category',
            'ttype': 'selection',
            'field_description': 'Service Category',
            'selection': "[('installation', 'Installation'),('maintenance', 'Maintenance'),('repair', 'Repair'),('inspection', 'Inspection'),('support', 'Support'),('upgrade', 'Upgrade')]",
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field2',
            'name': 'x_work_description',
            'ttype': 'text',
            'field_description': 'Work Description',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field3',
            'name': 'x_work_performed',
            'ttype': 'text',
            'field_description': 'Work Performed',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field4',
            'name': 'x_equipment_details',
            'ttype': 'text',
            'field_description': 'Equipment/Asset Details',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field5',
            'name': 'x_condition_before_service',
            'ttype': 'text',
            'field_description': 'Condition Before Service',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field6',
            'name': 'x_condition_after_service',
            'ttype': 'text',
            'field_description': 'Condition After Service',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field7',
            'name': 'x_date',
            'ttype': 'date',
            'field_description': 'Date',
            'model_id': model_id,
        }, {
            'id': 'fsm_template_field8',
            'name': 'x_time_spent',
            'ttype': 'float',
            'field_description': 'Time Spent(Hours)',
            'model_id': model_id,
        },{
            'id': 'fsm_template_field9',
            'name': 'x_customer_feedback',
            'ttype': 'text',
            'field_description': 'Customer Feedback/Remarks',
            'model_id': model_id,
        },{
            'id': 'fsm_template_field10',
            'name': 'x_customer_signature',
            'ttype': 'binary',
            'field_description': 'Customer Signature',
            'model_id': model_id,
        }])