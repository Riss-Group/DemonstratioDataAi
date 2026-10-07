# -*- coding: utf-8 -*-
{
    'name': "Mistral AI Connector",

    'summary': """
        Mistral AI Connector for Odoo 19
    """,

    'description': """
        This module integrates Mistral AI capabilities into Odoo 19, enabling advanced AI functionalities.
    """,

    'author': "Captivea",
    'category': 'Demo/',
    'version': '19.0.1.0',
    'application': True,
    'installable': True,
    'depends': ['ai_app'],
    "external_dependencies": {"python": ['mistralai']},
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'license': 'LGPL-3'
}
