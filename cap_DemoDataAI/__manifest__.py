# -*- coding: utf-8 -*-
{
    'name': "Demo Data AI Generation",

    'summary': """
        Generate demonstration data specific to clients
    """,

    'description': """
        Uses Mistral API to generate demonstration data corresponding to the client entered.
    """,

    'author': "Nadal",
    'category': 'Demo/',
    'version': '18.0.1.1',
    'application': True,
    'installable': True,
    'depends': ['base', 'product', 'account', 'web_studio', 'documents', 'knowledge'],
    "external_dependencies": {"python": ['mistralai']},
    'images':['static/description/icon.png'],

    'data': [
        'security/ir.model.access.csv',
        'views/cap_demo_data_view.xml',
        'views/cap_demo_data_form_view.xml',
        'views/cap_demo_data_menu.xml',
        'views/cap_module_load_form_view.xml',
        'wizard/cap_wiz_create_company.xml',
        'data/ir_cron_data.xml'
    ],
    'license': 'LGPL-3'
}
