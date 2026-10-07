# -*- coding: utf-8 -*-
{
    'name': "CRM Lead Feedback Summary",
    'summary': "Generate an AI-powered feedback summary on CRM leads",
    'description': """
        Adds a 'Generate Feedback Summary' button on CRM leads.
        It collects messages from the chatter (emails, notes, internal messages)
        and uses Mistral AI to produce a concise feedback summary stored on the lead.
    """,
    'author': "Captivea",
    'category': 'CRM',
    'version': '19.0.1.0.0',
    'depends': ['crm', 'connect_mistral_ai'],
    'data': [
        'security/ir.model.access.csv',
        'views/crm_lead_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
