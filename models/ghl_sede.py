# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ghl_api_client import GHLApiClient, GHLApiError

_logger = logging.getLogger(__name__)


class GHLSede(models.Model):
    _name = "ghl.sede"
    _description = "Sede con reparto de citas GHL entre vendedores por turno"
    _order = "company_id, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Empresa",
        required=True,
        default=lambda self: self.env.company,
    )

    # --- Credenciales y calendario GHL de esta sede ---
    # Independiente de ghl.calendar.config: cada sede tiene su propio
    # calendario y, en general, su propio conjunto de credenciales GHL,
    # para no depender del modelo 1 config = 1 calendario = 1 usuario fijo
    # que ya está en producción.
    ghl_api_key = fields.Char(
        string="GHL API Key / Private Integration Token",
        required=True,
        groups="base.group_system",
    )
    ghl_location_id = fields.Char(string="GHL Location ID", required=True)
    ghl_calendar_id = fields.Char(
        string="GHL Calendar ID",
        required=True,
        help="ID del calendario de GHL correspondiente a esta sede.",
    )

    # --- Regla de reparto ---
    morning_cutoff_time = fields.Float(
        string="Hora de corte mañana/tarde",
        required=True,
        default=13.0,
        help="Hora (formato decimal, hora America/Guatemala) que separa el "
        "turno mañana del turno tarde para esta sede. Ej: 13.5 = 1:30 PM. "
        "Citas con hora de inicio antes de este valor son 'mañana'; a partir "
        "de este valor, 'tarde'.",
    )
    fallback_user_id = fields.Many2one(
        "res.users",
        string="Usuario de respaldo (fallback)",
        required=True,
        help="Usuario al que se asignan las citas de esta sede cuando ningún "
        "vendedor del turno correspondiente, ni el de cobertura, está activo.",
    )
    last_assigned_morning_id = fields.Many2one(
        "res.users",
        string="Último asignado (turno mañana)",
        readonly=True,
        copy=False,
        help="Puntero interno del round robin del turno mañana. No editar manualmente.",
    )
    last_assigned_afternoon_id = fields.Many2one(
        "res.users",
        string="Último asignado (turno tarde)",
        readonly=True,
        copy=False,
        help="Puntero interno del round robin del turno tarde. No editar manualmente.",
    )

    vendor_ids = fields.One2many("ghl.sede.vendor", "sede_id", string="Vendedores")
    vendor_count = fields.Integer(compute="_compute_vendor_count")

    # --- Control de sincronización (se usará a partir de la Fase 2) ---
    sync_window_days_past = fields.Integer(
        string="Días hacia atrás a sincronizar", default=7
    )
    sync_window_days_future = fields.Integer(
        string="Días hacia adelante a sincronizar", default=90
    )
    last_sync_datetime = fields.Datetime(string="Última sincronización exitosa")
    last_sync_log = fields.Text(string="Log de última corrida", readonly=True)
    last_sync_status = fields.Selection(
        [("ok", "OK"), ("error", "Error"), ("never", "Nunca ejecutado")],
        default="never",
        readonly=True,
    )

    _sql_constraints = [
        (
            "ghl_calendar_unique",
            "unique(ghl_calendar_id)",
            "Ya existe una sede configurada para este GHL Calendar ID.",
        ),
    ]

    @api.depends("vendor_ids.active")
    def _compute_vendor_count(self):
        for sede in self:
            sede.vendor_count = len(sede.vendor_ids.filtered("active"))

    def _get_client(self):
        self.ensure_one()
        return GHLApiClient(api_key=self.ghl_api_key, location_id=self.ghl_location_id)

    def action_test_connection(self):
        self.ensure_one()
        client = self._get_client()
        try:
            now = fields.Datetime.now()
            start_ms = int((now - timedelta(days=1)).timestamp() * 1000)
            end_ms = int((now + timedelta(days=1)).timestamp() * 1000)
            client.list_events(self.ghl_calendar_id, start_ms, end_ms)
        except GHLApiError as exc:
            raise UserError(f"Falló la conexión con GHL: {exc}") from exc
        raise UserError("Conexión exitosa con GHL.")  # usado como mensaje informativo
