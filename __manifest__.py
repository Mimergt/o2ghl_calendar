# -*- coding: utf-8 -*-
{
    "name": "GoHighLevel Calendar Sync",
    "version": "18.0.1.1.0",
    "summary": "Sincronización bidireccional entre el Calendario de Odoo y GoHighLevel (GHL)",
    "description": """
GoHighLevel Calendar Sync
==========================
Sincroniza citas (calendar.event) entre Odoo y un calendario específico de
GoHighLevel (GHL) de forma bidireccional, vía polling (cron).

Características:
-----------------
* Configuración de conexión a GHL (API Key / Location ID) y mapeo de
  calendarios Odoo <-> GHL.
* Cron periódico que:
    1. Trae cambios de GHL (creados/actualizados/borrados) y los aplica en Odoo.
    2. Empuja cambios de Odoo (creados/actualizados/borrados) hacia GHL.
* En conflicto (editado en ambos lados), GHL es la fuente de verdad.
* Auto-vinculación/creación de res.partner <-> contacto GHL por email/teléfono.
* Prevención de loops de sincronización mediante contexto interno.
    """,
    "category": "Calendar",
    "author": "Custom Development",
    "license": "LGPL-3",
    "depends": ["calendar", "contacts", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/ghl_calendar_config_views.xml",
        "views/calendar_event_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
