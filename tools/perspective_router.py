"""Deterministic perspective router.

It selects a minimal panel from the task wording; it does not call models. Luna
uses the returned route to decide which native perspective tools to invoke.
"""
from __future__ import annotations

from typing import Any

from tools.registry import registry, tool_error, tool_result


_ROUTE_RULES = (
    ("architecture", ("arquitectura", "diseño del sistema", "refactor", "trade-off", "escalabilidad"), ("gemini", "claude")),
    ("safety", ("seguridad", "security", "vulnerab", "secreto", "credencial"), ("claude", "gemini")),
    ("health", ("salud", "glucosa", "presión", "medicación", "síntoma", "médic"), ("claude",)),
    ("technical", ("código", "code", "bug", "error", "debug", "test", "api", "servidor", "configur"), ("gemini",)),
    ("optimization", ("optimiza", "rendimiento", "algoritmo", "más rápido", "costo", "cuota"), ("deepseek",)),
    ("creative", ("diseña", "diseño", "imagen", "personaje", "creativo", "estilo", "ilustr"), ("gemini",)),
)


def _classify(text: str) -> tuple[str, tuple[str, ...], str]:
    normalized = text.casefold()
    for category, keywords, perspectives in _ROUTE_RULES:
        matched = [word for word in keywords if word in normalized]
        if matched:
            return category, perspectives, matched[0]
    return "simple", (), "sin indicador de complejidad"


def _handle_perspective_router(args: dict[str, Any], **_kwargs: Any) -> str:
    task = str(args.get("task", "")).strip()
    if not task:
        return tool_error("task es obligatorio", code="invalid_task")
    if len(task) > 20_000:
        return tool_error("task supera el límite de 20000 caracteres", code="task_too_large")

    category, selected, trigger = _classify(task)
    risk = str(args.get("risk", "normal")).strip().casefold() or "normal"
    if risk in {"alto", "high", "crítico", "critical"} and "claude" not in selected:
        selected = (*selected, "claude")
    if category == "simple":
        recommendation = "Luna responde directamente; no convoques perspectivas."
    else:
        recommendation = "Convoca las perspectivas indicadas en paralelo e independientes; Luna sintetiza después."

    return tool_result(
        success=True,
        route=category,
        perspectives=list(dict.fromkeys(selected)),
        coordinator="luna",
        trigger=trigger,
        risk=risk,
        max_external_perspectives=min(2, len(selected)),
        recommendation=recommendation,
        output_contract=["conclusion", "supuestos", "riesgos", "confianza", "evidencia_necesaria"],
    )


PERSPECTIVE_ROUTER_SCHEMA = {
    "name": "perspective_router",
    "description": (
        "Clasifica una tarea y recomienda el panel mínimo de perspectivas. "
        "No llama modelos ni ejecuta acciones; úsalo antes de convocar Claude, Gemini o DeepSeek."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Descripción de la tarea o decisión."},
            "risk": {"type": "string", "description": "normal, alto/high o crítico/critical.", "default": "normal"},
        },
        "required": ["task"],
        "additionalProperties": False,
    },
}

registry.register(
    name="perspective_router",
    toolset="perspectives",
    schema=PERSPECTIVE_ROUTER_SCHEMA,
    handler=_handle_perspective_router,
    emoji="🧩",
)
