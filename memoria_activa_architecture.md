# Memoria Activa — Arquitectura de Memoria Mejorada para Hermes Agent

## Diagnóstico del Sistema Actual

### Lo que existe

| Componente | Estado | Fortalezas | Debilidades |
|---|---|---|---|
| MEMORY.md / USER.md | Activo (2200/1375 chars) | Simple, siempre en contexto | Se llena rápido (1230/1375 hoy), plano, no busca |
| Holographic Memory (HRR) | Plugin registrado | HRR 1024-dim, FTS5+Jaccard+HRR, trust scoring, probe/reason/related/contradict | Solo explícito (fact_store), no auto-extrae |
| agent_memory.py | Script separado | Extracción regex básica, user_profile, pattern_log | Extracción superficial, no usa HRR, sin integración real |
| session_context.py | Script separado | Estado estructurado (salud, trading, bujo, sesión) | Guarda en JSON, no se inyecta automáticamente |
| session_search | Herramienta FTS5 | Búsqueda full-text en sesiones pasadas | Requiere llamada explícita del modelo |
| cross_salud_finanzas | Cron 6h | Correlaciona farmacia+citas, glucosa+comida | Solo salud-finanzas, no sesiones cruzadas |
| bujo_cross_insights | Cron diario | Insights de finanzas, salud, bujo, conocimiento | No correlaciona con sesiones de chat |
| bujo_insights.py | Cola de insights | Sistema de prioridad, expiry, mark-shown | Depende de otros scripts para poblarse |
| Context compression | Activo (65%) | Comprime mensajes viejos con modelo auxiliar | No extrae hechos antes de comprimir |

### DBs existentes (4 separadas)

- `bujo.sqlite` (325 entradas): PA, síntomas, tareas, eventos, conocimiento
- `salud.sqlite`: glucosa (LibreLink CGM), exámenes, medicamentos
- `finanzas.db`: transacciones categorizadas
- `agent_memory.db`: hechos, perfil, patrones (no usa HRR)

### Problemas raíz

1. **Extracción manual**: Los hechos solo se guardan si el modelo llama `fact_store` o el usuario pide explícitamente
2. **Contexto plano**: MEMORY.md/USER.md son texto crudo sin búsqueda ni priorización — todo ocupa espacio siempre
3. **Correlación ausente**: Nadie conecta "esto ya pasó hace 3 días en otra sesión"
4. **DBs aisladas**: Consultar salud+bujo+finanzas gasta tokens del modelo principal
5. **Sin ciclo de vida**: Los hechos se acumulan sin limpieza, degradación ni resumen

---

## Arquitectura Propuesta: "Memoria Activa"

### Principios de diseño

- **0 tokens para el modelo principal**: Toda extracción, correlación y limpieza corre en cron jobs o scripts Python puro
- **Vectorización universal**: Todo hecho → HRR phase vector (1024-dim, 8KB). Las búsquedas usan cosine similarity sin API calls
- **Inyección mínima**: Solo se inyecta en contexto lo estrictamente relevante para el turno actual (top-3 hechos, ~150 tokens)
- **Escalabilidad por capas**: Días → semanas → meses, cada capa resume y compacta la anterior

### Diagrama de capas

```
┌─────────────────────────────────────────────────────────────┐
│                    CAPA 1: EXTRACCIÓN                        │
│  Post-session → LLM extrae hechos → HRR vectors + SQLite    │
│  Cron: cada vez que termina una sesión (on_session_end)     │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                 CAPA 2: ALMACENAMIENTO                       │
│  memory_store.db unificado (HRR vectors 1024-dim)            │
│  Banks por: dia, semana, categoria, entidad                  │
│  Facts: content + entities + trust + hrr_vector + session_id │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│               CAPA 3: RECUPERACIÓN PROACTIVA                 │
│  MemoryProvider.prefetch() → HRR similarity → top-3 facts   │
│  Se ejecuta ANTES de cada turno, automático, ~150 tokens    │
│  + Sistema de "novedades" desde bujo_insights               │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              CAPA 4: CORRELACIÓN CRUZADA                     │
│  Cron cada 6h/día: cruza salud↔finanzas↔bujo↔sesiones       │
│  Detecta patrones, escribe a bujo_insights (0 tokens)      │
│  "Tu PA subió 3 días seguidos después de gastos en comida"  │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│             CAPA 5: CICLO DE VIDA (ESCALABILIDAD)            │
│  Diario: decay trust de hechos no recuperados               │
│  Semanal: bundle facts → week vector, limpiar < threshold   │
│  Mensual: archivar hechos viejos, generar resúmenes         │
└─────────────────────────────────────────────────────────────┘
```

