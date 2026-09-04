# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class GHLSedeVendor(models.Model):
    _name = "ghl.sede.vendor"
    _description = "Vendedor asignable a una sede GHL, con sus turnos"
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
    works_morning = fields.Boolean(
        string="Mañana",
        default=True,
        help="Este vendedor participa del round robin del turno mañana.",
    )
    works_afternoon = fields.Boolean(
        string="Tarde",
        default=True,
        help="Este vendedor participa del round robin del turno tarde.",
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

    @api.constrains("works_morning", "works_afternoon")
    def _check_at_least_one_shift(self):
        for vendor in self:
            if not vendor.works_morning and not vendor.works_afternoon:
                raise ValidationError(
                    "%s debe participar en al menos un turno (Mañana o Tarde)."
                    % vendor.user_id.name
                )
