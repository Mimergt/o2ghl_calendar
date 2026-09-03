# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from .ghl_api_client import GHLApiClient, GHLApiError

_logger = logging.getLogger(__name__)


class GHLCalendarConfig(models.Model):
    _name = "ghl.calendar.config"
    _description = "Configuración de sincronización de Calendario GHL <-> Odoo"

    name = fields.Char(required=True, default="Sincronización GHL")
    active = fields.Boolean(default=True)

    sync_mode = fields.Selection(
        [
            ("bidirectional", "Bidireccional (Odoo ↔ GHL, incluye borrados)"),
            ("odoo_to_ghl", "Solo Odoo → GHL (Odoo es la fuente de verdad)"),
            ("ghl_to_odoo", "Solo GHL → Odoo (GHL es la fuente de verdad)"),
        ],
        string="Modo de sincronización",
        default="bidirectional",
        required=True,
        help="Bidireccional: cambios y borrados se propagan en ambos sentidos, "
        "GHL gana en caso de conflicto.\n"
        "Solo Odoo → GHL: los cambios en Odoo se empujan a GHL; los cambios "
        "hechos directamente en GHL se ignoran.\n"
        "Solo GHL → Odoo: los cambios en GHL se traen a Odoo; los cambios "
        "hechos directamente en Odoo se ignoran.",
    )

    # --- Credenciales GHL ---
    ghl_api_key = fields.Char(
        string="GHL API Key / Private Integration Token", required=True, groups="base.group_system"
    )
    ghl_location_id = fields.Char(string="GHL Location ID", required=True)

    # --- Mapeo de calendarios (1 a 1) ---
    ghl_calendar_id = fields.Char(
        string="GHL Calendar ID",
        required=True,
        help="ID del calendario específico en GHL que se sincronizará.",
    )
    odoo_user_id = fields.Many2one(
        "res.users",
        string="Usuario/Recurso Odoo asociado",
        required=True,
        help="Los calendar.event de este usuario en Odoo son los que se "
        "sincronizan con el calendario de GHL indicado arriba.",
    )

    # --- Control de sync ---
    last_sync_datetime = fields.Datetime(
        string="Última sincronización exitosa",
        help="Se usa como marca de agua para traer solo cambios incrementales.",
    )
    sync_window_days_past = fields.Integer(
        string="Días hacia atrás a sincronizar", default=7
    )
    sync_window_days_future = fields.Integer(
        string="Días hacia adelante a sincronizar", default=90
    )

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
            "Ya existe una configuración para este GHL Calendar ID.",
        ),
        (
            "odoo_user_unique",
            "unique(odoo_user_id)",
            "Este usuario de Odoo ya está mapeado a otro calendario de GHL.",
        ),
    ]

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

    def action_sync_now(self):
        self.ensure_one()
        self.env["calendar.event"]._ghl_run_sync_for_config(self)
        return True

    @api.model
    def _cron_sync_all(self):
        configs = self.search([("active", "=", True)])
        for config in configs:
            try:
                self.env["calendar.event"]._ghl_run_sync_for_config(config)
            except Exception:
                _logger.exception("Error sincronizando config GHL id=%s", config.id)
                config.sudo().write(
                    {
                        "last_sync_status": "error",
                    }
                )
                # Continúa con las demás configuraciones aunque una falle
                continue