---

## Componente por Componente

### CAPA 1: Extracción Automática de Hechos

**Archivo**: `~/.hermes/scripts/memory_extract.py` (nuevo)

**Qué hace**: Al finalizar cada sesión, toma los últimos N mensajes y extrae hechos usando DeepSeek (modelo barato y rápido, mismo provider).

**Prompt de extracción** (~500 tokens input, ~200 tokens output estimado):

```
Eres un extractor de hechos. Del siguiente fragmento de conversación,
extrae hechos relevantes en estas categorías:

SALUD: presión arterial, glucosa, síntomas, medicamentos, dosis, citas médicas
FINANZAS: gastos, ingresos, inversiones, decisiones financieras
BUJO: tareas, eventos, recordatorios, conocimientos
PREFERENCIAS: gustos, prioridades, formas de trabajo
DECISIONES: acuerdos, próximos pasos, compromisos
PATRONES: tendencias, correlaciones, recurrencias

Para cada hecho, devuelve JSON:
{"category": "...", "fact": "...", "entities": [...], "confidence": 0.0-1.0}

Solo hechos NUEVOS, no repitas lo que ya sabes.
```

**Costo estimado**: ~700 tokens por sesión (DeepSeek input ~$0.27/1M tokens = $0.0002 por extracción. 10 sesiones/día = $0.002/día. $0.06/mes)

**Alternativa sin tokens**: Usar el modelo local de compresión (`on_pre_compress`) para extraer hechos de los mensajes a punto de ser comprimidos. Esto ya está implementado como hook del MemoryProvider. Costo: ~$0.01/mes adicional.

**Viabilidad**: ALTA. DeepSeek es barato y ya está configurado. La extracción puede ser asíncrona (no bloquea al usuario).

**Prioridad**: CRÍTICA (P0). Sin esto, todo lo demás depende de entrada manual.

---

### CAPA 2: Almacenamiento Vectorizado Unificado

**Archivos a modificar**:
- `plugins/memory/holographic/store.py` (extender)
- `plugins/memory/holographic/__init__.py` (extender)

**Qué cambia**: El MemoryStore actual ya tiene la infraestructura correcta (facts, entities, hrr_vector, memory_banks). Necesita:

1. **Migrar datos de agent_memory.db → memory_store.db**
   - Los facts existentes en agent_memory se convierten a facts con HRR vectors
   - Las entidades de bujo_knowledge.db se fusionan con las entities del store
   - Un solo source of truth: memory_store.db

2. **Nuevo campo `session_id` en facts** para trazabilidad

3. **Nuevo campo `fact_type`**: `explicit` (puesto por el usuario), `extracted` (por LLM), `correlated` (por cross-domain), `pattern` (detectado)

4. **Nuevo banco `bank:daily:{fecha}`** — todos los hechos de un día bundled en un vector. Permite búsqueda temporal: "qué pasó el martes?"

5. **Nuevo banco `bank:weekly:{semana}`** — rollup semanal (cron)

6. **Compresión de contexto**:
   - La función `prefetch()` del MemoryProvider devuelve top-3 hechos (~150 tokens) en vez de inyectar toda la memoria
   - MEMORY.md pasa a tener solo datos estáticos del perfil (nombre, condiciones base) — no hechos dinámicos

**Costo**: 0 tokens adicionales (SQLite + HRR similarity son CPU puro)

**Viabilidad**: ALTA. La infraestructura ya existe. Solo hay que extender y migrar.

**Prioridad**: ALTA (P1). Es la base sobre la que corre todo.

---

### CAPA 3: Recuperación Proactiva

**Archivo a modificar**: `plugins/memory/holographic/__init__.py` (método `prefetch()`)

**Qué hace**: En cada turno, ANTES de que el modelo vea el mensaje del usuario:

