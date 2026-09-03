# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ghl_contact_id = fields.Char(
        string="GHL Contact ID",
        copy=False,
        index=True,
        help="ID del contacto correspondiente en GoHighLevel.",
    )

    _sql_constraints = [
        (
            "ghl_contact_id_unique",
            "unique(ghl_contact_id)",
            "Ya existe otro contacto vinculado a este GHL Contact ID.",
        ),
    ]
