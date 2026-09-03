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

    # ------------------------------------------------------------------
    # Interceptar borrado en Odoo -> marcar para borrar en GHL
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
            config_model = self.env["ghl.calendar.config"].sudo()
            configs_by_ghl_calendar = {}
            for ghl_calendar_id, ghl_event_id in pending_ghl_deletes:
                configs_by_ghl_calendar.setdefault(ghl_calendar_id, []).append(ghl_event_id)

            for ghl_calendar_id, ghl_event_ids in configs_by_ghl_calendar.items():
                config = config_model.search(
                    [("ghl_calendar_id", "=", ghl_calendar_id)], limit=1
                )
                if not config:
                    _logger.warning(
                        "No hay config GHL para calendar_id=%s; no se pudo "
                        "propagar el borrado de %s", ghl_calendar_id, ghl_event_ids
                    )
                    continue
                if config.sync_mode not in ("bidirectional", "odoo_to_ghl"):
                    _logger.info(
                        "Config %s en modo '%s': no se propaga el borrado de %s a GHL.",
                        config.id, config.sync_mode, ghl_event_ids,
                    )
                    continue
                client = config._get_client()
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

    # ------------------------------------------------------------------
    # Punto de entrada del cron
    # ------------------------------------------------------------------
    @api.model
    def _ghl_run_sync_for_config(self, config):
        """
        Ejecuta un ciclo completo de sincronización para una config dada,
        respetando su sync_mode:
          - bidirectional: pull GHL->Odoo (GHL gana) y push Odoo->GHL
          - ghl_to_odoo:   solo pull GHL->Odoo (cambios en Odoo se ignoran)
          - odoo_to_ghl:   solo push Odoo->GHL (cambios en GHL se ignoran)
        """
        client = config._get_client()
        now = fields.Datetime.now()
        window_start = now - timedelta(days=config.sync_window_days_past)
        window_end = now + timedelta(days=config.sync_window_days_future)

        log_lines = [f"Modo de sincronización: {config.sync_mode}"]
        touched_event_ids_from_ghl = set()

        try:
            if config.sync_mode in ("bidirectional", "ghl_to_odoo"):
                touched_event_ids_from_ghl = self._ghl_pull_from_ghl(
                    config, client, window_start, window_end, log_lines
                )
            if config.sync_mode in ("bidirectional", "odoo_to_ghl"):
                self._ghl_push_to_ghl(
                    config, client, window_start, window_end,
                    touched_event_ids_from_ghl, log_lines
                )
            config.sudo().write(
                {
                    "last_sync_datetime": now,
                    "last_sync_status": "ok",
                    "last_sync_log": "\n".join(log_lines) or "Sin cambios.",
                }
            )
        except GHLApiError as exc:
            log_lines.append(f"ERROR: {exc}")
            config.sudo().write(
                {
                    "last_sync_status": "error",
                    "last_sync_log": "\n".join(log_lines),
                }
            )
            raise

    # ------------------------------------------------------------------
    # Paso 1: GHL -> Odoo
    # ------------------------------------------------------------------
    @api.model
    def _ghl_pull_from_ghl(self, config, client, window_start, window_end, log_lines):
        start_ms = int(window_start.timestamp() * 1000)
        end_ms = int(window_end.timestamp() * 1000)

        try:
            ghl_events = client.list_events(config.ghl_calendar_id, start_ms, end_ms)
        except GHLApiError as exc:
            log_lines.append(f"No se pudo listar eventos de GHL: {exc}")
            raise

        ghl_event_ids_seen = set()
        touched_odoo_ids = set()

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

            vals = self._ghl_map_ghl_event_to_odoo_vals(config, ghl_event)

            if not odoo_event:
                new_event = self.sudo().with_context(**{GHL_SYNC_CONTEXT_KEY: True}).create(vals)
                touched_odoo_ids.add(new_event.id)
                log_lines.append(f"Creada en Odoo desde GHL: {ghl_id} -> odoo id {new_event.id}")
            else:
                # GHL gana el conflicto: siempre sobreescribimos con la versión de GHL
                # si hubo cambios en GHL desde el último sync exitoso.
                ghl_updated_at = ghl_event.get("dateUpdated") or ghl_event.get("updatedAt")
                if config.last_sync_datetime and ghl_updated_at:
                    ghl_updated_dt = fields.Datetime.from_string(
                        ghl_updated_at[:19].replace("T", " ")
                    )
                    if ghl_updated_dt <= config.last_sync_datetime and odoo_event.ghl_last_sync:
                        # No cambió en GHL desde el último sync; no forzamos nada.
                        continue
                odoo_event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).write(vals)
                touched_odoo_ids.add(odoo_event.id)
                log_lines.append(f"Actualizada en Odoo desde GHL (GHL gana): {ghl_id}")

        # Eventos que en Odoo tienen ghl_event_id de ESTE calendario pero ya
        # no aparecieron en la ventana de GHL -> fueron borrados en GHL.
        orphan_domain = [
            ("ghl_calendar_id", "=", config.ghl_calendar_id),
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

        return touched_odoo_ids

    @api.model
    def _ghl_map_ghl_event_to_odoo_vals(self, config, ghl_event):
        partner = self._ghl_find_or_create_partner_from_contact_id(
            config, ghl_event.get("contactId")
        )
        start_dt = self._ghl_parse_datetime(ghl_event.get("startTime"))
        end_dt = self._ghl_parse_datetime(ghl_event.get("endTime"))
        ghl_status = (ghl_event.get("appointmentStatus") or "confirmed").lower()
        if ghl_status not in ("confirmed", "cancelled", "showed", "noshow", "invalid"):
            ghl_status = "confirmed"

        vals = {
            "name": ghl_event.get("title") or "Cita GHL",
            "start": start_dt,
            "stop": end_dt,
            "user_id": config.odoo_user_id.id,
            "ghl_event_id": ghl_event.get("id") or ghl_event.get("_id"),
            "ghl_calendar_id": config.ghl_calendar_id,
            "ghl_last_sync": fields.Datetime.now(),
            "ghl_appointment_status": ghl_status,
        }
        if partner:
            vals["partner_ids"] = [(6, 0, [partner.id])]
        description = ghl_event.get("notes")
        if description:
            vals["description"] = description
        return vals

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
    def _ghl_find_or_create_partner_from_contact_id(self, config, ghl_contact_id):
        if not ghl_contact_id:
            return False
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("ghl_contact_id", "=", ghl_contact_id)], limit=1)
        if partner:
            return partner
        # No lo tenemos mapeado todavía: lo dejamos sin vincular por ahora.
        # (Podría extenderse para hacer GET /contacts/{id} y crear el partner aquí.)
        return False

    # ------------------------------------------------------------------
    # Paso 2: Odoo -> GHL
    # ------------------------------------------------------------------
    @api.model
    def _ghl_push_to_ghl(self, config, client, window_start, window_end,
                          skip_odoo_ids, log_lines):
        domain = [
            ("user_id", "=", config.odoo_user_id.id),
            ("start", ">=", window_start),
            ("start", "<=", window_end),
        ]
        if config.last_sync_datetime:
            domain.append(("write_date", ">=", config.last_sync_datetime))

        events = self.sudo().search(domain)
        events = events.filtered(lambda e: e.id not in skip_odoo_ids)

        for event in events:
            try:
                if not event.ghl_event_id:
                    self._ghl_create_event_in_ghl(config, client, event, log_lines)
                else:
                    self._ghl_update_event_in_ghl(config, client, event, log_lines)
            except GHLApiError as exc:
                log_lines.append(f"ERROR empujando evento Odoo id={event.id} a GHL: {exc}")
                _logger.exception("Error empujando evento a GHL")

    def _ghl_create_event_in_ghl(self, config, client, event, log_lines):
        contact_id = self._ghl_get_or_create_contact_id(config, client, event)
        if not contact_id:
            log_lines.append(
                f"Saltado evento Odoo id={event.id}: no se pudo resolver contacto GHL "
                f"(agregue un contacto/partner con email o teléfono)."
            )
            return

        ghl_event = client.create_event(
            calendar_id=config.ghl_calendar_id,
            contact_id=contact_id,
            title=event.name or "Cita",
            start_iso=fields.Datetime.to_string(event.start).replace(" ", "T") + "+00:00",
            end_iso=fields.Datetime.to_string(event.stop).replace(" ", "T") + "+00:00",
            notes=event.description or None,
            appointment_status=event.ghl_appointment_status or "confirmed",
        )
        ghl_id = ghl_event.get("id") or ghl_event.get("_id")
        event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).write(
            {
                "ghl_event_id": ghl_id,
                "ghl_calendar_id": config.ghl_calendar_id,
                "ghl_last_sync": fields.Datetime.now(),
            }
        )
        log_lines.append(f"Creada en GHL desde Odoo: odoo id {event.id} -> {ghl_id}")

    def _ghl_update_event_in_ghl(self, config, client, event, log_lines):
        if event.ghl_appointment_status == "cancelled":
            # Cancelar en Odoo = borrar la cita en GHL (y en Odoo, vía unlink normal).
            try:
                client.delete_event(event.ghl_event_id)
                log_lines.append(
                    f"Cita cancelada en Odoo -> borrada en GHL: {event.ghl_event_id}"
                )
            except GHLApiError:
                _logger.exception(
                    "No se pudo borrar en GHL la cita cancelada %s", event.ghl_event_id
                )
                raise
            event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).unlink()
            return

        client.update_event(
            event.ghl_event_id,
            title=event.name or "Cita",
            startTime=fields.Datetime.to_string(event.start).replace(" ", "T") + "+00:00",
            endTime=fields.Datetime.to_string(event.stop).replace(" ", "T") + "+00:00",
            notes=event.description or None,
            appointmentStatus=event.ghl_appointment_status or "confirmed",
        )
        event.with_context(**{GHL_SYNC_CONTEXT_KEY: True}).write(
            {"ghl_last_sync": fields.Datetime.now()}
        )
        log_lines.append(f"Actualizada en GHL desde Odoo: odoo id {event.id} -> {event.ghl_event_id}")

    def _ghl_get_or_create_contact_id(self, config, client, event):
        partner = event.partner_ids.filtered(lambda p: p.ghl_contact_id)[:1]
        if partner:
            return partner.ghl_contact_id

        candidate = event.partner_ids[:1]
        if not candidate:
            return False

        email = candidate.email
        phone = candidate.phone or candidate.mobile
        try:
            found = client.find_contact_by_email_or_phone(email=email, phone=phone)
            if found:
                contact_id = found.get("id") or found.get("_id")
            else:
                created = client.create_contact(name=candidate.name, email=email, phone=phone)
                contact_id = created.get("id") or created.get("_id")
        except GHLApiError:
            _logger.exception("Error resolviendo/creando contacto GHL para partner %s", candidate.id)
            return False

        if contact_id:
            candidate.sudo().write({"ghl_contact_id": contact_id})
        return contact_id