```python
def prefetch(self, query, session_id=""):
    # 1. Obtener query embedding (HRR bag-of-words del mensaje del usuario)
    query_vec = hrr.encode_text(query)

    # 2. Buscar en todos los bancos relevantes:
    #    - bank:daily:{hoy} (lo de hoy)
    #    - bank:daily:{ayer} (lo de ayer)
    #    - cat:salud, cat:finanzas, cat:bujo (por categoria)
    #    - Entidades mencionadas en el mensaje

    # 3. HRR similarity → top-3 facts con score > threshold
    results = self._retriever.search(query, limit=3, min_trust=0.3)

    # 4. Formatear compacto (~50 tokens por hecho)
    return "\n".join(f"- [{r['trust_score']:.1f}] {r['content']}" for r in results)
```

**Además**: El sistema de insights (bujo_insights.py) ya existe. El `prefetch()` también debe inyectar los insights pendientes de prioridad "alta" o "crítico":

```
[System note: The following is recalled memory context...]

## Memoria Relevante
- [0.9] PA promedio esta semana: 138/88 (tendencia al alza)
- [0.7] Gastaste $45,000 en comida ayer, tu glucosa subió a 180

## Novedades
- 🟡 Sin lecturas de glucosa en 12 horas
- 💡 3 transacciones sin categorizar
```

**Costo de tokens**: ~150 tokens inyectados por turno (vs ~500-800 actuales con MEMORY.md completo). Ahorro neto: ~350-650 tokens por turno.

**Viabilidad**: ALTA. `prefetch()` ya está en el contrato del MemoryProvider. Solo hay que implementarlo bien.

**Prioridad**: ALTA (P1). Es lo que hace que la memoria sea proactiva, no reactiva.

---

### CAPA 4: Correlación Cruzada Automática

**Archivos existentes a extender**:
- `~/.hermes/scripts/cross_salud_finanzas.py`
- `~/.hermes/scripts/bujo_cross_insights.py`

**Archivos nuevos**:
- `~/.hermes/scripts/cross_session_patterns.py`
- `~/.hermes/scripts/cross_salud_bujo.py`

**Qué hacen** (todo 0 tokens, SQLite + HRR):

#### cross_session_patterns.py (nuevo, cron cada 3h)

```python
# 1. Buscar en memory_store.db hechos con mismas entidades en distintas sesiones
#    HRR: unbind(entity_vector, fact_a) ≈ unbind(entity_vector, fact_b)
#    Si similitud > threshold → posible patrón

# 2. Ejemplo: "PA alta" aparece en sesión del lunes, miércoles y viernes
#    → Insight: "PA alta recurrente: lun, mie, vie — ¿patrón de estrés laboral?"

# 3. Comparar facts extraídos en sesiones recientes contra facts de hace 7, 14, 30 días
#    Mismas entidades + contenido similar → recurrencia

# 4. Escribir a bujo_insights
```

#### cross_salud_bujo.py (nuevo, cron cada 6h)

```python
# 1. Correlacionar PA/glucosa con tareas del BuJo:
#    - Días con muchas tareas → ¿PA más alta?
#    - Días con eventos sociales → ¿más gasto en comida? ¿glucosa alta?

# 2. Medicamentos registrados en BuJo vs lecturas de PA
#    - ¿PA más baja consistentemente en días con medicación registrada?

# 3. Síntomas en BuJo vs glucosa en salud.sqlite
#    - "fatiga" reportada → correlacionar con hipoglucemias
```

#### cross_salud_finanzas.py (extender)

Ya existe. Añadir:
- Gasto en supermercado vs glucosa siguiente día (lag correlation)
- Gasto en farmacia recurrente → posible alerta de presupuesto médico

#### bujo_cross_insights.py (extender)

Ya existe. Añadir:
- Sesiones de chat como fuente de datos
- Detección de "decisiones no ejecutadas" (se decidió X en chat pero no hay tarea en BuJo)

**Costo**: 0 tokens. Todo SQLite + HRR local.

**Viabilidad**: ALTA. La infraestructura de correlación ya existe (cross_salud_finanzas funciona). Solo hay que extenderla.

**Prioridad**: MEDIA (P2). Agrega mucho valor pero depende de que las capas 1-3 funcionen.

---

### CAPA 5: Ciclo de Vida y Escalabilidad

