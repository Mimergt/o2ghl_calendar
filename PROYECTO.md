# PROYECTO.md — ghl_calendar_sync

Historial y contexto de desarrollo del módulo `ghl_calendar_sync`.
Este documento complementa al `README.md` (que cubre instalación y uso)
con el "por qué" de las decisiones tomadas, los bugs ya resueltos, y el
estado real de las cosas — para que cualquier persona (o instancia de
Claude) que retome el proyecto no repita el mismo camino de prueba y
error.

## Qué es esto

Módulo Odoo 18 que sincroniza el Calendario de Odoo con calendarios de
GoHighLevel (GHL) vía polling (cron, no webhooks). Cliente piloto en
producción: FitZone GT.

Repo: https://github.com/Mimergt/o2ghl_calendar.git

## Cómo se llegó al diseño actual

### Decisión: polling en vez de webhooks
Se eligió polling periódico (cron) en vez de webhooks porque el VPS del
desarrollador no expone endpoints públicos fácilmente verificables desde
GHL, y porque simplifica el manejo de reintentos/errores. Trade-off
aceptado: hay un delay (5 min actualmente) para que un borrado hecho
directamente en GHL se refleje en Odoo. En cambio, un borrado hecho en
Odoo SÍ se propaga a GHL de forma instantánea (no espera al cron),
porque se dispara desde el propio `unlink()` de Odoo.

### Decisión: GHL gana en conflictos (modo bidireccional)
Si una cita se edita en ambos sistemas entre una corrida de cron y otra,
la versión de GHL sobreescribe la de Odoo. Fue una decisión explícita
del cliente, no un default técnico.

### Decisión: modos de sincronización seleccionables
Además del bidireccional, existen modos "solo Odoo→GHL" y "solo
GHL→Odoo" para casos donde uno de los dos sistemas debe ser la única
fuente de verdad y el otro solo debe reflejar, sin nunca escribir de
vuelta.

### Decisión: un usuario Odoo = un calendario GHL (modelo original)
El diseño original (`ghl.calendar.config`) mapea 1 a 1: un usuario de
Odoo tiene sus `calendar.event` sincronizados con un calendario
específico de GHL. Esto sigue siendo el modo válido para clientes
simples (como FitZone). Para el cliente con múltiples sedes/vendedores
se está extendiendo esto (ver sección "Trabajo en progreso" abajo) sin
romper este modo original.

## Bugs reales encontrados y resueltos (evitar repetirlos)

1. **Manifest version inválida en Odoo 18**: `"version": "17.0.1.0.0"`
   tumbaba TODO el listado de módulos con `ValueError: Invalid version`.
   Odoo 18 exige formato `18.0.x.y[.z]`. Cualquier módulo custom debe
   usar el prefijo de versión de Odoo correcto.

2. **Campo `numbercall` obsoleto en `ir.cron`**: existía en Odoo <17,
   fue removido. Usarlo en un XML de cron rompe la instalación con
   `ValueError: Invalid field 'numbercall' on model 'ir.cron'`. No
   incluirlo en Odoo 18.

3. **Parseo de fechas ISO con offset negativo**: GHL manda fechas como
   `2026-09-02T15:00:00-06:00` (hora de Guatemala). Un parser ingenuo
   con `.split("+")[0]` rompe con offsets negativos
   (`ValueError: unconverted data remains: -06:00`). Solución: usar
   `datetime.fromisoformat()` + conversión a UTC vía `pytz`, nunca
   parsing manual de substrings.

4. **`unlink()` accediendo a campos después de `super().unlink()`**:
   una vez que `super().unlink()` corre, los recordsets de Odoo quedan
   inválidos — leer `event.ghl_event_id` después de eso silenciosamente
   no funciona como se espera. Solución: extraer todos los datos
   necesarios (ghl_event_id, ghl_calendar_id) a variables de Python
   ANTES de llamar a `super().unlink()`.

5. **Vistas XML (`ir.ui.view`) no se actualizan solo con
   `docker restart`**: el restart del contenedor recarga el código
   Python, pero las vistas están persistidas en la base de datos. Un
   cambio en un archivo `.xml` de vistas requiere ir a
   Apps → "Actualizar" el módulo desde la UI de Odoo, no solo reiniciar
   el contenedor. Cambios solo-Python sí bastan con restart.

## Entorno de desarrollo/pruebas (no es el servidor del cliente final)

- VPS Linode/Akamai, Ubuntu 22.04, Odoo 18 vía Docker (imagen del
  Marketplace de Linode/Akamai).
- Contenedor Odoo: `odoo`. Contenedor Postgres: `db`.
- Addons custom montados del host en:
  `/var/lib/docker/volumes/b662d8793caea30553517edb44bc8edefb87f2f95e3aad30234af9d524909eb2/_data/`
  → dentro del contenedor: `/mnt/extra-addons`
