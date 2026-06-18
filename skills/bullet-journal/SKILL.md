---
name: bullet-journal
description: "Bullet Journal v5: 3 tabs (Agenda, Estado, Más). DayBriefing + EntriesList. Context Engine v3.5: threading narrativo con ciclo de vida, 2548 links, 24 contextos. RelationsPanel v2: search-first explorer con navegación clickeable. Search accent-insensitive via NORM(). Date reference linking, TF-IDF keywords, entity aliases. EstadoPanel psicologico. Task migration 5:30AM con (-Nx), fatiga 5x. Dismiss (checked+dismissed, no delete). Watchdog distingue tc_tenpo vs tc_tenpo_pac, filtra promos BCI."
version: 5.2.0
triggers:
  keywords:
    - bujo
    - bullet journal
    - agenda
    - journal
    - diario
    - entrada
    - tarea
    - evento
    - knowledge
    - briefing
    - contextos
    - relaciones
    - hilos
    - panel hoy
    - glucosa
    - dashboard
    - presion arterial
    - canvas
    - material estudio
    - alastair
    - editar entrada
    - eliminar entrada
    - cerrar dia
    - bottom sheet
    - optimistic update
    - parent-child alastair
  patterns:
    - "\\bbu[ij][oó]\\b"
    - "\\bcollaps\\w*\\b"
    - "\\bglucosa\\b.*\\btimestamp\\b"
    - "\\bpres[io]ón\\b"
    - "\\bcontextos\\b"
    - "\\brelaciones\\b"
    - "\\bhilo[s]?\\b"
    - "\\bpanel hoy\\b"
---
## Arquitectura v5.0 — Simplified + Live Glucose
App simplificada a 3 tabs en sidebar: 📓 Agenda, 💭 Estado (tracker psicológico), 📦 Más. Dashboards legacy dentro de "Más". El editor ProseMirror/TipTap fue eliminado; el usuario escribe interacciones via chat y se registran en BuJo automáticamente.
### Flujo de escritura
- `bujo_tool.sh add|journal|knowledge [--date YYYY-MM-DD] [--type date|task|event|note] "Titulo" "detalle1" ...`  
  → SQLite directo a `bujo_entries` + regenera .md  
  **NO soporta --parent**. Todas las entradas nuevas se crean como root (depth=0, parent_id=NULL).  