**Archivos nuevos**:
- `~/.hermes/scripts/memory_lifecycle.py` (cron diario)
- `~/.hermes/scripts/memory_weekly_rollup.py` (cron semanal)
- `~/.hermes/scripts/memory_monthly_archive.py` (cron mensual)

#### Diario: Trust Decay y Limpieza

```python
# 1. Decay: hechos no recuperados en 7+ días → trust -0.05
# 2. Hechos con trust < 0.2 y más de 14 días → considerar eliminar
# 3. Hechos expirados (expires_at < now) → trust = 0, mantener para analytics
# 4. Reconstruir bancos afectados
```

#### Semanal: Rollup

```python
# 1. Agrupar hechos de la semana por categoria
# 2. Crear fact resumen: "Semana 21: PA promedio 135/85, 4 síntomas reportados, ..."
# 3. HRR bundle de los hechos semanales → banco bank:weekly:2026-W21
# 4. Bajar trust de hechos diarios antiguos (ya están en el resumen semanal)
```

#### Mensual: Resumen

```python
# 1. Agrupar hechos del mes
# 2. Crear fact resumen: "Mayo 2026: tendencias de salud, finanzas, patrones, ..."
# 3. Archivar hechos con trust < 0.3 y más de 30 días → tabla facts_archive
# 4. Reconstruir bancos
```

**Métricas de capacidad HRR**:
- 1024 dimensiones, SNR aceptable hasta ~256 hechos por banco
- Si hay 20 hechos/día, cada banco semanal tiene ~140 hechos (SNR ≈ 2.7, bueno)
- Bancos mensuales: ~600 hechos → usar bundle de bundles, no de hechos individuales
- Límite teórico: ~5000 hechos activos antes de necesitar aumentar dim

**Costo**: 0 tokens.

**Viabilidad**: ALTA. HRR soporta esto nativamente (bundle de bundles es válido).

**Prioridad**: MEDIA-BAJA (P3). Importante para escalar pero no bloquea el MVP.

---

## Flujo de Datos Completo

