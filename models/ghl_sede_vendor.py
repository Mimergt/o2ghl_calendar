# -*- coding: utf-8 -*-
from odoo import fields, models


class GHLSedeVendor(models.Model):
    _name = "ghl.sede.vendor"
    _description = "Vendedor asignable a una sede GHL, con su turno"
    _order = "sede_id, sequence, id"

    sede_id = fields.Many2one(
        "ghl.sede",
        string="Sede",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Vendedor (usuario Odoo)",
        required=True,
    )
    shift = fields.Selection(
        [
            ("morning", "Mañana"),
            ("afternoon", "Tarde"),
            ("both", "Ambos"),
        ],
        string="Turno",
        required=True,
        default="both",
        help="Turno(s) en los que este vendedor participa del reparto. "
        "'Ambos' significa que entra en el round robin de mañana y también "
        "en el de tarde.",
    )
    active = fields.Boolean(
        default=True,
        help="Desmarcar para excluir temporalmente al vendedor del reparto "
        "(vacaciones, baja) sin borrar el registro.",
    )
    sequence = fields.Integer(
        default=10,
        help="Orden usado para desempatar en el round robin y para la "
        "cobertura entre turnos.",
    )
    company_id = fields.Many2one(
        related="sede_id.company_id",
        string="Empresa",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "sede_user_unique",
            "unique(sede_id, user_id)",
            "Este vendedor ya está asignado a esta sede.",
        ),
    ]