- Frontend: TipTap eliminado → vista día con DayBriefing + EntriesList.  
- Chat quick register: detecta prefijos (☐, ○, -) → POST `/api/bujo/add` directo a SQLite, sin LLM.
### DayBriefing card layout
Tarjeta Salud con 3 sub-tarjetas (Glucosa, PA, Saldo) con flex-1 y gradientes. PA overlay modal con 3 inputs (SIS/DIA/PPM), Enter avanza foco, guarda como "PA (AM): 120/80 70ppm". BP por sección (AM/PM) guardado en localStorage con fecha para evitar phantom reads (verifica fecha actual).  
Glucosa: endpoint unificado `/api/glucose/latest` (bridge LibreLink → fallback SQLite). Auto-refresh con visibilitychange + focus + setInterval (15s glucosa, 60s datos completos).
### EntriesList
Lista plana de entradas como tarjetas de colores: ☐ Tareas (ámbar), ○ Eventos (sky), — Notas (violeta). Tap para check/uncheck via POST `/api/bujo/check/{id}`. Items hijos indentados. Checkeado: opacidad reducida + tachado.
### EstadoPanel — tracker psicológico
Registro rápido: ánimo (5 emojis), saturación mental (1-5), energía (1-5), nota opcional. Guarda via POST `/api/tracker/estado`. Historial con correlaciones: estados + glucosa promedio + resumen BuJo. Selector de rango 7/14/30 días. Backend: tabla `tracker_estado` en salud.sqlite.
### MorePanel
Botones compactos en grilla 3 columnas. Contenido se despliega debajo. Toggle al mismo botón cierra panel.
### Task Migration System (5:30 AM)
Script `bujo_migrate_tasks.py`: toma tareas sin checkear del día anterior (fallback últimos 3 días), ignora tareas del sistema (confirmada, dosis, insulina, pastillas). Cuenta ocurrencias en 30 días: fatiga 5+ → no migra, crea nota de revisión en knowledge. Dedup si ya existe hoy. Etiqueta `(-Nx)`. Marca original como migrada.
### Dismiss vs Delete
- Dismiss (right-click/long press → overlay "✕ Descartar"): POST `/api/bujo/dismiss/{entry_id}` setea `checked=1, dismissed=1`. Data permanece en DB. Hijos también marcados.  
- Delete (solo tool): DELETE `/api/bujo/entry/{entry_id}` remueve entry + hijos. No expuesto en UI.
### BuJo Context Engine v3.5 — Threading narrativo
Unificado, reemplaza BuJo Linker y Context Cards. Capas:  
1. Indexación: entidades, keywords TF-IDF, date referencing.  
2. Linking híbrido (~2548 links): entidades compartidas + fecha referenciada + keywords overlap + roles, con decaimiento temporal. Full rebuild cada 6h (`bujo_referencing_v4.py`), incremental cada 1 min (`bujo_reference_live.py`).  
3. Context threading (24 contextos temáticos).  
4. Ciclos de vida: detecta patrones `symptom_action_outcome`, etc.  
API: `GET /api/bujo/contexts`, `GET /api/bujo/context/{id}`, `GET /api/bujo/links-for/{entry_id}`, `GET /api/bujo/sequences`.  
Frontend: "Hilos activos" en DayBriefing con patrón, rango, última entrada.
### RelationsPanel v2 — Search-first explorer
Buscador instantáneo con debounce 300ms, tarjetas por tipo con badge color, enfoque en entrada (GET `/api/bujo/entry/{id}` devuelve entry + links + contexts). Conexiones navegables (click en link navega). Breadcrumb con "← Volver". Sin tabs ni IDs.
### Search accent-insensitive
Función Python `_norm(s)` normaliza (NFKD + ascii + lower). Registrada como `NORM()` en SQLite. La búsqueda `LIKE '%q%'` reemplazada por `WHERE NORM(content) LIKE '%' || NORM(?) || '%'`.
### Date reference linking
"Miércoles 17 de junio" en entrada de junio 1 enlaza con entrada real del 17-jun. Detecta "N de mes", "N-mes", "N/mes" e ISO dates.
### Journal vs Hermes — separación de contenido
Dos tarjetas colapsables en vista día:  
- **📔 Journal (ámbar)**: diario del usuario. Solo sus reflexiones y comentarios personales. NUNCA análisis del agente.  
- **📔 Hermes (azul)**: notas del agente: análisis, contexto, resúmenes, datos de contacto/pago, logs.  
- Eventos concretos (citas, fechas) van como entries root type=event, no bajo Journal ni Hermes.  
- Para crear tarjeta Hermes: `INSERT INTO bujo_entries (date, section, item_type, content, depth, sort_order) VALUES ('YYYY-MM-DD', 'agenda', 'note', '📔 Hermes', 0, ORDER);`  
- NUNCA borrar Hermes ni Journal cards.
### Buenas prácticas de escritura
- NO poner fecha `[DD/MM]` en el título — el BuJo organiza por fecha.  
- Si el usuario dice "ponlo en el Journal", significa bajo la tarjeta existente, no como nueva root.  
- Usar SQLite directo para insert con parent_id (bujo_tool.sh no soporta --parent).  
- NUNCA mezclar notas del agente en el Journal del usuario.  
- Verificar día de la semana con cálculo de fecha antes de escribir.
### Reglas anti-rotura
- Nunca usar `patch` para modificar CSS minificado inline en index.css (se pierde el bloque). Usar `write_file` con contenido completo o agregar al final.  
- Al crear/editar endpoints, usar router sin catch-all conflictivo (ej: hoy_router).  
- Para llamadas API desde frontend, usar prefijo `/hermes/api/` (Caddy /api/* da 404).  
- Los handlers de eventos en useEffect deben ser variables nombradas (no arrow inline) para que removeEventListener funcione.  
- Al actualizar estado parcial (ej: glucosa), usar `setData(prev => prev ? {...prev, glucosa: newGlucosa} : prev)`.  
- Para auto-refresh en PWA, combinar visibilitychange + focus + setInterval.  
- En entradas con hijos, SOLO el icono hace toggle (no el texto). Triángulo ▼ colapsa/expande hijos con stopPropagation.  
- En optimistc updates, revertir en catch.  
- Usar `h-screen` (no `min-h-screen`) para scroll en flexbox.  
- `bujo_migrate_tasks.py`: contar ocurrencias, fatiga 5x → no migrar, sino crear nota de revisión.
### Cron Jobs
- "BuJo Context Engine" cada 6h (no_agent mode).  
- "Migracion tareas BuJo" a las 5:30 AM.  
- Watchdog glucose: guarda current + history (no congelar última lectura).  
- Actualización saldos financieros cada hora (`actualizar_saldos.py`).
### Communication rules
- Siempre que el usuario pregunte sobre sí mismo, salud, finanzas o preferencias: leer MEMORY.md/USER.md inyectados + `fact_store(action='probe', entity='Sebastian Alvarez')` + `fact_store(action='search', query=<tema>)`.  
- Si el usuario dice "no sé" sobre diseño: construir prototipo funcional inmediato en vez de preguntar más en abstracto.  
- Prefiere búsqueda por palabras clave naturales antes que IDs, tags o filtros estructurados.  
- Velocidad de consulta debe ser más rápida que preguntar al agente.
### Tags disponibles
@casa, @salud, @trading, @colegio, @finanzas, @trabajo, @urgencia, @pendiente, @esperando
### Prioridades del usuario
1. Mobile-first: BuJo como pantalla principal, chat como overlay (bottom sheet).  
2. Alastair: tabla horizontal semanal.  
3. Todas las entradas visibles (completadas y pendientes).  
4. Colores por tipo: tarea=ámbar, evento=sky, nota=violeta.  
5. Input rápido sin LLM.  
6. Día mínimo viable: 4 checkboxes (agua, pastillas, comer, salir 1 min).  
7. Cerrar el día: botón al final guarda evento "☑ Cerrar el día".  
8. Chat: botón flotante 💬.
### Familia del usuario
Samanta (8), Catalina (6), Claudia (esposa). Priorizar tareas de hijas sobre personales.
### DeepSeek Pricing (referencia)
deepseek-v4-flash: input $0.14/M (miss), $0.0028/M (hit), output $0.28/M.  
deepseek-v4-pro: input $0.435/M (miss, promo 75%), $0.003625/M (hit), output $0.87/M.
### Blood Pressure Auto-Save
Al detectar patrón 128/89 70ppm en POST `/api/bujo/add`, guarda automáticamente en section journal, parent "Presión arterial", child "128/89 - 70 ppm".
### MedTracker
Tabla `meds_state` en bujo.sqlite. Endpoints: GET/POST `/api/meds/state?date=`. Frontend: toggle → POST + localStorage fallback.
### Enfoque cards clickeables
Tareas son botones. Click → POST `/api/bujo/done/{entry_id+2000}?date=` (modifica markdown Y bujo_entries). Muestra ☐/☑ según estado checked.
