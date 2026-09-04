# -*- coding: utf-8 -*-
import logging
from datetime import timedelta

import psycopg2

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
    sync_in_progress = fields.Boolean(
        string="Sincronización en curso", readonly=True, copy=False,
        help="Se marca automáticamente mientras esta sede está sincronizando "
        "(manual o por cron) y se limpia al terminar. Mientras esté marcado, "
        "no se puede lanzar otra sincronización para esta misma sede, para "
        "evitar dos corridas simultáneas peleando por el mismo calendario.",
    )
    sync_started_at = fields.Datetime(
        string="Sincronización iniciada", readonly=True, copy=False,
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

    def action_sync_now(self):
        self.ensure_one()
        self._sync_with_lock(raise_if_locked=True)
        return True

    def action_force_unlock(self):
        """
        Libera manualmente el bloqueo de sincronización de esta sede.
        Uso normal: nunca hace falta (el bloqueo se libera solo al
        terminar la corrida, con éxito o con error). Existe solo para
        el caso raro de que Odoo se haya reiniciado/caído a mitad de una
        sincronización y el bloqueo haya quedado "pegado" en True sin
        que nadie lo esté usando realmente.
        """
        self.ensure_one()
        self.sudo().write({"sync_in_progress": False, "sync_started_at": False})
        return True

    def _sync_with_lock(self, raise_if_locked=False):
        """
        Toma un lock de fila a nivel de base de datos (SELECT ... FOR
        UPDATE NOWAIT) antes de sincronizar esta sede, para que dos
        corridas simultáneas (cron + botón manual, o doble clic) no
        procesen el mismo calendario GHL al mismo tiempo. El lock dura
        hasta que termine la transacción (se libera solo al hacer commit
        al final del cron/de la acción).
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM ghl_sede WHERE id = %s FOR UPDATE NOWAIT",
                    (self.id,),
                )
        except psycopg2.errors.LockNotAvailable:
            # El savepoint ya deshizo (solo) el SELECT fallido; el resto de
            # la transacción (p.ej. otras sedes ya sincronizadas en esta
            # misma corrida del cron) queda intacto.
            message = (
                f"La sede '{self.name}' ya tiene una sincronización en curso "
                "(cron u otro usuario). Espera a que termine antes de lanzar otra."
            )
            if raise_if_locked:
                raise UserError(message)
            _logger.info(message)
            return

        self.sudo().write(
            {"sync_in_progress": True, "sync_started_at": fields.Datetime.now()}
        )
        try:
            self.env["calendar.event"]._ghl_sede_run_sync_for_sede(self)
        finally:
            self.sudo().write({"sync_in_progress": False, "sync_started_at": False})

    @api.model
    def _cron_sync_all(self):
        sedes = self.search([("active", "=", True)])
        for sede in sedes:
            try:
                sede._sync_with_lock(raise_if_locked=False)
            except Exception:
                _logger.exception("Error sincronizando sede GHL id=%s", sede.id)
                sede.sudo().write({"last_sync_status": "error"})
                # Continúa con las demás sedes aunque una falle.
                continue

    # ------------------------------------------------------------------
    # Algoritmo de reparto (Fase 2)
    # ------------------------------------------------------------------
    def _sede_candidates(self, shift):
        """
        Vendedores activos de esta sede que participan del turno indicado
        ('morning' o 'afternoon'), es decir, con works_morning/works_afternoon
        marcado según corresponda. Un vendedor puede marcar ambas casillas y
        así entrar en el round robin de los dos turnos.
        Ordenados por sequence (y luego id) para un orden estable de
        round robin.
        """
        self.ensure_one()
        shift_field = "works_morning" if shift == "morning" else "works_afternoon"
        return self.vendor_ids.filtered(
            lambda v: v.active and v[shift_field]
        ).sorted(key=lambda v: (v.sequence, v.id))

    def _assign_vendor_and_shift(self, start_dt_local):
        """
        Decide qué vendedor de esta sede debe quedar asignado a una cita
        cuyo inicio, en hora local de la sede (America/Guatemala), es
        start_dt_local (un datetime con tzinfo).

        Reglas: turno por hora de corte -> candidatos activos de ese turno
        -> si no hay, cobertura cruzada con el otro turno -> si tampoco hay,
        fallback_user_id. Entre candidatos, round robin estricto 1-a-1 con
        un puntero separado por turno (mañana y tarde reparten igual).

        Devuelve (vendedor res.users o fallback_user_id o registro vacío,
        turno calculado a partir de la hora de la cita -- este último NO
        cambia aunque se haya usado cobertura cruzada, ya que es solo la
        hora la que define el turno "real" de la cita).
        """
        self.ensure_one()
        hour_decimal = start_dt_local.hour + start_dt_local.minute / 60.0
        primary_shift = "morning" if hour_decimal < self.morning_cutoff_time else "afternoon"
        other_shift = "afternoon" if primary_shift == "morning" else "morning"

        candidates = self._sede_candidates(primary_shift)
        rotation_shift = primary_shift
        if not candidates:
            candidates = self._sede_candidates(other_shift)
            rotation_shift = other_shift

        if not candidates:
            return self.fallback_user_id, primary_shift

        pointer_field = (
            "last_assigned_morning_id" if rotation_shift == "morning"
            else "last_assigned_afternoon_id"
        )
        ordered_user_ids = [vendor.user_id.id for vendor in candidates]
        last_assigned = self[pointer_field]
        if last_assigned and last_assigned.id in ordered_user_ids:
            next_index = (ordered_user_ids.index(last_assigned.id) + 1) % len(ordered_user_ids)
        else:
            next_index = 0
        assigned_user = self.env["res.users"].browse(ordered_user_ids[next_index])
        self.sudo().write({pointer_field: assigned_user.id})
        return assigned_user, primary_shift
