# -*- coding: utf-8 -*-
{
    "name": "Mistral AI Connector",

    "summary": """
        Mistral AI Connector for Odoo 19
    """,

    "description": """
        This module integrates Mistral AI capabilities into Odoo 19, enabling advanced AI functionalities.
    """,

    "version": "19.0.1.1",
    "category": "General",
    "author":"Captivea",
    "maintainer": "Captivea",
    "contributors": ["Captivea"],
    "website":"https://www.captivea.com/",
    "images": ['static/description/Mistral AI Connector.gif', 'static/description/icon.png'],
    "depends": ['ai', 'ai_app'],
    "external_dependencies": {"python": ['mistralai']},
    "data": [
        'views/res_config_settings_views.xml',
    ],
    "assets": {
        'web.assets_backend': [
            'connect_mistral_ai/static/src/vad_audio_recorder_patch.js',
        ],
    },
    "license": "OPL-1",
    "price": 60.0,
    "currency": "USD",
    "auto_install": False,
    "application": True,
    "installable": True, 
}
