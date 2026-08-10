# ADR 0001: conservar la base de memoria legacy como solo lectura

- **Estado:** aceptada
- **Fecha:** 2026-08-10
- **Alcance:** sistema personal de Hermes Enhanced

## Contexto

La instalación activa usa `/home/ubuntu/.hermes-enhanced/memory_store.db` como
fuente de verdad. La auditoría actual muestra 248 facts explícitos y ninguna
fila `extracted`, `fact_governance_audit` o `memory_handoffs`.

Existe además una base legacy separada bajo `~/.hermes/data/` con registros
históricos potencialmente duplicados. No es la base activa del gateway.

Borrar, deduplicar o fusionar esa base ahora agregaría riesgo sin aportar una
capacidad necesaria: la migración segura ya está implementada y la restauración
requiere conservar una copia estable mientras se observa el comportamiento real
de la nueva estructura.

## Decisión

Conservar la base legacy como **respaldo histórico de solo lectura**.

- No se fusionará automáticamente con `memory_store.db`.
- No se eliminarán duplicados todavía.
- No se ejecutará `VACUUM`, `DELETE` ni una migración adicional sobre ella.
- Toda futura migración deberá usar `scripts/memory_migrate.py` en modo dry-run,
  backup verificable, confirmación explícita y rollback probado.
- La base activa seguirá siendo exclusivamente
  `/home/ubuntu/.hermes-enhanced/memory_store.db`.

## Alternativas descartadas

1. **Borrarla ahora:** irreversible y sin necesidad operativa.
2. **Deduplicarla in-place:** puede destruir historial y no ofrece rollback
   suficiente por sí sola.
3. **Fusionarla automáticamente:** mezclaría fuentes de verdad y podría
   reintroducir facts obsoletos o duplicados.

## Consecuencias

- Se conserva una vía de recuperación histórica.
- El sistema mantiene una única base activa clara.
- Queda pendiente una revisión futura si aparece una necesidad concreta de
  recuperar datos legacy.
- El coste es mantener un archivo histórico adicional.

## Verificación

Auditoría read-only realizada el 2026-08-10:

```text
memory_store.db: 248 facts
fact_type=explicit: 248
fact_type=extracted: 0
facts con session_id: 0
fact_governance_audit: 0
memory_handoffs: 0
```

No se modificaron bases, configuración, servicios ni secretos.

## Reversión

La decisión se revierte mediante un nuevo ADR. La base legacy no se toca para
revertir esta decisión.