```
┌─────────────────────────────────────────────────────────────────┐
│                       CADA SESIÓN DE CHAT                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. Usuario envía mensaje                                        │
│ 2. MemoryProvider.prefetch(query)                               │
│    ├── HRR encode_text(query) → query_vec                       │
│    ├── similarity contra bank:daily:{hoy}                       │
│    ├── similarity contra cat:salud, cat:finanzas, cat:bujo      │
│    ├── Probe entities mencionadas en query                      │
│    ├── Recuperar bujo_insights pendientes (priority >= alta)    │
│    └── Inyectar top-3 facts + insights como memory-context      │
│ 3. Modelo procesa (ve ~150 tokens de memoria, no 800+)          │
│ 4. Modelo responde                                              │
│ 5. MemoryProvider.sync_turn(user_msg, assistant_msg)            │
│    └── (opcional) guardar hechos explícitos vía fact_store      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    ON_SESSION_END (al cerrar sesión)              │
├─────────────────────────────────────────────────────────────────┤
│ 1. MemoryProvider.on_session_end(messages)                      │
│ 2. Tomar últimos 20-30 mensajes                                 │
│ 3. LLM extractor (DeepSeek, ~700 tokens)                        │
│    └── Extraer hechos nuevos como JSON                          │
│ 4. Para cada hecho:                                             │
│    ├── add_fact(content, category, entities, fact_type="extracted")
│    ├── compute HRR vector                                       │
│    └── rebuild bank: cat:{category}                            │
│ 5. Rebuild bank:daily:{hoy}                                     │
│ 6. También: on_pre_compress extrae de mensajes a comprimir      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      CRON JOBS (background)                      │
├─────────────────────────────────────────────────────────────────┤
│ Cada 1h:                                                        │
│   └── cron_watchdog.py (health checks, ya existe)               │
│                                                                  │
│ Cada 3h:                                                        │
│   └── cross_session_patterns.py (NUEVO)                         │
│       "esto ya pasó hace 3 días en otra sesión"                 │
│                                                                  │
│ Cada 6h:                                                        │
│   ├── cross_salud_finanzas.py (extender)                        │
│   ├── cross_salud_bujo.py (NUEVO)                               │
│   └── bujo_cross_insights.py (extender)                         │
│                                                                  │
│ Diario (8am):                                                    │
│   ├── health_briefing.py (extender)                             │
│   ├── memory_lifecycle.py (NUEVO) — trust decay, cleanup        │
│   ├── bujo_knowledge_auto.py (ya existe)                        │
│   └── memory_extract.py (NUEVO) — extracción de sesiones        │
│       no procesadas                                              │
│                                                                  │
│ Semanal (lunes 8am):                                            │
│   ├── resumen_semanal.py (ya existe)                             │
│   └── memory_weekly_rollup.py (NUEVO)                           │
│                                                                  │
│ Mensual (día 1):                                                │
│   └── memory_monthly_archive.py (NUEVO)                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Costo de Tokens Estimado

| Operación | Frecuencia | Tokens/op | Tokens/mes | Costo/mes (DeepSeek) |
|---|---|---|---|---|
| Extracción post-sesión | ~10/día | 700 | 210,000 | $0.06 |
| prefetch (HRR, local) | ~50/día | 0 | 0 | $0.00 |
| Inyección en contexto | ~50/día | 150 | 225,000 | $0.06 |
| Correlación cruzada | 6/día | 0 | 0 | $0.00 |
| Rollup semanal | 4/mes | 0 | 0 | $0.00 |
| Limpieza mensual | 1/mes | 0 | 0 | $0.00 |
| **TOTAL** | | | ~435,000 | **~$0.12/mes** |

**Ahorro vs sistema actual**:
- Sistema actual: MEMORY.md completo en cada turno (~800 chars ≈ 200 tokens × 50 turnos/día = 300,000 tokens/mes). Pero además el modelo tiene que leerlo en TODOS los turnos aunque no sea relevante.
- Con Memoria Activa: solo se inyectan ~150 tokens relevantes por turno (225,000/mes).
- La diferencia real es cualitativa: la memoria actual es ruido; la nueva es señal.

---

## Viabilidad de Implementación

### Lo que NO hay que construir de cero:

| Ya existe | Estado | Aprovechamiento |
|---|---|---|
| HRR library (1024-dim) | Completo | encode_atom, encode_text, encode_fact, bind, unbind, bundle, similarity, phases_to_bytes |
| MemoryStore (SQLite) | Completo | facts, entities, fact_entities, memory_banks, FTS5, trust scoring |
| FactRetriever (HRR) | Completo | search (FTS5+Jaccard+HRR), probe, related, reason, contradict |
| MemoryProvider ABC | Completo | prefetch(), sync_turn(), on_session_end(), on_pre_compress(), on_memory_write() |
| MemoryManager | Completo | Orquestación, context fencing, streaming scrubber |
| bujo_insights.py | Completo | Cola de insights con prioridad, expiry, mark-shown |
| cross_salud_finanzas.py | Completo | Correlación farmacia↔citas, glucosa↔comida |
| bujo_cross_insights.py | Completo | Insights de finanzas, salud, bujo, conocimiento |
| bujo_knowledge_auto.py | Completo | Extracción de entidades del BuJo |
| session_context.py | Completo | Estado estructurado de sesiones |
| health_briefing.py | Completo | Resumen diario de salud |
| Prompt caching (DeepSeek) | 100% cache rate | El system prompt con prefetch se cachea completo |

### Lo que SÍ hay que construir:

| Componente | Archivo | Complejidad | Tiempo estimado |
|---|---|---|---|
| LLM Extractor | `memory_extract.py` | Media | 2-3 horas |
| Prefetch inteligente | modificar `__init__.py` | Baja | 1-2 horas |
| Migración agent_memory → store | script único | Baja | 1 hora |
| Nuevos campos en store | modificar `store.py` | Baja | 1 hora |
| cross_session_patterns | nuevo script | Media | 2-3 horas |
| cross_salud_bujo | nuevo script | Media | 2-3 horas |
| memory_lifecycle (diario) | nuevo script | Baja | 1-2 horas |
| memory_weekly_rollup | nuevo script | Baja | 1 hora |
| memory_monthly_archive | nuevo script | Baja | 1 hora |
| Extender cross existentes | modificar scripts | Baja | 1-2 horas |
| Tests | tests/ | Media | 3-4 horas |
| **TOTAL** | | | **~16-22 horas** |

---

## Plan de Implementación por Fases

### Fase 0: Preparación (1-2 horas)
- Migrar datos de agent_memory.db → memory_store.db
- Añadir campos `session_id`, `fact_type`, `expires_at` a la tabla facts
- Unificar entidades de bujo_knowledge.db con entities del store
- Script: `migrate_to_unified_store.py`

### Fase 1: Extracción Automática (2-3 horas) — P0
- Implementar `memory_extract.py` con prompt de extracción
- Conectar a `on_session_end()` del MemoryProvider
- También conectar a `on_pre_compress()` para extraer antes de comprimir
- Probar con 5 sesiones reales, ajustar prompt

### Fase 2: Recuperación Proactiva (1-2 horas) — P1
- Implementar `prefetch()` con HRR similarity + bujo_insights
- Limitar a 3 hechos + 2 insights como máximo
- Verificar que el context fencing funciona correctamente
- Medir cache rate (debe seguir cerca del 100%)

### Fase 3: Correlación Cruzada (4-6 horas) — P2
- `cross_session_patterns.py`: patrones entre sesiones
- `cross_salud_bujo.py`: salud vs carga de trabajo/eventos
- Extender `cross_salud_finanzas.py` con lag correlation
- Extender `bujo_cross_insights.py` con decisiones no ejecutadas

### Fase 4: Ciclo de Vida (2-3 horas) — P3
- `memory_lifecycle.py`: trust decay, limpieza diaria
- `memory_weekly_rollup.py`: bundle semanal
- `memory_monthly_archive.py`: archivo mensual

### Fase 5: Testing y Monitoreo (3-4 horas)
- Tests unitarios para cada script nuevo
- Test de integración: sesión completa → extracción → recuperación
- Dashboard de salud del sistema de memoria (facts activos, SNR, cache rate)
- Añadir al cron_watchdog monitoreo del memory store

---

## Resumen de Prioridades

| Prioridad | Componente | Por qué |
|---|---|---|
| **P0 CRÍTICA** | Extracción automática (LLM) | Sin esto, la memoria sigue siendo manual. Es el habilitador de todo. |
| **P1 ALTA** | Almacenamiento unificado | Un solo source of truth. Sin esto hay 3 sistemas de memoria compitiendo. |
| **P1 ALTA** | Recuperación proactiva (prefetch) | Reemplaza MEMORY.md hinchado con ~150 tokens relevantes. Ahorra tokens. |
| **P2 MEDIA** | Correlación cruzada | El "wow factor": descubre patrones que el usuario no ve. Ya hay base. |
| **P3 MEDIA-BAJA** | Ciclo de vida / escalabilidad | Necesario para meses de uso, pero sin las capas 1-3 no hay datos que gestionar. |

---

## Riesgos y Mitigaciones

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| LLM extractor alucina hechos | Baja (DeepSeek es bueno con JSON estructurado) | Confidence score < 0.5 → trust inicial bajo. Verificación cruzada con otras fuentes |
| HRR SNR se degrada con muchos facts | Media | Monitoreo constante de SNR por banco. Si SNR < 2.0, dividir banco o aumentar dim a 2048 |
| Cache rate cae por contenido dinámico en prefetch | Baja | El prefetch va dentro de `<memory-context>`, que el sistema ya fencea. El system prompt base no cambia |
| Migración de datos rompe algo | Baja | Backup de las 3 DBs antes de migrar. Script de rollback |
| Cron jobs compiten por locks de SQLite | Baja | WAL mode ya activado. Usar timeout generoso. Si hay contención, serializar con file lock |

---

## Conclusión

El sistema propuesto "Memoria Activa" transforma la memoria de Hermes de un archivo plano de 2200 caracteres a un sistema vectorizado de 5 capas que:

1. **Extrae** hechos automáticamente de cada conversación (sin intervención del usuario)
2. **Almacena** en vectores HRR de 1024 dimensiones, ocupando 8KB por hecho y permitiendo búsqueda por similitud sin API calls
3. **Recupera** proactivamente solo lo relevante (~150 tokens) en cada turno, en vez de inyectar toda la memoria
4. **Correlaciona** datos entre salud, finanzas y BuJo automáticamente cada pocas horas
5. **Escala** a meses de uso mediante rollups semanales, archivos mensuales y trust decay

Costo total estimado: ~$0.12/mes en tokens. Tiempo de implementación: ~16-22 horas. La mayoría de la infraestructura ya existe y solo necesita ser conectada e integrada.
