# Registro de versiones e identidad de runtime

Este registro identifica qué Hermes está ejecutando el gateway, sin depender de
un único número de versión.

## Fuente de verdad

La identidad se construye en `gateway/runtime_identity.py` y se expone en:

```text
GET /health/detailed
```

El campo `runtime_identity` contiene:

| Campo | Significado |
|---|---|
| `structure_id` | Línea funcional integrada; actualmente `personal-system-memory`. |
| `structure_version` | Versión del contrato de la estructura integrada. |
| `integration_commit` | Commit exacto que identifica el código integrado. |
| `channel` | Canal de publicación; actualmente `integration`. |
| `package_version` | Versión de `hermes-agent` obtenida desde metadata instalada. |
| `source_version` | `hermes_cli.__version__` del código fuente cargado. |

`package_version` y `source_version` pueden diferir: el primero identifica el
paquete instalado y el segundo el checkout fuente. No deben interpretarse como
sustitutos del `integration_commit`.

## Identidad actual

- `structure_id`: `personal-system-memory`
- `structure_version`: `1`
- `integration_commit`: `422be8696f9b7a6247d9b23209f263a50ce96343`
- `channel`: `integration`

Cuando la integración avance, se actualiza `INTEGRATION_COMMIT`; se incrementa
`STRUCTURE_VERSION` solo cuando cambia el contrato o la forma de la estructura.

## Diagnóstico

```bash
curl -s http://127.0.0.1:8643/health/detailed | python3 -m json.tool
```

El endpoint simple `/health` conserva su contrato existente y su campo `version`.
Para identificar la estructura desplegada se debe usar `runtime_identity`.
