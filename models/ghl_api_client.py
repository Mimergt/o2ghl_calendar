# -*- coding: utf-8 -*-
import logging

import requests

_logger = logging.getLogger(__name__)

GHL_BASE_URL = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"  # Ajustar si tu Private Integration usa otra versión
DEFAULT_TIMEOUT = 20


class GHLApiError(Exception):
    """Error genérico al hablar con la API de GHL."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class GHLApiClient:
    """
    Wrapper delgado sobre la API REST de GoHighLevel.
    No depende del ORM de Odoo para poder testearse/reusarse fácilmente
    (p.ej. desde el módulo de Metabase también, si conviene).
    """

    def __init__(self, api_key, location_id, base_url=GHL_BASE_URL, timeout=DEFAULT_TIMEOUT):
        if not api_key:
            raise GHLApiError("Falta el API Key de GHL.")
        if not location_id:
            raise GHLApiError("Falta el Location ID de GHL.")
        self.api_key = api_key
        self.location_id = location_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Infra
    # ------------------------------------------------------------------
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Version": GHL_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method, path, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            _logger.error("GHL API request failed: %s %s -> %s", method, url, exc)
            raise GHLApiError(f"Error de red hablando con GHL: {exc}") from exc

        if resp.status_code >= 400:
            _logger.error(
                "GHL API error %s on %s %s: %s", resp.status_code, method, url, resp.text[:2000]
            )
            raise GHLApiError(
                f"GHL API respondió {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                payload=resp.text,
            )

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise GHLApiError(f"Respuesta de GHL no es JSON válido: {exc}") from exc

    # ------------------------------------------------------------------
    # Calendarios / Citas (Appointments)
    # ------------------------------------------------------------------
    def list_events(self, calendar_id, start_time_ms, end_time_ms):
        """
        Lista eventos/citas de un calendario en un rango de tiempo (epoch ms).
        Endpoint: GET /calendars/events
        """
        params = {
            "locationId": self.location_id,
            "calendarId": calendar_id,
            "startTime": start_time_ms,
            "endTime": end_time_ms,
        }
        data = self._request("GET", "/calendars/events", params=params)
        return data.get("events", data.get("appointments", []))

    def get_event(self, event_id):
        data = self._request("GET", f"/calendars/events/appointments/{event_id}")
        return data.get("appointment", data)

    def create_event(self, calendar_id, contact_id, title, start_iso, end_iso,
                      appointment_status="confirmed", notes=None, address=None):
        body = {
            "calendarId": calendar_id,
            "locationId": self.location_id,
            "contactId": contact_id,
            "title": title,
            "startTime": start_iso,
            "endTime": end_iso,
            "appointmentStatus": appointment_status,
            "ignoreFreeSlotValidation": True,
        }
        if notes:
            body["notes"] = notes
        if address:
            body["address"] = address
        data = self._request("POST", "/calendars/events/appointments", json_body=body)
        return data.get("appointment", data)

    def update_event(self, event_id, **fields):
        """
        fields puede incluir: title, startTime, endTime, appointmentStatus, notes, contactId
        """
        body = {k: v for k, v in fields.items() if v is not None}
        data = self._request(
            "PUT", f"/calendars/events/appointments/{event_id}", json_body=body
        )
        return data.get("appointment", data)

    def delete_event(self, event_id):
        return self._request("DELETE", f"/calendars/events/appointments/{event_id}")

    # ------------------------------------------------------------------
    # Contactos
    # ------------------------------------------------------------------
    def find_contact_by_email_or_phone(self, email=None, phone=None):
        if not email and not phone:
            return None
        params = {"locationId": self.location_id}
        if email:
            params["query"] = email
        elif phone:
            params["query"] = phone
        data = self._request("GET", "/contacts/", params=params)
        contacts = data.get("contacts", [])
        return contacts[0] if contacts else None

    def get_contact(self, contact_id):
        """Obtiene un contacto por su ID. Devuelve None si no existe/hay error."""
        if not contact_id:
            return None
        try:
            data = self._request("GET", f"/contacts/{contact_id}")
        except GHLApiError:
            return None
        return data.get("contact", data)

    def create_contact(self, name, email=None, phone=None):
        body = {
            "locationId": self.location_id,
            "name": name,
        }
        if email:
            body["email"] = email
        if phone:
            body["phone"] = phone
        data = self._request("POST", "/contacts/", json_body=body)
        return data.get("contact", data)
