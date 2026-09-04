# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta

import pytz
from odoo import api, fields, models
from odoo.exceptions import UserError

from .ghl_api_client import GHLApiClient, GHLApiError

_logger = logging.getLogger(__name__)

# Contexto que se usa internamente al escribir cambios que vienen DE GHL,
# para que esa misma escritura no se vuelva a empujar hacia GHL en la
# misma corrida del cron (evita loops).
GHL_SYNC_CONTEXT_KEY = "ghl_sync_write"


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    ghl_event_id = fields.Char(
        string="GHL Event ID", copy=False, index=True,
        help="ID de la cita correspondiente en GoHighLevel.",
    )
    ghl_calendar_id = fields.Char(
        string="GHL Calendar ID", copy=False, index=True,
        help="Calendario de GHL al que pertenece esta cita (se setea automáticamente).",
    )
    ghl_last_sync = fields.Datetime(
        string="Última sync con GHL", copy=False, readonly=True,
    )
    ghl_sync_pending_delete = fields.Boolean(
        string="Pendiente de borrar en GHL", copy=False, default=False,
        help="Bandera interna: la cita fue borrada en Odoo y falta propagar el borrado a GHL.",
    )
    ghl_appointment_status = fields.Selection(
        [
            ("confirmed", "Confirmada"),
            ("cancelled", "Cancelada"),
            ("showed", "Asistió"),
            ("noshow", "No asistió"),
            ("invalid", "Inválida"),
        ],
        string="Estado de cita GHL",
        copy=False,
        default="confirmed",
        help="Estado de la cita en GoHighLevel. Se sincroniza en ambos "
        "sentidos según el modo de sincronización configurado. Marcar "
        "como 'Cancelada' borra la cita en ambos sistemas.",
    )

    # --- Campos del reparto por sede (Fase 2, ver models/ghl_sede.py) ---
    ghl_sede_id = fields.Many2one(
        "ghl.sede", string="Sede GHL", copy=False, index=True,
        help="Sede (reparto de citas entre vendedores) a la que pertenece "
        "esta cita.",
    )
    ghl_sede_shift = fields.Selection(
        [("morning", "Mañana"), ("afternoon", "Tarde")],
        string="Turno calculado (sede)", copy=False,
        help="Turno (mañana/tarde) calculado a partir de la hora de la "
        "cita y la hora de corte de la sede, la última vez que se asignó "
        "vendedor. Si en una sincronización posterior la hora de la cita "
        "cae en un turno distinto, se vuelve a correr el reparto.",
    )

    # ------------------------------------------------------------------
    # Interceptar borrado en Odoo -> propagar borrado en GHL (vía la sede)
    # ------------------------------------------------------------------
    def unlink(self):
        # Si el borrado viene DESDE la sync de GHL, no hay nada que propagar.
        if self.env.context.get(GHL_SYNC_CONTEXT_KEY):
            return super().unlink()

        # Extraemos los datos ANTES de borrar: una vez que super().unlink()
        # corre, estos recordsets quedan inválidos y ya no se puede leer
        # ningún campo de ellos.
        pending_ghl_deletes = [
            (event.ghl_calendar_id, event.ghl_event_id)
            for event in self
            if event.ghl_event_id
        ]

        result = super().unlink()

        # Se propaga DESPUÉS de que el unlink de Odoo tuvo éxito, para no
        # dejar la cita borrada en GHL pero viva en Odoo por un error a mitad.
        if pending_ghl_deletes:
            Sede = self.env["ghl.sede"].sudo()
            events_by_ghl_calendar = {}
            for ghl_calendar_id, ghl_event_id in pending_ghl_deletes:
                events_by_ghl_calendar.setdefault(ghl_calendar_id, []).append(ghl_event_id)

            for ghl_calendar_id, ghl_event_ids in events_by_ghl_calendar.items():
                sede = Sede.search([("ghl_calendar_id", "=", ghl_calendar_id)], limit=1)
                if not sede:
                    _logger.warning(
                        "No hay sede GHL para calendar_id=%s; no se pudo "
                        "propagar el borrado de %s", ghl_calendar_id, ghl_event_ids
                    )
                    continue
                client = sede._get_client()
                for ghl_event_id in ghl_event_ids:
                    try:
                        client.delete_event(ghl_event_id)
                        _logger.info("Borrado en GHL: %s", ghl_event_id)
                    except GHLApiError:
                        _logger.exception(
                            "No se pudo borrar en GHL el evento %s (ya borrado en Odoo)",
                            ghl_event_id,
                        )
        return result

    # Prefijo para identificar en Odoo que una cita viene originalmente de GHL.
    GHL_TITLE_PREFIX = "CRM-"

    @staticmethod
    def _ghl_parse_datetime(iso_string):
        """
        Convierte un datetime ISO 8601 (con offset +HH:MM, -HH:MM o 'Z')
        que llega de GHL a un naive datetime en UTC, como espera Odoo.
        """
        if not iso_string:
            return fields.Datetime.now()
        try:
            dt = datetime.fromisoformat(iso_string)
        except ValueError:
            # Fallback defensivo por si llega un formato inesperado
            cleaned = iso_string.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)

        if dt.tzinfo is not None:
            dt = dt.astimezone(pytz.UTC).replace(tzinfo=None)
        return dt

    @api.model
    def _ghl_find_or_create_partner_from_contact_id(self, config, client, ghl_contact_id):
        """
        Busca un res.partner vinculado a este ghl_contact_id. Si no existe,
        intenta traer los datos del contacto desde GHL y crea un partner
        ligero (nombre + email + teléfono) para poder mostrarlo como
        asistente de la cita en Odoo.
        """
        if not ghl_contact_id:
            return False
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("ghl_contact_id", "=", ghl_contact_id)], limit=1)
        if partner:
            return partner

        contact_data = client.get_contact(ghl_contact_id)
        if not contact_data:
            return False

        name = (
            contact_data.get("name")
            or f"{contact_data.get('firstName', '')} {contact_data.get('lastName', '')}".strip()
            or contact_data.get("email")
            or "Contacto GHL"
        )
        vals = {
            "name": name,
            "email": contact_data.get("email") or False,
            "phone": contact_data.get("phone") or False,
            "ghl_contact_id": ghl_contact_id,
        }
        partner = Partner.create(vals)
        return partner

    # ------------------------------------------------------------------
    # Reparto por sede — sincronización GHL -> Odoo (pull). Las citas
    # siempre se originan en GHL vía el agente de IA que decide la sede;
    # no hay push Odoo->GHL para citas de sede todavía.
    # ------------------------------------------------------------------
    GHL_SEDE_TZ = "America/Guatemala"

    @api.model
    def _ghl_sede_run_sync_for_sede(self, sede):
        client = sede._get_client()
        now = fields.Datetime.now()
        window_start = now - timedelta(days=sede.sync_window_days_past)
        window_end = now + timedelta(days=sede.sync_window_days_future)
        log_lines = [f"Sede: {sede.name}"]
        try:
            self._ghl_sede_pull_from_ghl(sede, client, window_start, window_end, log_lines)
            sede.sudo().write(
                {
                    "last_sync_datetime": now,
                    "last_sync_status": "ok",
                    "last_sync_log": "\n".join(log_lines) or "Sin cambios.",
                }
            )
        except GHLApiError as exc:
            log_lines.append(f"ERROR: {exc}")
            sede.sudo().write(
                {
                    "last_sync_status": "error",
                    "last_sync_log": "\n".join(log_lines),
                }
            )
            raise

    @api.model
    def _ghl_sede_pull_from_ghl(self, sede, client, window_start, window_end, log_lines):
        start_ms = int(window_start.timestamp() * 1000)
        end_ms = int(window_end.timestamp() * 1000)

        try:
            ghl_events = client.list_events(sede.ghl_calendar_id, start_ms, end_ms)
        except GHLApiError as exc:
            log_lines.append(f"No se pudo listar eventos de GHL: {exc}")
            raise

        tz = pytz.timezone(self.GHL_SEDE_TZ)
        ghl_event_ids_seen = set()

        for ghl_event in ghl_events:
            ghl_id = ghl_event.get("id") or ghl_event.get("_id")
            if not ghl_id:
                continue
            ghl_event_ids_seen.add(ghl_id)

            status = (ghl_event.get("appointmentStatus") or "").lower()
            odoo_event = self.sudo().search([("ghl_event_id", "=", ghl_id)], limit=1)

            if status in ("cancelled", "canceled", "invalid"):
                if odoo_event:
                    odoo_event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).unlink()
                    log_lines.append(f"Borrada en Odoo (cancelada en GHL): {ghl_id}")
                continue

            start_dt_utc = self._ghl_parse_datetime(ghl_event.get("startTime"))
            start_dt_local = pytz.UTC.localize(start_dt_utc).astimezone(tz)
            hour_decimal = start_dt_local.hour + start_dt_local.minute / 60.0
            current_shift = (
                "morning" if hour_decimal < sede.morning_cutoff_time else "afternoon"
            )

            if not odoo_event:
                vendor, _shift = sede._assign_vendor_and_shift(start_dt_local)
                vals = self._ghl_sede_map_ghl_event_to_odoo_vals(
                    sede, client, ghl_event, vendor, current_shift
                )
                new_event = self.sudo().with_context(**{GHL_SYNC_CONTEXT_KEY: True}).create(vals)
                vendor_name = vendor.name if vendor else "(sin fallback_user_id configurado)"
                log_lines.append(
                    f"Creada en Odoo desde GHL (sede {sede.name}, turno {current_shift}, "
                    f"vendedor {vendor_name}): {ghl_id} -> odoo id {new_event.id}"
                )
                continue

            vals = self._ghl_sede_map_ghl_event_to_odoo_vals(
                sede, client, ghl_event, vendor=None, shift=None, existing_event=odoo_event
            )

            if odoo_event.ghl_sede_shift and odoo_event.ghl_sede_shift != current_shift:
                vendor, _shift = sede._assign_vendor_and_shift(start_dt_local)
                vendor_name = vendor.name if vendor else "(sin fallback_user_id configurado)"
                partner_ids = set(odoo_event.partner_ids.ids)
                if odoo_event.user_id and odoo_event.user_id.partner_id:
                    partner_ids.discard(odoo_event.user_id.partner_id.id)
                if vendor and vendor.partner_id:
                    partner_ids.add(vendor.partner_id.id)
                vals["ghl_sede_shift"] = current_shift
                vals["user_id"] = vendor.id if vendor else False
                vals["partner_ids"] = [(6, 0, list(partner_ids))]
                log_lines.append(
                    f"Turno cambió ({odoo_event.ghl_sede_shift} -> {current_shift}) en {ghl_id}; "
                    f"reasignado a {vendor_name}"
                )

            odoo_event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).write(vals)

        orphan_domain = [
            ("ghl_sede_id", "=", sede.id),
            ("ghl_event_id", "!=", False),
            ("ghl_event_id", "not in", list(ghl_event_ids_seen)),
            ("start", ">=", window_start),
            ("start", "<=", window_end),
        ]
        orphans = self.sudo().search(orphan_domain)
        if orphans:
            log_lines.append(
                f"Borrando en Odoo {len(orphans)} evento(s) ya no presentes en GHL: "
                f"{orphans.mapped('ghl_event_id')}"
            )
            orphans.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).unlink()

    @api.model
    def _ghl_sede_map_ghl_event_to_odoo_vals(
        self, sede, client, ghl_event, vendor, shift, existing_event=None
    ):
        """
        Arma los vals de calendar.event para una cita ruteada por sede.

        Si existing_event es None (cita nueva), incluye vendedor/turno/
        asistentes. Si existing_event está presente (cita ya sincronizada),
        arma solo los campos "comunes" (fecha, estado, notas, contacto)
        SIN tocar vendedor/turno — eso lo decide el llamador según si el
        turno calculado cambió respecto al que ya tenía guardado.
        """
        contact_id = ghl_event.get("contactId")
        partner = self._ghl_find_or_create_partner_from_contact_id(sede, client, contact_id)
        start_dt = self._ghl_parse_datetime(ghl_event.get("startTime"))
        end_dt = self._ghl_parse_datetime(ghl_event.get("endTime"))
        ghl_status = (ghl_event.get("appointmentStatus") or "confirmed").lower()
        if ghl_status not in ("confirmed", "cancelled", "showed", "noshow", "invalid"):
            ghl_status = "confirmed"

        title = ghl_event.get("title") or "Cita GHL"
        if not title.startswith(self.GHL_TITLE_PREFIX):
            title = f"{self.GHL_TITLE_PREFIX}{title}"

        vals = {
            "name": title,
            "start": start_dt,
            "stop": end_dt,
            "ghl_event_id": ghl_event.get("id") or ghl_event.get("_id"),
            "ghl_calendar_id": sede.ghl_calendar_id,
            "ghl_sede_id": sede.id,
            "ghl_last_sync": fields.Datetime.now(),
            "ghl_appointment_status": ghl_status,
        }

        description_parts = []
        original_notes = ghl_event.get("notes")
        if original_notes:
            description_parts.append(original_notes)
        if partner:
            contact_lines = [f"Contacto GHL: {partner.name}"]
            if partner.email:
                contact_lines.append(f"Correo: {partner.email}")
            if partner.phone:
                contact_lines.append(f"Teléfono: {partner.phone}")
            description_parts.append("\n".join(contact_lines))
        if description_parts:
            vals["description"] = "\n\n".join(description_parts)

        if existing_event is None:
            vals["ghl_sede_shift"] = shift
            vals["user_id"] = vendor.id if vendor else False
            attendee_partner_ids = set()
            if vendor and vendor.partner_id:
                attendee_partner_ids.add(vendor.partner_id.id)
            if partner:
                attendee_partner_ids.add(partner.id)
            if attendee_partner_ids:
                vals["partner_ids"] = [(6, 0, list(attendee_partner_ids))]

        return vals