- Ownership esperado de los archivos del módulo: `systemd-network:systemd-journal`
  (mismo patrón que otros módulos custom ya presentes, ej. `metabase_bridge`).
- No hay `docker-compose.yml` — los contenedores se levantaron con
  `docker run` directo (imagen del Marketplace).
- nginx corre como reverse proxy delante de Odoo (puerto 8069 interno).
- Flujo de despliegue de cambios validado y repetible:
  1. Editar archivo localmente (o generarlo).
  2. `scp archivo root@VPS:/tmp/`
  3. En el VPS: copiar de `/tmp` a la ruta del módulo dentro del volumen.
  4. `chown systemd-network:systemd-journal` sobre el archivo copiado.
  5. Validar sintaxis antes de reiniciar:
     `python3 -c "import ast; ast.parse(open('archivo.py').read())"`
  6. `docker restart odoo`
  7. Si el cambio incluyó XML/manifest: ir a Apps → Actualizar el
     módulo en la UI (con modo desarrollador activo, `?debug=1`).
  8. Confirmar arranque limpio: `docker logs odoo --tail 20`.

Nota: el acceso a este VPS es del desarrollador, no necesariamente del
cliente final. Para instalar en el Odoo real de cada cliente, ver la
sección "Instalación" del `README.md` — típicamente el cliente o su
proveedor de hosting deben hacer el despliegue, ya que Odoo no soporta
instalar módulos custom subiendo un zip desde la interfaz de Apps (esa
pantalla solo instala módulos ya presentes en el filesystem del
servidor).

## Estado funcional confirmado (probado en vivo, no solo en teoría)

- Pull GHL→Odoo: creación, actualización, detección de cancelados y de
  "huérfanos" (borrados en GHL que ya no aparecen en la ventana de
  sync) — todo probado con citas reales.
- Push Odoo→GHL: creación, actualización, borrado — todo probado.
- Auto-creación de contacto ligero (`res.partner` con nombre, email,
  teléfono) a partir del `contactId` de GHL vía `GET /contacts/{id}`,
  cuando no existe vínculo previo por `ghl_contact_id`.
- Prefijo `CRM-` en el título de citas creadas en Odoo desde GHL.
- Campo `ghl_appointment_status` (confirmed/cancelled/showed/noshow/
  invalid) sincronizado en ambos sentidos; marcar "Cancelada" en Odoo
  dispara el borrado en GHL (y consecuentemente en Odoo también).
- El usuario Odoo de la config aparece como invitado además del
  contacto GHL.
- Modos de sincronización (bidirectional / odoo_to_ghl / ghl_to_odoo)
  funcionando y respetados tanto en el pull como en el push y en el
  unlink.

## Trabajo en progreso / próxima fase

Un cliente con múltiples marcas/sedes pidió una extensión: reparto
automático de citas entrantes entre varios vendedores por sede, según
turno (mañana/tarde) y round robin. Contexto de negocio, diseño
propuesto, preguntas ya resueltas con el cliente, y plan de fases están
documentados por separado — ver el chat/sesión de Cowork dedicada a
"reparto de citas por sede" (rama de git sugerida:
`feature/sede-routing`, NO mezclar con `main` hasta validar en
producción). Puntos clave ya decididos con el cliente, para no volver a
preguntarlos:

- 3 empresas = 3 `res.company` separadas en el mismo Odoo (a confirmar
  multi-compañía y permisos cruzados en el Odoo real del cliente).
- Cada sede tiene su propio calendario GHL (un agente de IA dentro de
  GHL decide en qué calendario cae cada cita, fuera del alcance de este
  módulo).
- Turno mañana y turno tarde → round robin estricto 1-a-1 (no por carga)
  entre los vendedores activos de ese turno (decisión confirmada: si algún
  día una sede tiene 2+ vendedores en turno mañana, también se reparten
  por round robin, con su propio puntero, igual que tarde — no es un
  vendedor fijo). Por eso `ghl.sede` necesita DOS punteros de round robin
  (`last_assigned_morning_id` y `last_assigned_afternoon_id`), no uno solo.
- Hora de corte mañana/tarde configurable POR SEDE (no global, no por
  empresa).
- Cobertura: si el vendedor de un turno está inactivo, cae al vendedor
  del otro turno de la misma sede. Si todos están inactivos, cae a un
  usuario "fallback" fijo por sede.
- El vendedor asignado debe quedar como organizador (`user_id`) Y como
  invitado (`partner_ids`), junto con el contacto del cliente.
