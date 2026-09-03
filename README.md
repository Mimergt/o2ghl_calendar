# GHL Calendar Sync

Módulo de Odoo 18 para sincronizar el Calendario de Odoo con un calendario
específico de GoHighLevel (GHL) de forma bidireccional (o en un solo
sentido, según configuración).

## Características

- Sincronización vía polling (cron cada 10 min, configurable).
- Mapeo 1 a 1: un usuario de Odoo <-> un calendario de GHL. Puedes crear
  tantas configuraciones como usuarios/calendarios necesites.
- 4 modos de sincronización por configuración:
  - **Bidireccional**: Odoo ↔ GHL, incluye borrados en ambos sentidos.
    En conflicto (editado en ambos lados), **GHL gana**.
  - **Solo Odoo → GHL**: Odoo es la fuente de verdad. Cambios hechos
    directamente en GHL se ignoran.
  - **Solo GHL → Odoo**: GHL es la fuente de verdad. Cambios hechos
    directamente en Odoo se ignoran.
- Auto-vinculación/creación de contactos: `res.partner` ↔ contacto GHL,
  por email o teléfono.
- Prevención de loops de sincronización (contexto interno al escribir
  cambios que vienen de la sync).

## Requisitos

- Odoo 18.
- Módulos base: `calendar`, `contacts`, `mail` (dependencias estándar).
- Una Private Integration de GHL con scopes:
  - `calendars.readonly`
  - `calendars/events.readonly`
  - `calendars/events.write`
  - `contacts.readonly`
  - `contacts.write`

## Instalación

1. Copia la carpeta `ghl_calendar_sync/` a tu carpeta de addons custom de
   Odoo (la misma donde tienes otros módulos custom).
2. Si Odoo corre en Docker, copia el módulo dentro del volumen mapeado a
   `/mnt/extra-addons` (revisa con
   `docker inspect <contenedor> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'`).
3. Reinicia el contenedor/servicio de Odoo:
   ```bash
   docker restart odoo
   ```
4. En la interfaz web de Odoo:
   - Activa el modo desarrollador (`?debug=1` en la URL, o desde
     Ajustes → General → Activar modo desarrollador).
   - Ve a **Apps** → botón ⋮ → **Actualizar lista de aplicaciones**.
   - Busca "GoHighLevel Calendar Sync" (quita el filtro "Apps" si no
     aparece) e instálalo.

## Configuración

1. Ve a **GHL Calendar Sync → Configuración** → Nuevo.
2. Completa:
   - **GHL API Key / Private Integration Token**
   - **GHL Location ID**
   - **GHL Calendar ID** (el calendario específico a sincronizar)
   - **Usuario/Recurso Odoo asociado** (las citas de este usuario en Odoo
     son las que se sincronizan con ese calendario de GHL)
   - **Modo de sincronización**
3. Dale **"Probar conexión"** para validar credenciales.
4. Dale **"Sincronizar ahora"** para el primer ciclo manual, o espera al
   cron automático (cada 10 min).

## Notas técnicas

- El cron corre cada 10 minutos por defecto (ajustable en Ajustes →
  Técnico → Acciones programadas → "GHL Calendar Sync: sincronizar
  citas").
- La ventana de sincronización (días hacia atrás/adelante) es
  configurable por cada registro de configuración.
- Los campos `ghl_event_id`, `ghl_calendar_id` y `ghl_last_sync` en
  `calendar.event`, y `ghl_contact_id` en `res.partner`, son internos
  del módulo — no editarlos manualmente salvo que sepas lo que haces.

## Changelog

- **1.1.0.0**: Se agrega selección de modo de sincronización
  (bidireccional / solo Odoo→GHL / solo GHL→Odoo) por configuración.
- **1.0.0.0**: Versión inicial. Sync bidireccional con GHL ganando en
  conflictos, propagación de borrados, auto-vinculación de contactos.
