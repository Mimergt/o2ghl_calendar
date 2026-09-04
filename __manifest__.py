# -*- coding: utf-8 -*-
{
    "name": "GHL2ODOO",
    "version": "18.0.1.5.0",
    "summary": "Reparto y sincronización de citas GoHighLevel <-> Odoo por sede",
    "description": """
GHL2ODOO
==========================
Sincroniza citas (calendar.event) entre Odoo y calendarios de GoHighLevel
(GHL), con reparto automático entre vendedores por sede y turno.

Características:
-----------------
* Una sede (ghl.sede) por calendario de GHL, con sus propias credenciales.
* Reparto automático (round robin) entre los vendedores activos del turno
  correspondiente, con cobertura cruzada y usuario de respaldo (fallback).
* Cron periódico que trae las citas desde GHL y las aplica en Odoo,
  asignando organizador e invitados según el vendedor calculado.
* Auto-vinculación/creación de res.partner <-> contacto GHL por email/teléfono.
* Prevención de loops de sincronización mediante contexto interno.

Desarrollado por Mimer (EPIC.GT).
    """,
    "category": "Calendar",
    "author": "Mimer - EPIC.GT",
    "website": "https://epic.gt",
    "license": "LGPL-3",
    "depends": ["calendar", "contacts", "mail"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/ghl_sede_views.xml",
        "views/calendar_event_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