- Modelo de datos implementado (Fase 1, en `feature/sede-routing`):
  `ghl.sede` (name, company_id, ghl_api_key, ghl_location_id,
  ghl_calendar_id, morning_cutoff_time, fallback_user_id,
  last_assigned_morning_id, last_assigned_afternoon_id) +
  `ghl.sede.vendor` (sede_id, user_id, shift, active, sequence) — N
  vendedores por sede, no fijo en 2. `ghl.sede` es independiente de
  `ghl.calendar.config`: tiene sus propias credenciales GHL y su propio
  ciclo de sync — no modifica el pull/push existente que usa FitZone GT.
- Algoritmo de reparto implementado (Fase 2, en `feature/sede-routing`):
  `ghl.sede._assign_vendor_and_shift()` calcula el turno por hora de corte
  (zona horaria fija America/Guatemala), arma candidatos activos de ese
  turno (o del otro turno si no hay, cobertura cruzada), hace round robin
  1-a-1 con puntero propio por turno, y cae a `fallback_user_id` si no hay
  ningún candidato. Se integra en `calendar_event.py` vía
  `_ghl_sede_run_sync_for_sede` / `_ghl_sede_pull_from_ghl` (cron propio,
  `ir_cron_ghl_sede_sync`, cada 5 min) — SOLO pull GHL->Odoo por ahora, sin
  push Odoo->GHL para citas de sede (las citas siempre se originan en GHL).
  El vendedor se asigna una sola vez al crear la cita en Odoo; si en una
  sync posterior la hora de la cita cae en un turno distinto al guardado
  (`ghl_sede_shift` en calendar.event), SÍ se reasigna vendedor corriendo
  de nuevo el round robin del turno nuevo.
- **Limitación resuelta**: se eliminó por completo `ghl.calendar.config`
  (el cliente no lo necesita; ver más abajo "Retiro del modo config") y
  `unlink()` ahora busca la `ghl.sede` por `ghl_calendar_id` para propagar
  el borrado a GHL. Validado en vivo contra la API real de GHL: al borrar
  en Odoo una cita ruteada por sede, `client.delete_event()` sí se
  ejecuta y el evento desaparece de `list_events` (aunque el endpoint
  `get_event` individual de GHL puede devolver datos obsoletos/cacheados
  para un evento recién borrado por un rato — no confiar en ese endpoint
  para verificar borrados, usar `list_events`).

### Retiro del modo `ghl.calendar.config` (post Fase 3)
El cliente de sedes múltiples no usa ni necesita el modo original "1
config = 1 calendario = 1 usuario fijo" (ese modelo seguía existiendo
solo por herencia del diseño original para FitZone GT). Se eliminó
completamente de `feature/sede-routing`: modelo, vistas, cron, accesos,
y todo el código de push/pull bidireccional en `calendar_event.py` que
dependía de él (`_ghl_run_sync_for_config`, `_ghl_pull_from_ghl`,
`_ghl_push_to_ghl`, etc.). Esta rama es exclusiva de este cliente y no
se mergea a `main` (que sigue sirviendo a FitZone GT con el modelo
original intacto), así que el retiro no afecta producción. De paso, el
módulo se renombró de "GoHighLevel Calendar Sync" a "GHL2ODOO" con
ícono/branding propio (Mimer — EPIC.GT) en el manifest y el menú raíz.
- **Cambio de diseño (post Fase 3, validación en vivo con el cliente)**:
  `ghl.sede.vendor.shift` (Selection mañana/tarde/ambos) se reemplazó por
  dos booleanos `works_morning` / `works_afternoon`. El campo `shift`
  único resultaba confuso al configurar el caso real más común (un
  vendedor que cubre mañana Y tarde, otro que cubre solo tarde) — con
  checkboxes independientes se marca directamente lo que aplica a cada
  vendedor, sin tener que pensar en la palabra "Ambos" como un tercer
  valor aparte. `_sede_candidates()` en `ghl.sede` ahora filtra por el
  booleano correspondiente al turno en vez de `shift in (turno, 'both')`.
  Migración (`migrations/18.0.1.3.0/pre-migrate.py`) convierte datos
  existentes antes de que el ORM borre la columna vieja.
- Debe convivir sin romper el modo actual de `ghl.calendar.config`
  (usuario fijo); probablemente como modelo paralelo en vez de
  modificar el existente, para minimizar riesgo sobre producción.
- Riesgos sin validar aún: permisos multi-compañía cruzados, conteo
  exacto de vendedores (~24-26, cifra no confirmada), confirmación de
  que habrá un calendario GHL real por cada una de las ~13 sedes.

## Versionado

Ver changelog detallado en `README.md`. Resumen:
- 1.0.0.0 — sync bidireccional básico.
- 1.1.0.0 — modos de sincronización seleccionables.
- 1.2.0.0 — cron a 5 min, campo de estado de cita.
- 1.3.0.0 — prefijo CRM-, auto-creación de contacto ligero, nota en
  descripción.
- 1.4.0.0 — usuario Odoo como invitado además del contacto GHL.
